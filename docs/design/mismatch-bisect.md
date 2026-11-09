# Mismatch bisect over per-step traces (EXPERIMENTAL)

Status: experimental, for discussion. Built on the checker campaign branch
([checker-campaign.md](checker-campaign.md)) and the check receipts branch
([check-receipts.md](check-receipts.md)). Nothing in the repository writes per-step traces yet.

## The problem

A segment check ends in one bit. `ovl.py verify --segment k` replays boundary `k` to `k + 1` and
compares the state digest at the end: MATCH or MISMATCH. A MISMATCH on the reference GPU can mean two
different things:

- the published segment isn't what the recorded data and code produce (a real mismatch), or
- the replay itself isn't reproducible on that machine (run-to-run nondeterminism, a driver or
  library difference that the environment record didn't catch).

The single digest can't tell these apart. Replaying the same segment twice on matching hardware and
comparing per-step digests can give a first hint.

## What `simple/bisect_trace.py` does

It compares two traces of the same segment and prints one of:

- `IDENTICAL`: every step has the same digests.
- `DIVERGED at step s`: the first step where they differ, the last equal step, how many steps differ,
  and whether the divergence persists to the end or the digests become equal again (and from which
  step). If the optional batch hashes differ, it says the replays read different data, which is not
  numeric nondeterminism.
- `FAIL`: a trace is malformed (bad JSON, missing or extra fields, a non-hex digest, steps not
  consecutive, more than one segment, fields changing between rows) or the two are misaligned
  (different segment, first step, length, or fields). It never compares a partial overlap.

```bash
python simple/bisect_trace.py A.jsonl B.jsonl [--json]   # exit 0 IDENTICAL, 2 DIVERGED, 1 FAIL
```

## Trace format v0

There is no existing per-step trace output to reuse: `chain.jsonl` has digests only at boundaries.
The format borrows names from what `ovl.py` already computes:

```json
{"segment": 7, "step": 70001, "hash": "<64 hex>", "batches": "<64 hex>"}
```

- one line per training step, in order, consecutive `step` values (the global step after the update);
- `segment` is `k` for segment `k -> k+1`;
- `hash` is meant to be `ovl.state_digest(model, opt)` after the step, the same digest the chain uses
  at boundaries;
- `batches` is optional: the running batch hash that `ovl.py` already keeps in the control record.

Hashing the full state every step costs time; how often to write a row (every step, every 100 steps)
is an open question, and the tool only needs both traces to use the same steps.

## How to read the result

- Two replays on the same machine that are IDENTICAL, while both disagree with the published
  boundary, point away from local nondeterminism and toward a real mismatch (or a different
  environment from the one recorded).
- Two replays on the same machine that diverge from each other show the machine isn't reproducing the
  run; that check says nothing about the release either way and should be reported as such, not as
  MISMATCH.
- A divergence in `batches` means the data order differs, which points at the data or the code, not
  the GPU.
- A full state digest that becomes equal again after differing is unexpected; the tool flags it and
  the likely cause is how the trace was written.

## What it can't do

- It cannot localise a forgery beyond a segment. A published release has digests only at boundaries,
  so a per-step trace of a replay can only be compared with another replay, not with the original run.
  Comparing with the original would need the trainer to publish per-step digests, which this lays
  groundwork for but doesn't propose.
- It doesn't explain a divergence, only where it starts.
- It is about matching hardware. Other GPU architectures are known not to reproduce the bits; this
  tool doesn't study that.

## Tests and AI use

`tests/simple/test_bisect_trace.py` uses synthetic traces only: identical, diverged at the first,
middle and last step, persisting and recovering divergence, data divergence, and every malformed or
misaligned case above. No real replay trace has been produced or compared. Written with AI coding
assistance and reviewed against those tests.
