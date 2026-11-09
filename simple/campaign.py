"""Summarise a checker campaign from a receipts report: coverage per class, disputes, follow-ups, a bound.

Input is the JSON printed by receipts_report.py and a classes file, a JSON object that maps each
key_fp to "project", "contributor" or "independent". The classes are a human judgment (a key can't
show who is behind it). Only completed MATCH checks of keys classed "independent" count toward the
headline bound; project and contributor keys never do, whatever the report's own --independent flags
say, and a release author key (--author) classed "independent" is rejected. Keys in the report but not
in the classes file are listed as unclassified and count toward no bound.

The bound: if F of the N segments were forged, the chance that every counted check missed all of them
is prod_i C(N-F, k_i) / C(N, k_i), where check i drew k_i segments uniformly without replacement.
F_max is the largest F for which that chance is still above alpha, so "at most F_max of N segments
forged at 1-alpha confidence". It assumes the forger fixed the forged segments before the draws
existed (registration after the anchor), and that abandoned, missing or incomplete checks are unrelated
to which segment was drawn. A checker sees its segment before running, so a drop-out can hide a forged
segment; the headline names how many independent checks dropped out. Any MISMATCH makes the campaign DISPUTED; CLEAN only means no MISMATCH was reported.

  python simple/campaign.py REPORT.json --classes classes.json [--author KEY_FP ...] [--alpha 0.05] [--json]

Exit status: 0 CLEAN, 2 DISPUTED, 1 FAIL (input rejected). See docs/design/checker-campaign.md.
"""
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import sys

CLASSES = ("project", "contributor", "independent")
OUTCOMES = ("MATCH", "MISMATCH", "WRONG_STACK", "ERROR")  # as in receipt.py
SINGLE = set(OUTCOMES) | {"ABANDONED", "MISSING", "PENDING"}
REPORT_KEYS = {"source", "release", "segments", "keys", "rejected", "independent_match_segments", "disputes",
               "independent_match_draws", "p_detect_one_forged"}
KEY_KEYS = {"key_fp", "handle_claim", "independent", "registered", "R0", "k", "m", "checks"}
CHECK_KEYS = {"j", "round", "status", "segments", "url"}
FP = re.compile(r"^[0-9a-f]{64}$")


