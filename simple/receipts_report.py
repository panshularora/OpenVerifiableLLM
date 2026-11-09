"""Aggregate published registrations and check receipts for one release.

For every registered key it lists checks j = 1..m in order with their outcome, and marks a check
MISSING when a later check was posted or its deadline passed without an outcome. A missing or
abandoned check is listed with the segment it drew, since anyone can recompute that segment.
Coverage counts only completed MATCH outcomes from keys you mark as independent (a human judgment;
keys can't show who is behind them), and the same segment from one key counts once.

  python simple/receipts_report.py --issue AOSSIE-Org/OpenVerifiableLLM#NNN --anchor RUN/anchor.json \\
        [--independent KEY_FP ...] [--deadline-hours 72]
  python simple/receipts_report.py --posts mirror.json --anchor RUN/anchor.json   # offline
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beacon  # noqa: E402
import receipt  # noqa: E402


def issue_comment_urls(issue):
    """All comment URLs of OWNER/REPO#N, oldest first, from the GitHub REST API."""
    repo, num = issue.rsplit("#", 1)
    urls, page = [], 1
    while True:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues/{num}/comments?per_page=100&page={page}",
                                     headers={"Accept": "application/vnd.github+json"})
        if os.environ.get("GITHUB_TOKEN"):
            req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
        with urllib.request.urlopen(req, timeout=30) as f:
            batch = json.loads(f.read())
        urls += [c["html_url"] for c in batch]
        if len(batch) < 100:
            return urls
        page += 1


def detect(n, draws):
    """P(one forged segment of n is drawn at least once) after `draws` independent uniform draws."""
    return 1 - ((n - 1) / n) ** draws


def report(urls, posts, release, anchor_time, independent=(), now=None, deadline=72 * 3600, signature=None):
    now = time.time() if now is None else now
    regs, outcomes, rejected = {}, {}, []
    for url in urls:
        try:
            env, _ = posts.get(url)
        except receipt.Invalid as e:
            rejected.append({"url": url, "reason": str(e)})
            continue
        try:
            if env.get("payloadType") == receipt.REGISTRATION_TYPE:
                reg = receipt.Registration(url, posts, release, anchor_time)
                if reg.key_fp in regs:  # one registration per key and release; a second one can't reset R0
                    raise receipt.Invalid("key already registered for this release")
                regs[reg.key_fp] = reg
            else:
                o = receipt.verify(url, posts, release, anchor_time)
                slot = (o["_key_fp"], o["challenge"]["j"])
                if slot in outcomes:
                    raise receipt.Invalid(f"second outcome for check {slot[1]}; only the first counts")
                outcomes[slot] = o
        except receipt.Invalid as e:
            rejected.append({"url": url, "reason": str(e)})
    keys, covered, by_segment = [], {}, {}
    for fp, reg in regs.items():
        seq, last = [], max([j for (k, j) in outcomes if k == fp], default=0)
        for j in range(1, reg.m + 1):
            o = outcomes.get((fp, j))
            r = reg.round(j)
            if o:
                segs = [x["segment"][0] for x in o["results"]]
                kinds = sorted({x["outcome"] for x in o["results"]})
                status = kinds[0] if len(kinds) == 1 else "+".join(kinds)
                for x in o["results"]:
                    by_segment.setdefault(x["segment"][0], set()).add(x["outcome"])
                    if x["outcome"] == "MATCH" and fp in independent:
                        covered.setdefault(x["segment"][0], set()).add(fp)
            else:
                late = now > beacon.round_time(r) + deadline
                status = "MISSING" if j < last or late else "PENDING"
                segs = None
                if signature and now >= beacon.round_time(r):
                    try:
                        segs = reg.segments(j, signature(r))
                    except (receipt.Invalid, OSError):
                        segs = None
            seq.append({"j": j, "round": r, "status": status, "segments": segs, "url": o["_url"] if o else None})
        keys.append({"key_fp": fp, "handle_claim": reg.body["handle_claim"], "independent": fp in independent,
                     "registered": receipt.iso(reg.time), "R0": reg.R0, "k": reg.k, "m": reg.m, "checks": seq})
    n = release["chain_entries"] - 1
    draws = sum(1 for (fp, _), o in outcomes.items() if fp in independent and fp in regs
                for x in o["results"] if x["outcome"] == "MATCH")
    return {"release": release, "segments": n, "keys": keys, "rejected": rejected,
            "independent_match_segments": sorted(covered),
            "disputes": sorted(s for s, kinds in by_segment.items() if {"MATCH", "MISMATCH"} <= kinds),
            "independent_match_draws": draws, "p_detect_one_forged": round(detect(n, draws), 4)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--issue", help="OWNER/REPO#N where registrations and outcomes are posted")
    src.add_argument("--posts", help="JSON mirror of comments (URL -> API comment JSON)")
    p.add_argument("--anchor", required=True)
    p.add_argument("--anchor-time", type=int)
    p.add_argument("--rounds", help="JSON {round: signature}, to recompute the segments of missing checks offline")
    p.add_argument("--independent", nargs="*", default=[], help="key fingerprints judged independent of the project")
    p.add_argument("--deadline-hours", type=float, default=72)
    p.add_argument("--now", type=float)
    a = p.parse_args(argv)
    try:
        release, anchor_time = receipt.release_of(a.anchor, a.anchor_time)
    except receipt.Invalid as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    mirror = json.loads(Path(a.posts).read_text()) if a.posts else None
    posts = receipt.Posts(mirror)
    rounds = json.loads(Path(a.rounds).read_text()) if a.rounds else None
    sig = (lambda r: rounds[str(r)]) if rounds else (lambda r: beacon.fetch_round(r)["signature"])
    urls = list(mirror) if mirror is not None else issue_comment_urls(a.issue)
    out = report(urls, posts, release, anchor_time, set(a.independent), a.now, a.deadline_hours * 3600, sig)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
