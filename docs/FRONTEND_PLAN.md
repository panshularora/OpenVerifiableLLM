# OpenVerifiableLLM frontend: my plan

This is exactly what I will build and how. It builds on [#122's data layer](https://github.com/AOSSIE-Org/OpenVerifiableLLM/pull/122) (lean dependencies, `contracts.ts` / `validate.ts`) and brings over [#178's Playwright and accessibility tests](https://github.com/AOSSIE-Org/OpenVerifiableLLM/pull/178). Every number below was checked against the real `release/simple-run/` files.

## 1. Goals and non-goals

Goal: a public evidence site for each OpenVerifiableLLM release. A visitor should be able to answer, without trusting the project:
1. What was released (model, data, code, recipe)?
2. What has been checked, on what hardware, with what result?
3. What is still unchecked or unknown?
4. How do I check it myself?

Rule: every page answers one question, written as its lede. If a page can't state its question in one sentence, it doesn't ship.

Non-goals (v1): no globe, scroll animation, spotlight cards, 3D, or hero motion; no framer-motion, Tailwind, zod; no committed screenshots; no chat/inference page (inference stays as #122's `DisabledAdapter`); no fetching `.pt` files; no server or database; no accounts; no parsing of `.tsr` files in the browser.

## 2. Pages and routes

| Page | Route | The one question |
|---|---|---|
| Overview | `/r/:run/` | What was trained, and is the evidence intact? |
| Chain | `/r/:run/chain` (`/chain/:index`) | Is the training record unbroken, entry by entry? |
| Verify it yourself | `/r/:run/verify` | How do I check this without trusting the site? |
| Hardware | `/r/:run/hardware` | On which hardware do the bits reproduce? |
| Files and data | `/r/:run/data` | Which bytes are official, and which data went in? |
| Anchor | `/r/:run/anchor` | When was this release fixed, and how do I check that? |
| Segments (second wave) | `/r/:run/segments` | Was every segment re-run, and by whom? |
| Runs (second wave) | `/runs` | What other runs exist? |

`/` redirects to the latest run in `runs.ts`. Every page shows a run switcher and the pinned dataset revision.

## 3. Data sources and contracts

Source: Hugging Face dataset `AOSSIE/openverifiable-enwiki-20260901-20260918-r1-evidence`, folder `release/<run>/` (runs today: `simple-run`, `unlearning-3i-atlas`). The browser reads only small text files (each < 20 KB), at a pinned revision via `resolve/<revision>/`. A build script (`scripts/snapshot.mjs`) also copies them into `public/data/<run>/` and records their sha256, so the site and CI work offline. The page always cites the HF URL as the source.

Every page reads through one adapter: `loadRelease(run) -> Release`. Validators are hand-written type guards (the #122 pattern) that throw `ReleaseFileError { file, fieldPath, reason }`. A file that fails validation produces an error state for that file; partial data is never shown silently.

Loader rules (from #178's `load.ts`): only same-origin or allow-listed HF `resolve/<revision>/` URLs; `credentials: 'omit'`; 1 MB cap per file; fatal UTF-8 decoding; 10 s timeout.

| File | Required fields (verified in the real files) | Validation beyond shape |
|---|---|---|
| `anchor.json` | `schema` = `"ovl.release-anchor.v1"`, `dataset`, `path`, `commit` (40-hex), `chain_entries` (38), `chain_head_index` (37), `chain_head_sha256`, `chain_jsonl_sha256`, `release_hashes_sha256`, `final_model`, `final_state` (64-hex). No timestamp field. | `chain_head_index == chain_entries - 1`; unknown schema → "unsupported schema" error |
| `chain.jsonl` | Per line: `index`, `kind` (simple-run: initial 1, progress 30, transition 3, epoch 2, base 1, final 1; fork kinds in the unlearning run), `previous` (64-hex, `null` only at index 0), `model`, `state`, `time` (unix float), `control {batches, cursor, epoch, global_step, phase, phase_step, pos[2]}`. Entry 0 also has `data {wikipedia, conversation}`, `environment {gpu, cuda, cudnn, torch, cublas_workspace}`, `recipe`. | `index` contiguous from 0; every `previous` link; line count == `chain_entries`; head hash == `chain_head_sha256`; last `model`/`state` == `final_model`/`final_state`; file sha256 == `chain_jsonl_sha256` |
| `verifications.jsonl` | `segment` (int), `result` ∈ {MATCH, MISMATCH}, `checker`, `code` (e.g. `ovl.py 202ac6ce`), `gpu`, `driver`. Real file: 46 rows, all MATCH; checkers: "project second pod" 37, "project training pod" 8, "project fresh machine public files only" 1. | `segment` in 0..chain_entries−2; duplicate segments allowed and aggregated |
| `hardware-map.jsonl` | `segment` (int or string, e.g. `"all 37"`), `result`, `hardware`, `driver`, `software`, `note`. Real file: 5 rows; segment 32 MATCH on RTX 5090, MISMATCH on RTX 4090, H100, x86_64 CPU; `"all 37"` MATCH on RTX 5090. | string segments kept as labels, never coerced |
| `release-hashes.txt` | 42 lines `<64-hex>  <path>` (sha256sum format) | own sha256 == `release_hashes_sha256`; its `chain.jsonl` line == `chain_jsonl_sha256` |
| `recipe.json` | `batch_size` 32, `boundary_every` 18080, `context` 512, `conversation_epochs` 3, `init`, `model {attn_impl, dropout, embed_dim 384, max_seq_len 512, num_heads 6, num_layers 6, vocab_size 32000}`, `schedule {conversation, floor, peak, warmup, wikipedia_updates}`, `seed` 1234, `weight_decay` | its sha256 under the Python canonical form == chain entry 0 `recipe` (shown as PENDING if the canonical form is not confirmed) |
| `anchor.digicert.tsr`, `anchor.freetsa.tsr` | binary, linked only | listed in `release-hashes.txt` if present there; size shown |
| `README.md` | linked, not re-rendered | — |

## 4. In-browser checks

All checks use WebCrypto SHA-256 and run in a Web Worker when a chain has more than 1,000 entries.

1. **Hash links.** For each entry, hash = sha256 of Python `json.dumps(entry, sort_keys=True)` (the rule in `simple/ovl.py` `read_chain`): default separators `", "` and `": "`, ASCII escaping, Python float repr. Entry *i* is linked when its `previous` equals entry *i−1*'s hash. I reproduce the canonical form in TypeScript and test it against all 37 published `previous` values; On the real file all 37 links pass.
2. **Chain head vs anchor.** Count == `chain_entries` and hash of the last entry == `chain_head_sha256` (`9edbcaddbb08…5d8251a51`). This catches a truncated chain, which link checks alone cannot.
3. **Chain file vs anchor.** sha256 of the raw `chain.jsonl` bytes == `chain_jsonl_sha256` (`c1f867e1…8a327`), and equals the `chain.jsonl` line in `release-hashes.txt`.
4. **Hashes file vs anchor.** sha256 of `release-hashes.txt` == `release_hashes_sha256` (`479ce030…080e8d`).
5. **Final digests.** Last entry `model` == `final_model` (`ec9a2b31…`), `state` == `final_state` (`934d681c…`).
6. **Small files vs release-hashes.txt.** Every fetched file that has a line in `release-hashes.txt` is hashed and compared.

Each check returns MATCH, MISMATCH (with expected and actual), or PENDING (file missing, or not checkable in the browser). I verified checks 1–5 pass on the real `simple-run` files.

**Link to the CLI fix.** Check 2 is the browser version of the fix for [issue #209](https://github.com/AOSSIE-Org/OpenVerifiableLLM/issues/209) / [PR #210](https://github.com/AOSSIE-Org/OpenVerifiableLLM/pull/210): `ovl.py verify` only checked links, so a chain with its tail removed passed. The site and the CLI must apply the same rule (count + head hash vs `anchor.json`), and the Verify page tells users which CLI version includes it. The truncated-chain fixture from #209 (first 30 lines) is a test case for the site too.

## 5. Pages in detail

Shared states on every page: **Loading** (skeleton rows with "Loading `chain.jsonl` from Hugging Face…"); **Error** (file name, URL, reason, retry button; e.g. "Couldn't load `chain.jsonl`: HTTP 404. Nothing on this page is shown as verified."); **Invalid** ("`anchor.json` doesn't match the expected format: `chain_head_sha256` is not a 64-character hex string."); **Missing optional file** (section shows PENDING with "This run doesn't publish `hardware-map.jsonl`."); **MISMATCH** (red badge, expected vs actual hash side by side, link to the raw file). Errors never fall back to a success state.

### 5.1 Overview `/`
Question: What was trained, and is the evidence intact?
Top to bottom:
1. Lede (the question) and run name + `path`.
2. Verdict banner, computed from checks 1–5: "Evidence intact: 38/38 chain entries linked · head matches anchor · file hashes match · 46/46 segment checks MATCH." On any failure: "Evidence check failed: chain head does not match `anchor.json`" with a link to the failing check. Each check is a row with a badge.
3. What was released: `dataset`, `commit` (link to GitHub), `final_model`, `final_state`, training start/end (`time` of entries 0 and 37, ISO UTC), recipe summary ("6 layers, 6 heads, 384 dim, vocab 32,000, context 512, seed 1234").
4. What was checked: segment re-runs by checker (37 / 8 / 1), GPU and driver; independent checks: "None outside the project yet."
5. What is not checked / caveats: "Bit-exact results need an RTX 5090. Other architectures give different bits (see Hardware)." "A timestamp shows the release existed by a date, not when training happened."
6. Links to each page.
Interactions: copy buttons on hashes; each verdict row links to its evidence.

### 5.2 Chain `/chain`
Question: Is the training record unbroken, entry by entry?
1. Summary line: "38 entries · all links OK · head matches `anchor.json`."
2. Table (caption = question): `index`, `kind`, `control.phase`, `control.epoch`, `control.global_step`, `time` (UTC), `model` (short), `state` (short), `previous` (short), computed hash (short), link status (✓ MATCH "links to #i−1" / ✕ MISMATCH "expected …, found …").
3. Each row has a permalink (`/chain#37`) and a `<details>` with raw JSON; entry 0 also shows `data`, `environment`, `recipe`.
4. Head row marked "Head · anchored".
Interactions: filter by `kind`; sort by index; jump to index; copy any hash; paged at 100 rows for long chains.
States: break at row *k* → row and all later rows flagged "after a break", banner names *k*. Truncation → "Chain has 30 entries; `anchor.json` says 38."

### 5.3 Verify it yourself `/verify`
Question: How do I check this without trusting the site?
Three tiers, each: what it proves, prerequisites, exact command (copy button), expected output, time.
1. No GPU: download files; `sha256sum -c release-hashes.txt` (expected: every line `OK`); chain recompute with `ovl.py verify` (with the head check from PR #210) — expected `chain head MATCH (38 entries)`.
2. Timestamp: the two `openssl ts -verify` commands from the release README; expected `Verification: OK`.
3. GPU: RTX 5090, image `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`; `bash code/simple/verify_segment.sh`, `K=3 …`, `SEGMENTS="7 8" …`; expected `MATCH` per segment, about 20 min each. Shows catch probability 1 − ((N−1)/N)^M with N = 37 and a small input for M.
Copy: "Run these yourself. If your result differs, that is a finding; please report it."

### 5.4 Hardware `/hardware`
Question: On which hardware do the bits reproduce?
Table from `hardware-map.jsonl`: `segment`, `hardware`, `driver`, `software`, `result`, `note` (all 5 rows). Callout above the MISMATCH rows: "Expected: other GPU architectures and CPUs run different low-level kernels, so the bits differ. This is not evidence of tampering." Trust line: "Bit-exact checks need an RTX 5090 (Blackwell). Drivers 570.195.03 and 595.91.07 agree." Interaction: filter by result.

### 5.5 Files and data `/data`
Question: Which bytes are official, and which data went in?
1. Integrity line: "`release-hashes.txt` matches `anchor.json`" (check 4).
2. Table of all 42 lines: file, sha256, size (HF tree API at build time), download link, and for small files a browser result (MATCH/MISMATCH); `.pt` files show "not checked in browser — use `sha256sum -c`".
3. Data digests from entry 0: `data.wikipedia`, `data.conversation`; `recipe` digest.
4. Coverage: which segments were checked; for the unlearning run, the excluded article and the shared range 0–30.

### 5.6 Anchor `/anchor`
Question: When was this release fixed, and how do I check that?
1. `anchor.json` field by field (all 11 fields), each with what it means.
2. The two `.tsr` files (DigiCert, FreeTSA) with size and download link.
3. Timestamp time, extracted at build time with `openssl ts -reply -text` and shown as UTC (release README: 2026-10-20 14:38:43 UTC); PENDING if not extracted.
4. Verify commands with copy buttons.
Copy: "A timestamp proves this `anchor.json` existed unchanged by this time. It does not prove when training happened."

### 5.7 Second wave
Segments: grid of 37 cells, each MATCH/MISMATCH/PENDING with checker count, plus the full `verifications.jsonl` table with filter by checker. Runs: table of runs with pinned revision, entries, verdict.

## 6. Architecture and folder layout

Stack: React, react-dom, react-router-dom, Vite, TypeScript strict. Dev: vitest, testing-library, Playwright, `@axe-core/playwright`. Fonts: `@fontsource/inter`, `@fontsource/ibm-plex-mono` (OFL licence file included). Static build, no runtime server.

```
frontend/
  src/
    data/
      contracts.ts       # types + schema ids per file
      validate.ts        # one validator per file
      load.ts            # bounded fetch
      canonical.ts       # Python json.dumps(sort_keys=True) in TS
      checks/            # links.ts, head.ts, fileHashes.ts (WebCrypto)
      sources/hf.ts      # HF resolve/<revision>/ URLs
      sources/static.ts  # public/data/<run>/
      release.ts         # loadRelease(run)
      runs.ts            # runs + pinned revisions (the only file to edit for a new run)
    pages/  components/  layout/  inference/DisabledAdapter.ts
    styles/tokens.css  base.css
  scripts/snapshot.mjs  scripts/static-server.mjs  scripts/contrast.mjs
  tests/unit/  tests/e2e/  tests/fixtures/
```

Components: `Hash` (first 8 … last 8, full value in DOM, copy), `CopyCommand` (command + expected output), `StatusBadge` (icon + word + colour), `VerdictBanner`, `CheckRow`, `DataTable` (caption, sticky header, row permalinks), `RawJson`, `FileError`.

Multi-run scaling: routes `/r/:run/...`; files fetched lazily per page and cached per run; a new run = one line in `runs.ts` + snapshot; a new file format = new schema id + validator, old runs keep theirs; chains over 1,000 entries use paging and a Worker.

## 7. Design system

Brand source: AOSSIE brand guide (AOSSIE-Org/Website `brand/Brand.md`). Status colours are not brand colours, so "brand" is never read as "verified".

| Role | Light | Dark |
|---|---|---|
| Background / text | `#FFFFFF` / `#282C33` (14.02:1) | `#0F1115` / `#E6E6E6` (15.14:1) |
| Muted text | `#5C6370` (6.05:1) | `#A0A6B0` (7.72:1) |
| MATCH ✓ | `#15803D` (5.02:1) | `#4ADE80` (10.84:1) |
| MISMATCH ✕ | `#B91C1C` (6.47:1) | `#F87171` (6.83:1) |
| PENDING ○ | `#B45309` (5.02:1) | `#FBBF24` (11.32:1) |
| Brand gold `#FFCD00` | fill only (active nav, focus ring, banner rule), with `#282C33` text (9.33:1); never text on light | fill only |
| Brand green `#00843D` | header/footer logo and rules only | header/footer only |
| Border | `#E6E6E6` (decorative) | `#3A3D45` |

Type: Inter for text, IBM Plex Mono for hashes, commands, raw JSON; `tabular-nums` for counts. Light/dark from `prefers-color-scheme` plus a manual toggle saved in `localStorage`.

## 8. Footer

Every page: AOSSIE logo, "OpenVerifiableLLM is an AOSSIE project · Made with ♥ by [AOSSIE](https://aossie.org) Community" (the heart has `role="img" aria-label="love"`), links: GitHub repo, licence, HF dataset (pinned revision), Discord, other AOSSIE projects (aossie.org/projects). Second line: "© {year} AOSSIE." This follows the Zplit and Resonate sites.

## 9. Accessibility

WCAG 2.2 AA. Tables with `<caption>` (the page question) and `<th scope>`; status never by colour alone; skip link; visible gold focus ring (3 px, ≥ 3:1 on both themes); copy buttons labelled ("Copy SHA-256 of chain head") with an `aria-live="polite"` "Copied"; truncated hashes keep the full value in the DOM; usable at 200 % zoom and 320 px width; `prefers-reduced-motion` honoured; times shown in UTC with `<time datetime>`.

## 10. Testing and CI

- Unit (vitest): each validator with good and bad fixtures (tampered `previous`, reordered index, truncated chain from #209, wrong schema id, head ≠ anchor, file hash ≠ `release-hashes.txt`, missing file, invalid JSON line).
- Chain recompute test: canonical form reproduces all 37 published `previous` values and `chain_head_sha256` of the real `simple-run` chain.
- Colour-contrast test (`scripts/contrast.mjs` as a vitest): reads `tokens.css` and fails if any text/status pair in either theme drops below 4.5:1, or gold is used as text on light.
- Playwright (ported from #178): one smoke + one axe test per page, in light and dark; keyboard/skip link; 390 px and 1440 px no overflow; 200 % zoom; error states never show success; copy button copies the exact full value.

| Page | Smoke assertion |
|---|---|
| Overview | "Evidence intact"; "46/46"; `final_model` starts `ec9a2b31` |
| Chain | 38 rows; `/chain#37` opens entry 37; tampered fixture flags that row |
| Verify | three tiers; copy gives the exact `verify_segment.sh` command |
| Hardware | 5 rows; RTX 4090 row reads MISMATCH with the explanation |
| Data | 42 rows; hashes-file check MATCH |
| Anchor | `chain_head_sha256` and both TSA names shown |

- CI: GitHub Actions on `frontend/**`: `npm ci`, typecheck, lint, unit, build, size check, Playwright (Chromium) against the static snapshot. Network blocked in CI; all data from `public/data/` and fixtures.

## 11. Performance budget

Initial JS ≤ 120 KB gzip; CSS ≤ 20 KB; fonts (subset, `font-display: swap`) ≤ 100 KB; data per run < 50 KB; LCP ≤ 2.0 s on Lighthouse mobile; chain checks for 38 entries < 50 ms. Size checked in CI on `dist/`.

## 12. Deployment

Static Vite build with a relative base path. GitHub Pages via an Actions workflow on `main`, or wherever maintainers choose. Snapshot refreshed by running `scripts/snapshot.mjs` and committing the new `public/data/<run>/` with the revision. A 404 fallback for client routes.

## 13. Milestones (small commits, one PR)

1. Scaffold `frontend/` from #122's structure; drop Journey/Try; add Playwright config + static server from #178.
2. Contracts + validators for all files; fixtures; unit tests.
3. `canonical.ts` + checks; chain recompute golden test; truncated-chain test.
4. Sources, `loadRelease`, snapshot script, `public/data/simple-run/`.
5. Tokens, contrast test, layout, footer, `StatusBadge`, `Hash`, `CopyCommand`.
6. Overview + Chain, with smoke + axe.
7. Verify + Hardware, with smoke + axe.
8. Data + Anchor, with smoke + axe.
9. `/r/:run` routing + `unlearning-3i-atlas`.
10. CI workflow, size budget, README (how to add a run).
11. Second wave: Segments, Runs.

## 14. Questions for the maintainers

1. Which `verifications.jsonl` format is canonical: the published lines or what `ovl.py verify` writes (it adds `PASS` and `chain_head`)? I'll accept the published one first.
2. Read HF live at a pinned revision, or commit a snapshot under `frontend/public/data/`? (My default: snapshot, HF cited as source.)
3. Is the footer wording and logo above right, or is there a project convention?
4. Scope: `simple-run` and `unlearning-3i-atlas` only, or legacy releases too?
5. Target `main` (`frontend/`) even though `simple/` (branch `simple-production-loop`) isn't merged yet?
6. Will file names or schema ids change in the final release layout (e.g. a schema id on chain lines)?
7. Should independent checks be submitted somewhere the site can read (a dataset file or GitHub issues)?
8. Hosting: GitHub Pages under `aossie-org.github.io/OpenVerifiableLLM`, or elsewhere?
9. Will PR #210's head check land, so the Verify page can point to a CLI version that matches the in-browser check?
10. Should the `recipe.json` digest in chain entry 0 be checkable in the browser, i.e. what exact canonical form produced it?