class Invalid(Exception):
    """A report or classes file that must be rejected."""


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise Invalid(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def load_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
    except OSError as e:
        raise Invalid(f"{what}: can't read {path} ({e.strerror})") from e
    except json.JSONDecodeError as e:
        raise Invalid(f"{what}: not valid JSON ({e.msg})") from e
    except Invalid as e:
        raise Invalid(f"{what}: {e}") from e


# ---------- the bound ----------

def miss(n, f, ks):
    """Exact chance that checks drawing ks[i] of n segments each, without replacement, all miss f forged ones."""
    p = Fraction(1)
    for k in ks:
        p *= Fraction(math.comb(n - f, k), math.comb(n, k))
    return p


def f_max(n, ks, alpha):
    """Largest F in 0..n with miss(n, F, ks) > alpha; n when there are no checks."""
    a = Fraction(alpha)
    return max(f for f in range(n + 1) if miss(n, f, ks) > a)  # F = 0 always qualifies: miss is 1


def continuous(ks, alpha):
    """The with-replacement approximation: forged fraction x with (1 - x)^(sum k) = alpha."""
    d = sum(ks)
    return 1 - alpha ** (1 / d) if d else 1.0


def p_detect(n, ks, f=1):
    """Chance the checks catch at least one of f forged segments."""
    return float(1 - miss(n, f, ks))


def bound(n, ks, alpha):
    fm = f_max(n, ks, alpha)
    return {"checks": len(ks), "draws": sum(ks), "f_max": fm, "f_max_fraction": round(fm / n, 4),
            "continuous_fraction": round(continuous(ks, alpha), 4), "p_detect_one_forged": round(p_detect(n, ks), 4)}


# ---------- inputs ----------

def check_status(s):
    if not isinstance(s, str):
        return False
    parts = s.split("+")
    if len(parts) == 1:
        return s in SINGLE
    return parts == sorted(set(parts)) and set(parts) <= set(OUTCOMES)


def validate_report(rep):
    """Fail closed on anything that isn't exactly the shape receipts_report.py prints."""
    if not isinstance(rep, dict) or set(rep) != REPORT_KEYS:
        got = sorted(rep) if isinstance(rep, dict) else type(rep).__name__
        raise Invalid(f"unknown report schema (fields {got}); expected the output of receipts_report.py")
    n = rep["segments"]
    if not _int(n) or n < 1:
        raise Invalid("report: segments must be a positive integer")
    if rep["source"] not in ("github", "mirror") or not isinstance(rep["release"], dict):
        raise Invalid("report: bad source or release")
    if not isinstance(rep["keys"], list) or not isinstance(rep["rejected"], list):
        raise Invalid("report: keys and rejected must be lists")
    if not isinstance(rep["disputes"], list) or not all(_int(s) and 0 <= s < n for s in rep["disputes"]):
        raise Invalid("report: disputes must be segment numbers")
    seen = set()
    for key in rep["keys"]:
        if not isinstance(key, dict) or set(key) != KEY_KEYS:
            raise Invalid("report: unknown key entry schema")
        fp = key["key_fp"]
        if not isinstance(fp, str) or not FP.match(fp):
            raise Invalid(f"report: bad key_fp {fp!r}")
        if fp in seen:
            raise Invalid(f"report: key {fp[:12]} listed twice")
        seen.add(fp)
        k, m, checks = key["k"], key["m"], key["checks"]
        if not (_int(k) and 1 <= k <= n and _int(m) and m >= 1 and isinstance(checks, list) and len(checks) == m):
            raise Invalid(f"report: key {fp[:12]}: bad k, m or checks")
        for j, c in enumerate(checks, 1):
            if not isinstance(c, dict) or set(c) != CHECK_KEYS or c["j"] != j or not check_status(c["status"]):
                raise Invalid(f"report: key {fp[:12]} check {j}: unknown check schema or status")
            segs = c["segments"]
            if segs is not None and not (isinstance(segs, list) and len(segs) == k and
                                         all(_int(s) and 0 <= s < n for s in segs) and len(set(segs)) == k):
                raise Invalid(f"report: key {fp[:12]} check {j}: segments must be {k} distinct segments of {n}")
            if c["status"] not in ("MISSING", "PENDING") and segs is None:
                raise Invalid(f"report: key {fp[:12]} check {j}: an outcome without its segments")
    return rep


def validate_classes(cls, authors=()):
    if not isinstance(cls, dict):
        raise Invalid("classes: must be a JSON object mapping key_fp to a class")
    for fp, c in cls.items():
        if not FP.match(fp):
            raise Invalid(f"classes: bad key_fp {fp!r} (64 lowercase hex)")
        if c not in CLASSES:
            raise Invalid(f"classes: {fp[:12]} has class {c!r}; use one of {', '.join(CLASSES)}")
    for fp in authors:
        if not isinstance(fp, str) or not FP.match(fp):
            raise Invalid(f"author: bad key_fp {fp!r} (64 lowercase hex)")
        if cls.get(fp) == "independent":
            raise Invalid(f"classes: release author key {fp[:12]} is marked independent; "
                          "the author's own checks are contributor checks")
    return cls


# ---------- the campaign ----------

def kind(status):
    parts = set(status.split("+"))
    if "MISMATCH" in parts:
        return "mismatch"
    if status == "MATCH":
        return "match"
    if status in ("ABANDONED", "MISSING", "PENDING"):
        return status.lower()
    return "incomplete"  # WRONG_STACK, ERROR, or a mix without MISMATCH: no segment counts


def campaign(rep, classes, alpha=0.05, authors=()):
    if not (isinstance(alpha, float | int) and 0 < alpha < 1):
        raise Invalid(f"alpha must be strictly between 0 and 1, not {alpha!r}")
    validate_report(rep)
    validate_classes(classes, authors)
    n = rep["segments"]
    rows = {c: {"keys": 0, "checks_declared": 0, "match_checks": 0, "match_draws": 0, "covered": set(),
                "mismatch": 0, "abandoned": 0, "missing": 0, "incomplete": 0, "pending": 0}
            for c in (*CLASSES, "unclassified")}
    ks = {"independent": [], "classified": []}
    disputes, follow_up, unclassified, notes = [], [], [], []
    for key in rep["keys"]:
        fp, cls = key["key_fp"], classes.get(key["key_fp"], "unclassified")
        if cls == "unclassified":
            unclassified.append(fp)
        if key["independent"] and cls != "independent":
            notes.append(f"report marks {fp[:12]} independent; the classes file says {cls}, which is used")
        row = rows[cls]
        row["keys"] += 1
        row["checks_declared"] += key["m"]
        for c in key["checks"]:
            what = kind(c["status"])
            entry = {"key_fp": fp, "class": cls, "j": c["j"], "round": c["round"], "status": c["status"],
                     "segments": c["segments"], "url": c["url"]}
            if what == "match":
                row["match_checks"] += 1
                row["match_draws"] += key["k"]
                row["covered"].update(c["segments"])
                if cls == "independent":
                    ks["independent"].append(key["k"])
                if cls != "unclassified":
                    ks["classified"].append(key["k"])
            elif what == "mismatch":
                row["mismatch"] += 1
                disputes.append(entry)
            else:
                row[what] += 1
                if what != "pending":
                    follow_up.append(entry)
    overall = {f: sum(r[f] for r in rows.values()) for f in rows["project"] if f != "covered"}
    overall["covered"] = set().union(*(r["covered"] for r in rows.values()))
    table = [{"class": c, **r, "covered": len(r["covered"])} for c, r in rows.items()]
    table.append({"class": "overall", **overall, "covered": len(overall["covered"])})
    head = bound(n, ks["independent"], alpha)
    dropped = [f for f in follow_up if f["class"] == "independent"]
    head["dropped"] = len(dropped)  # abandoned, missing or incomplete independent checks
    head["dropped_by_status"] = {s: sum(1 for f in dropped if kind(f["status"]) == s)
                                 for s in ("abandoned", "missing", "incomplete")}
    caveat = (f"assumes the {len(dropped)} abandoned, missing or incomplete independent "
              f"check{'' if len(dropped) == 1 else 's'} ({', '.join(f'{v} {s}' for s, v in head['dropped_by_status'].items())}) "
              "were unrelated to their drawn segments; a checker sees its segment before running, so a drop-out "
              "can hide a forged one" if dropped else "no independent check was abandoned, missing or incomplete")
    return {"status": "DISPUTED" if disputes or rep["disputes"] else "CLEAN", "source": rep["source"],
            "segments": n, "alpha": alpha, "table": table, "disputes": disputes,
            "disputed_segments": rep["disputes"], "follow_up": follow_up, "unclassified": unclassified,
            "rejected_posts": len(rep["rejected"]), "notes": notes,
            "bound_independent": head, "bound_all_classes": bound(n, ks["classified"], alpha),
            "headline": f"at most {head['f_max']} of {n} segments ({100 * head['f_max'] / n:.1f}%) forged at "
                        f"{100 * (1 - alpha):g}% confidence, from {head['checks']} independent MATCH checks; {caveat}"}


def render(out):
    cols = ("class", "keys", "checks_declared", "match_checks", "match_draws", "covered", "mismatch",
            "abandoned", "missing", "incomplete", "pending")
    w = [max(len(c), *(len(str(r[c])) for r in out["table"])) for c in cols]
    lines = [f"campaign status: {out['status']}" + ("  (CLEAN means no MISMATCH was reported)"
                                                    if out["status"] == "CLEAN" else "")]
    if out["source"] == "mirror":
        lines.append("WARNING: the report was built from a --posts mirror, not GitHub")
    if out["disputes"] or out["disputed_segments"]:
        lines.append("MISMATCH disputes:")
        lines += [f"  {d['key_fp'][:12]} ({d['class']}) check {d['j']}: segments {d['segments']} {d['status']} {d['url']}"
                  for d in out["disputes"]]
        lines.append(f"  segments with both MATCH and MISMATCH: {out['disputed_segments']}")
    lines += ["", "  ".join(c.ljust(x) for c, x in zip(cols, w, strict=True))]
    lines += ["  ".join(str(r[c]).ljust(x) for c, x in zip(cols, w, strict=True)) for r in out["table"]]
    b, a = out["bound_independent"], out["bound_all_classes"]
    lines += ["", f"bound (independent checks only): {out['headline']}",
              f"  continuous approximation {100 * b['continuous_fraction']:.1f}%; "
              f"P(catch 1 forged segment) {100 * b['p_detect_one_forged']:.1f}% from {b['draws']} draws",
              f"all classes, NOT an independence claim (includes project and contributor checks): at most "
              f"{a['f_max']} of {out['segments']} ({100 * a['f_max_fraction']:.1f}%) from {a['checks']} MATCH checks",
              "the all-classes figure also assumes every abandoned, missing or incomplete check is unrelated "
              "to which segment was drawn"]
    if out["follow_up"]:
        lines.append("follow-up targets (abandoned, missing or incomplete checks):")
        lines += [f"  {f['key_fp'][:12]} ({f['class']}) check {f['j']} round {f['round']}: {f['status']}, segments "
                  f"{f['segments'] if f['segments'] is not None else 'not recomputed (rerun the report with --rounds)'}"
                  for f in out["follow_up"]]
    if out["unclassified"]:
        lines.append("unclassified keys (counted in no bound): " + ", ".join(fp[:12] for fp in out["unclassified"]))
    if out["rejected_posts"]:
        lines.append(f"{out['rejected_posts']} rejected posts in the report (not counted; see its 'rejected' list)")
    lines += [f"note: {x}" for x in out["notes"]]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("report", help="JSON printed by receipts_report.py")
    p.add_argument("--classes", required=True, help="JSON object: key_fp -> project | contributor | independent")
    p.add_argument("--author", nargs="*", default=[], help="release author key fingerprints; never independent")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        out = campaign(load_json(a.report, "report"), load_json(a.classes, "classes"), a.alpha, a.author)
    except Invalid as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1) if a.json else render(out))
    return 2 if out["status"] == "DISPUTED" else 0


if __name__ == "__main__":
    sys.exit(main())
