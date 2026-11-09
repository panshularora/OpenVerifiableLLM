"""Aggregate published registrations and check receipts for one release.

Registrations are the merged pull requests that added checkers/<key_fp>.json; outcomes are the
comments of the checks issue. For every registered key it lists checks j = 1..m in order with their outcome, and marks a check
MISSING when a later check was posted or its deadline passed without an outcome. A missing or
abandoned check is listed with the segment it drew, since anyone can recompute that segment.
Coverage counts only completed MATCH outcomes from keys you mark as independent (a human judgment;
keys can't show who is behind them), and the same segment from one key counts once.

  python simple/receipts_report.py --issue AOSSIE-Org/OpenVerifiableLLM#NNN --anchor RUN/anchor.json \\
        [--independent KEY_FP ...] [--deadline-hours 72]
  python simple/receipts_report.py --posts mirror.json --anchor RUN/anchor.json   # offline; a mirror
        # is only as trustworthy as whoever wrote it
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beacon  # noqa: E402
import receipt  # noqa: E402


def _pages(url):
    page = 1
    while True:
        batch = receipt.Posts._get(f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}")
        yield from batch
        if len(batch) < 100:
            return
        page += 1


def issue_comment_urls(issue):
    """All comment URLs of OWNER/REPO#N, oldest first, from the GitHub REST API."""
    repo, num = issue.rsplit("#", 1)
    return [c["html_url"] for c in _pages(f"https://api.github.com/repos/{repo}/issues/{num}/comments")]


def registration_urls(repo):
    """Every pull request merged into main that touched checkers/, found through the commit history,
    so a registration file deleted later still shows up."""
    api, urls = f"https://api.github.com/repos/{repo}", []
    for c in _pages(f"{api}/commits?sha={receipt.REGISTRY_BRANCH}&path=checkers"):
        for pr in receipt.Posts._get(f"{api}/commits/{c['sha']}/pulls"):
            if pr.get("merged_at") and pr["html_url"] not in urls:
                urls.append(pr["html_url"])
    return urls


def detect(n, draws):
    """P(one forged segment of n is drawn at least once) after `draws` independent uniform draws."""
    return 1 - ((n - 1) / n) ** draws


def report(urls, posts, release, anchor_time, independent=(), now=None, deadline=72 * 3600, signature=None):
    now = time.time() if now is None else now
    regs, outcomes, rejected, found = {}, {}, [], []
    for url in urls:
        if receipt.PULL_URL.match(url):
            try:
                found.append(receipt.Registration(url, posts, release, anchor_time))
            except receipt.Invalid as e:
                rejected.append({"url": url, "reason": str(e)})
    for reg in sorted(found, key=lambda r: r.time):
        if reg.key_fp in regs:  # one registration per key and release; a later one can't reset R0
            rejected.append({"url": reg.url, "reason": "key already registered for this release"})
        else:
            regs[reg.key_fp] = reg
    for url in urls:
        if receipt.PULL_URL.match(url):
            continue
        try:
            o = receipt.verify(url, posts, release, anchor_time)
            if regs.get(o["_key_fp"]) is None or regs[o["_key_fp"]].url != o["_registration"]:
                raise receipt.Invalid("outcome of a registration that doesn't count")
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
    return {"source": "mirror" if posts.mirror is not None else "github", "release": release, "segments": n, "keys": keys, "rejected": rejected,
            "independent_match_segments": sorted(covered),
            "disputes": sorted(s for s, kinds in by_segment.items() if {"MATCH", "MISMATCH"} <= kinds),
            "independent_match_draws": draws, "p_detect_one_forged": round(detect(n, draws), 4)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--issue", help="OWNER/REPO#N where outcomes are posted; registrations are read from "
                     "the pull requests merged into OWNER/REPO")
    src.add_argument("--posts", help="JSON mirror (URL -> API JSON) of registrations and outcomes; only as "
                     "trustworthy as whoever wrote it")
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
    if mirror is not None:
        print("WARNING: --posts: reading a mirror file, not GitHub", file=sys.stderr)
    registry = a.issue.rsplit("#", 1)[0] if a.issue else receipt.REGISTRY
    posts = receipt.Posts(mirror, registry=registry)
    rounds = json.loads(Path(a.rounds).read_text()) if a.rounds else None
    sig = (lambda r: rounds[str(r)]) if rounds else (lambda r: beacon.fetch_round(r)["signature"])
    urls = list(mirror) if mirror is not None else registration_urls(registry) + issue_comment_urls(a.issue)
    out = report(urls, posts, release, anchor_time, set(a.independent), a.now, a.deadline_hours * 3600, sig)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
