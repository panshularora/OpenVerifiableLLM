# Checker campaign summary (on top of check receipts)

Status: an experimental branch for discussion, built on the check receipts branch
([check-receipts.md](check-receipts.md)). Not an agreed design.

## What it is for

`receipts_report.py` lists every registered key and every check with its outcome. A campaign (a
period where several people check one release) needs one more step: a table that says how much of the
release has been checked, and by whom, plus a number that a reader can quote without overstating it.
`simple/campaign.py` does that step and nothing else. It reads only the report's JSON and a classes
file; it doesn't fetch anything, run any check, or sign anything.

```bash
python simple/receipts_report.py --issue AOSSIE-Org/OpenVerifiableLLM#NNN --anchor RUN/anchor.json > report.json
python simple/campaign.py report.json --classes classes.json --author AUTHOR_KEY_FP [--alpha 0.05] [--json]
```

How checkers do their checks is unchanged. The CPU check of hashes, timestamps and weights is
`simple/check_release.sh` from #282 once that is merged; segment replays are `verify_segment.sh` on
the reference GPU, signed with `receipt.py`. The campaign kit depends on neither in code or tests.

## The classes file

A JSON object mapping each `key_fp` to `project`, `contributor` or `independent`:

```json
{"<64 hex key_fp>": "independent", "<64 hex key_fp>": "contributor"}
```

- The class is a human judgment. A key can't show who is behind it, so whoever publishes a campaign
  table should say who wrote the classes file and why each key is in its class.
- Only `independent` keys count toward the headline bound. `project` and `contributor` keys never do,
  even if the report was made with them in `--independent`; the classes file wins, and the
  disagreement is printed as a note.
- The release author's own keys are contributor checks. Panshul's own runs count as contributor
  checks, not independent ones. `--author KEY_FP` names the author's keys; a classes file that marks
  one of them `independent` is rejected.
- Keys in the report but not in the classes file are listed as unclassified and count toward no
  bound, including the all-classes figure. They are not an error, so a partly classified campaign
  still prints a table, but the list says which keys still need a decision.
- The file fails closed: not an object, a duplicate key, a key that isn't 64 lowercase hex, or an
  unknown class is a FAIL.

## What it prints

1. The campaign status. Any check with MISMATCH in its outcome (or any segment the report lists as
   disputed) makes it `DISPUTED`, and the disputes are printed first with key, class, check number,
   segments and URL. Otherwise it is `CLEAN`, which only means no MISMATCH was reported.
2. A table per class and overall: keys, checks declared (`m`), completed MATCH checks, MATCH draws,
   distinct segments covered by MATCH, mismatches, abandoned, missing, incomplete (WRONG_STACK, ERROR,
   or a mix of outcomes without MISMATCH) and pending.
3. The bound from independent checks only, with the number of independent checks that were
   abandoned, missing or incomplete on the same line, and an all-classes figure labelled as not an
   independence claim.
4. Follow-up targets: every abandoned, missing or incomplete check, with the segments it drew. Anyone
   can recheck those segments. If the report was made without beacon rounds, missing checks have no
   segments yet and the table says so.
5. Unclassified keys, the number of posts the report rejected, and a warning if the report came from
   a `--posts` mirror.

Exit status: 0 for CLEAN, 2 for DISPUTED, 1 when an input is rejected.

## The bound

Check `i` drew `k_i` of the `N` segments uniformly without replacement, from a beacon round that did
not exist when the run was published. If `F` segments were forged, the chance that every counted
check missed all of them is

```text
miss(F) = prod_i C(N - F, k_i) / C(N, k_i)
```

`F_max` is the largest `F` with `miss(F) > alpha` (default 0.05), so the table says "at most `F_max`
of `N` segments (x%) forged at 95% confidence". Only checks whose outcome is exactly MATCH count. The
arithmetic is exact (fractions, not floats). It also prints the with-replacement approximation
`1 - alpha^(1/sum k_i)` and the chance of catching one forged segment, `1 - miss(1)`.

The headline line itself names the independent checks that dropped out, for example:

```text
at most 9 of 37 segments (24.3%) forged at 95% confidence, from 10 independent MATCH checks; assumes
the 2 abandoned, missing or incomplete independent checks (1 abandoned, 1 missing, 0 incomplete) were
unrelated to their drawn segments; a checker sees its segment before running, so a drop-out can hide a
forged one
```

This caveat sits on the headline on purpose. A checker learns which segment was drawn before running
it, so a checker who finds (or knows) that segment is forged can simply not report it, and the bound
above never sees that draw. The bound is only as good as the assumption that drop-outs are unrelated
to the drawn segment; the count says how much rests on it, and each dropped check is in the follow-up
list with its segments so anyone can recheck them. The JSON has the same count as
`bound_independent.dropped` and `bound_independent.dropped_by_status`. With no drop-outs the headline
says so.

For the released run, `N = 37`, with single-segment checks:

| independent MATCH draws | F_max at 95% | continuous approximation | P(catch 1 forged) |
|---|---|---|---|
| 1 | 35 (94.6%) | 95.0% | 2.7% |
| 10 | 9 (24.3%) | 25.9% | 24.0% |
| 30 | 3 (8.1%) | 9.5% | 56.0% |
| 50 | 2 (5.4%) | 5.8% | 74.6% |
| 100 | 1 (2.7%) | 3.0% | 93.5% |

Ruling out even one forged segment at 95% takes 110 single-segment independent draws. These numbers
are small on purpose: they say what a handful of checks does and does not show.

### Assumptions

- The forged segments were fixed before the draws existed. The receipts rules give this: the
  registration is merged after the release anchor, and each round is fixed at registration.
- Abandoned, missing and incomplete checks are unrelated to which segment was drawn. A checker sees
  the segment before running it; if a checker abandons a check because its segment is forged or came
  out MISMATCH, the bound is wrong. The headline states how many independent checks this covers. That is why every abandoned and missing
  check is listed as a follow-up target with its segments: rechecking them is how a reader tests this
  assumption instead of taking it on trust.
- The keys classed independent really are independent of the project and of each other. Two keys held
  by one person count twice. Nothing in the data can show this.
- A MATCH is a claim by the key holder, not proof the check ran. A false MATCH on a forged segment
  breaks the bound; anyone can recheck a MATCH segment, which is the defence.

### What it does not cover

- Anything outside the segments: the data preparation, the weights file or the timestamps (those are
  the CPU check), and other hardware (see the hardware results; this kit doesn't look at them).
- A combined outcome such as `ERROR+MATCH`: the report doesn't say which segment had which outcome, so
  the check counts toward nothing and is listed as a follow-up.

## Input checks

The report fails closed on anything that isn't exactly the shape `receipts_report.py` prints: an extra
or missing field at the top, in a key, or in a check; a key listed twice; a status that isn't one the
report can produce; `k`, `m` or check numbers that don't add up; segments out of range, repeated, or
missing for a check that has an outcome. A future report format should add a version field, and this
script should then accept only the versions it knows.

## AI use

Written with AI coding assistance and reviewed against the tests in `tests/simple/test_campaign.py`,
which cover the numbers above, the class rules, disputes, follow-ups, empty input, malformed and
duplicate inputs, alpha edges, and one run from signed receipts through the report to the table. No
real campaign has been summarised with it yet.
