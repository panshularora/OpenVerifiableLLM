# Check receipts (roadmap item 6, format v0)

Status: an experimental branch for discussion, not an agreed design. The open questions at the end
are meant for the design issue before any of this is proposed upstream.

## The problem

A check today is a line in `local-verifications.jsonl` that the checker pasted into an issue. Two
things in it are the checker's own choice. They choose which segment to recompute, so a run that is
honest on most segments could be "checked" only where it is honest. And nothing ties the line to a
person: anyone can paste a line saying MATCH.

A receipt fixes both, within limits stated below. The segment is picked by a public random beacon at
a time the checker committed to in advance, and the result is signed with a key the checker
registered before that randomness existed.

## The flow

```text
release anchor (RFC 3161 time T_a)
  └─ registration comment, created_at T_r > T_a     signs: key, release, k, m
       R0 = first drand round after T_r
       ├─ check 1 uses round R0 + 1 → segments → receipt 1 (MATCH / MISMATCH / WRONG_STACK / ERROR / ABANDONED)
       ├─ check 2 uses round R0 + 2 → segments → receipt 2, which names receipt 1 by URL and hash
       └─ ... up to check m. A check with no receipt is reported as MISSING, with its segments.
```

Commands (all in `simple/receipt.py`):

```bash
python simple/receipt.py keygen   --out ~/.ovl/checker.key           # raw 32-byte Ed25519 seed, mode 0600
python simple/receipt.py register --key ~/.ovl/checker.key --k 1 --m 3 --anchor RUN/anchor.json
#   post the printed envelope as one comment on the checks issue, then:
python simple/receipt.py schedule --registration COMMENT_URL --anchor RUN/anchor.json
python simple/receipt.py derive   --registration COMMENT_URL --j 1 --anchor RUN/anchor.json
#   recompute those segments with verify_segment.sh, then:
python simple/receipt.py sign     --registration COMMENT_URL --j 1 --key ~/.ovl/checker.key \
                                  --report local-verifications.jsonl --anchor RUN/anchor.json
python simple/receipt.py abandon  --registration COMMENT_URL --j 2 --previous RECEIPT_1_URL --reason "..."
python simple/receipt.py verify   RECEIPT_URL --anchor RUN/anchor.json
python simple/receipts_report.py  --issue AOSSIE-Org/OpenVerifiableLLM#NNN --anchor RUN/anchor.json \
                                  --independent KEY_FP ...
```

## Format

