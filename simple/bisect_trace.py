"""EXPERIMENTAL: compare two per-step replay traces of one segment and report where they first differ.

A segment check today ends in one bit: the state digest at the next boundary matches or it doesn't.
When it doesn't, a per-step trace says more: whether the two replays split at the first step or late,
and whether they stay apart. That helps tell run-to-run nondeterminism on matching hardware (a late
split in the numbers, same data) from a real mismatch (a split at the first step, or in the data). It
can't localise a forgery beyond the segment, and nothing writes these traces yet: ovl.py records
digests only at boundaries. See docs/design/mismatch-bisect.md.

Trace format (JSON Lines, one object per training step, in order):

  {"segment": 7, "step": 70001, "hash": "<64 hex>"}                       # required fields
  {"segment": 7, "step": 70001, "hash": "<64 hex>", "batches": "<64 hex>"}  # optional

`segment` is the boundary k of segment k -> k+1, `step` the global step after the update, `hash` a
digest of the training state after that step (meant to be ovl.state_digest(model, opt)), and the
optional `batches` the running batch hash that ovl.py already keeps in the control record.

  python simple/bisect_trace.py A.jsonl B.jsonl [--json]

Exit status: 0 IDENTICAL, 2 DIVERGED, 1 FAIL (a trace is malformed, or the two are misaligned).
"""
import argparse
import json
from pathlib import Path
import re
import sys

HEX = re.compile(r"^[0-9a-f]{64}$")
REQUIRED, OPTIONAL = {"segment", "step", "hash"}, {"batches"}


class Invalid(Exception):
    """A trace, or a pair of traces, that can't be compared."""


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def parse(text, name="trace"):
    """Rows of one trace, checked: one segment, consecutive steps, the same fields on every row."""
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise Invalid(f"{name}: line {n} is blank")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise Invalid(f"{name}: line {n} is not valid JSON ({e.msg})") from e
        if not isinstance(row, dict) or not REQUIRED <= set(row) <= REQUIRED | OPTIONAL:
            raise Invalid(f"{name}: line {n} must have exactly segment, step, hash and optionally batches")
        if not (_int(row["segment"]) and row["segment"] >= 0 and _int(row["step"]) and row["step"] >= 1):
            raise Invalid(f"{name}: line {n}: segment and step must be non-negative integers, step at least 1")
        for f in ("hash", "batches"):
            if f in row and not (isinstance(row[f], str) and HEX.match(row[f])):
                raise Invalid(f"{name}: line {n}: {f} must be 64 lowercase hex")
        if rows:
            prev = rows[-1]
            if row["segment"] != prev["segment"]:
                raise Invalid(f"{name}: line {n}: segment {row['segment']} after {prev['segment']}; one segment per trace")
            if row["step"] != prev["step"] + 1:
                raise Invalid(f"{name}: line {n}: step {row['step']} after {prev['step']}; steps must be consecutive")
            if set(row) != set(prev):
                raise Invalid(f"{name}: line {n}: fields differ from the line before")
        rows.append(row)
    if not rows:
        raise Invalid(f"{name}: empty trace")
    return rows


def compare(a, b):
    """Verdict for two parsed traces of the same segment and steps."""
    if a[0]["segment"] != b[0]["segment"]:
        raise Invalid(f"misaligned: segment {a[0]['segment']} vs {b[0]['segment']}")
    if (a[0]["step"], len(a)) != (b[0]["step"], len(b)):
        raise Invalid(f"misaligned: steps {a[0]['step']}..{a[-1]['step']} vs {b[0]['step']}..{b[-1]['step']}")
    if set(a[0]) != set(b[0]):
        raise Invalid("misaligned: one trace has batches and the other doesn't")
    fields = [f for f in ("batches", "hash") if f in a[0]]
    differ = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if any(x[f] != y[f] for f in fields)]
    out = {"segment": a[0]["segment"], "steps": [a[0]["step"], a[-1]["step"]], "compared": len(a)}
    if not differ:
        return out | {"verdict": "IDENTICAL"}
    i = differ[0]
    first = [f for f in fields if a[i][f] != b[i][f]]
    later = [x for x in range(i + 1, len(a)) if x not in set(differ)]
    out |= {"verdict": "DIVERGED", "first_step": a[i]["step"], "first_fields": first,
            "last_equal_step": a[i - 1]["step"] if i else None, "differing_steps": len(differ),
            "persists": not later, "recovers_at": a[later[0]]["step"] if later else None,
            "ends_equal": a[-1]["hash"] == b[-1]["hash"]}
    return out


def describe(out):
    if out["verdict"] == "IDENTICAL":
        return f"IDENTICAL: segment {out['segment']}, steps {out['steps'][0]}..{out['steps'][1]} ({out['compared']} steps)"
    lines = [f"DIVERGED at step {out['first_step']} of segment {out['segment']} "
             f"(steps {out['steps'][0]}..{out['steps'][1]}), in {' and '.join(out['first_fields'])}",
             "  last equal step: " + (str(out["last_equal_step"]) if out["last_equal_step"] is not None
                                      else "none, the traces differ from the first step"),
             f"  {out['differing_steps']} of {out['compared']} steps differ; "
             + ("the divergence persists to the end" if out["persists"] else
                f"equal again at step {out['recovers_at']}" + ("" if out["ends_equal"] else ", but not at the end"))]
    if "batches" in out["first_fields"]:
        lines.append("  the batch hashes differ: the replays read different data, which is not numeric nondeterminism")
    if not out["persists"]:
        lines.append("  a full state digest that becomes equal again is unexpected; check how the traces were written")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--json", action="store_true")
    x = p.parse_args(argv)
    try:
        traces = []
        for path in (x.a, x.b):
            try:
                text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                raise Invalid(f"{path}: can't read ({e})") from e
            traces.append(parse(text, path))
        out = compare(*traces)
    except Invalid as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1) if x.json else describe(out))
    return 0 if out["verdict"] == "IDENTICAL" else 2


if __name__ == "__main__":
    sys.exit(main())