Both objects are [DSSE](https://github.com/secure-systems-lab/dsse) envelopes with exactly one
Ed25519 signature over `PAE(payloadType, payload)`; the `keyid` is `key_fp`, the SHA-256 of the raw
public key. The payload is JSON with sorted keys and no spaces. An object's id is the SHA-256 of the
RFC 8785 canonical form of the whole envelope.

- Registration, `payloadType` `application/vnd.ovllm.checker-registration.v0+json`, schema
  `ovllm.checker-registration/v0`. Fields: the public key and `key_fp`, the `release` (dataset, run
  path, anchor SHA-256, chain entries, chain head), the beacon (drand quicknet chain hash, pinned),
  `k` segments per check (1 to N) and `m` checks (1 to 64).
- Receipt, `payloadType` `application/vnd.in-toto+json`, an in-toto Statement v1 with predicate type
  `https://github.com/AOSSIE-Org/OpenVerifiableLLM/check-receipt/v0`. Subjects are the anchor and
  the chain head. The predicate repeats the release and holds the `challenge` (the registration's
  URL, hash and time, `R0`, `j`, `m`, `round`, the round's BLS `signature`, `previous_outcome`,
  selection `ovllm-select-v0`, `k`) and `results`, one per drawn segment, plus the environment and
  code commit from the report.

Selection. `randomness = SHA-256(round signature)`. The seed is
`SHA-256("OVLLM-check-seed-v0" ‖ randomness ‖ chain_head ‖ key_fp ‖ j as u64 big-endian)`. Words
are the first 8 bytes of `SHA-256("OVLLM-check-select-v0" ‖ seed ‖ counter as u32 big-endian)`,
with rejection sampling to avoid modulo bias, and a partial Fisher–Yates shuffle picks `k` of the
N = chain entries − 1 segments, returned sorted. Different checkers get different segments from
the same round because `key_fp` is in the seed.

## What `verify` checks, in order

It fails closed: any step that can't be completed is a FAIL, never a skip.

1. The comment URL is a GitHub issue comment, the comment belongs to that issue, and it was never
   edited (`updated_at` equals `created_at`).
2. The comment holds exactly one envelope; its type is right and its one signature verifies under
   the key named in it.
3. The registration (fetched by its URL) verifies the same way, has the same `key_fp`, matches the
   hash the receipt names, and is for the same release as the anchor given on the command line.
4. The registration's `created_at` is strictly after the anchor's time. The anchor time is the later
   of the two RFC 3161 replies, both checked over the anchor bytes with `stamp.verify_reply`.
5. `R0` is the first drand round after the registration time, and `round` is exactly `R0 + j`
   with `1 ≤ j ≤ m`.
6. For `j > 1`, `previous_outcome` names a posted, valid receipt `j − 1` by URL and envelope hash;
   for `j = 1` it is empty.
7. The drand round signature verifies (BLS on G1, quicknet), and the receipt comment was posted
   after that round existed.
8. The results cover exactly the derived segments, and the outcomes are valid and not mixed with
   ABANDONED.

`receipts_report.py` runs `verify` on every comment in the issue, keeps one registration per key
and one outcome per `j`, marks gaps and overdue checks MISSING with their recomputed segments, lists
segments where keys disagree, and counts coverage only from MATCH outcomes of keys you list as
independent. It prints the chance that `d` independent draws catch one forged segment,
`1 − ((N − 1)/N)^d`.

## The timing rules, and why each is there

- Rounds are fixed at registration: `R0 + j`, no gaps. A checker can't wait for a round that lands on
  a segment they like, because the round for each check was settled before its randomness existed.
- `m` is declared at registration. Otherwise a checker could keep adding checks until they got the
  draws they wanted and report only those. Each skipped check still shows up, as MISSING, with its
  segment.
- The registration must come after the release anchor. Otherwise a key could be registered before the
  run, with the run then tuned to pass that key's draws.
- Only the server-side `created_at` of a GitHub issue comment counts, never a git date, which the
  author sets.
- Edited comments are rejected. An edit keeps `created_at`, so without this rule a checker could post
  a placeholder, wait for the rounds, then edit in a registration for whichever of several keys
  draws the segments they want.
- One registration per key, and a re-registration is rejected, so a key can't restart its schedule.

## What a receipt does not show

- That the checker ran the computation. A receipt says the key holder claims a result for segments
  they did not choose. Its value comes from several independent keys, and from the fact that a MATCH
  claim on a segment can be rechecked by anyone.
- That two keys are two people. Independence is a judgment the reader makes (`--independent`).
- That the run is honest on segments nobody drew. With `d` independent draws out of `N = 37`
  segments, one forged segment is caught with probability `1 − (36/37)^d`: about 24% at 10 draws and
  about 94% at 100.

## What it relies on

- drand quicknet: the threshold of League of Entropy members not colluding to bias or predict a
  round. Rounds are verified against the pinned group public key, so a relay can't forge one.
- GitHub's `created_at`, and GitHub not rewriting comments. A comment deleted by its author leaves a
  gap that shows as MISSING; a comment changed by GitHub staff is out of scope.
- The two RFC 3161 timestamp authorities behind the anchor time, as for the anchor itself.
- Ed25519 (via `cryptography`), and `py_ecc` for BLS12-381. `py_ecc` is pure Python and takes about
  half a second per round.

`stamp.py` gains `stamp_digest`, which timestamps any SHA-256 with both TSAs, so a checker can also
timestamp a receipt's envelope id. That shows the receipt existed by then, not that it was public.

## Open questions for the design issue

1. Publication channel. v0 accepts only GitHub issue comments. A keys file merged by PR, using the
   PR's `merged_at`, would let maintainers vet keys, at the cost of a maintainer step per checker.
2. The deadline for MISSING (72 hours after the round in the report, a flag today).
3. Whether `k` and `m` should have project-wide defaults.
4. Whether receipts should be signed with Sigstore keyless signing, which the release already uses,
   instead of a long-lived key.

## Credits and AI use

The key-file layout follows ryoari's Ed25519 artifact signing in
[#92](https://github.com/AOSSIE-Org/OpenVerifiableLLM/pull/92) and the signed publish loop in
[#93](https://github.com/AOSSIE-Org/OpenVerifiableLLM/pull/93), both later removed with the legacy
pipeline in #271: a private key is a raw 32-byte Ed25519 seed with owner-only permissions, and the
public key is the raw 32 bytes beside it (`checker.key.pub`), so a PyNaCl `SigningKey` file works
unchanged. The `checks` rows come from the verification record of #302.

This branch was written with AI assistance (Grok Bot), with each rule above covered by a test in
`tests/simple/test_receipt.py`. The tests use real drand quicknet rounds recorded in
`tests/simple/fixtures/drand_quicknet_rounds.json`, the published main-run and fork anchors, and an
in-memory mirror of GitHub comments. No real GPU check has been run through this flow yet.
