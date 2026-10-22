# OpenVerifiableLLM frontend: plan

Status: draft for Panshul to review, then share with rajat (ryoari) and adi (AdityasagarR123) before coding.

## Summary for rajat

Rule: **every page answers one question.** If a page can't say its question in one sentence, it doesn't ship.

| Page | Route | The one question |
|---|---|---|
| Overview | `/` | What was trained, and is the evidence intact? |
| Chain | `/chain` | Is the training record unbroken, entry by entry? |
| Verify it yourself | `/verify` | How do I check this without trusting you? |
| Hardware map | `/hardware` | On which hardware do the bits reproduce? |
| Data | `/data` | Exactly which files and data went in, and what's covered? |
| Anchor | `/anchor` | When was this release fixed, and how do I check the timestamp? |
| Segments, Runs (second wave) | `/segments`, `/runs` | Which segments were recomputed? What other runs exist? |

Base: #122 (Panshul, `feat/frontend-overview`): its structure, lean dependencies and `contracts.ts`/`validate.ts` layer. Ported from #178 (adi, `feat/frontend-evidence-explorer`): Playwright, axe, the evidence list/detail views, the bounded loader. Dropped: globe, scroll animation, spotlight cards. One joint PR, adi credited as co-author.

## 1. Goal

A public evidence site for OpenVerifiableLLM releases. Someone who lands on it should be able to answer, without trusting us:

1. What was released, and what exactly is it (model, data, code, recipe)?
2. What has been checked, by whom, on what hardware, and with what result?
3. What is still unchecked or unknown?
4. How do I check it myself?

It is a record, not a showcase. No globe, no scroll animations, no 3D. Every page answers one of the four questions above, or it doesn't ship.

## 2. Ground rules (from rajat's message)

- Build on #122's structure: lean dependencies, plus the `contracts.ts` / `validate.ts` layer that checks data before rendering.
- Bring over from #178: Playwright end-to-end tests, accessibility tests (axe), and its best views.
- Drop from #178: `globe.tsx`, `container-scroll-animation.tsx`, `spotlight-card.tsx`, framer-motion, committed screenshots.
- Real release data, not fixtures. Fixtures stay only for tests (tampered chain, missing file, mismatch).
- One joint PR, with adi credited as co-author (`Co-authored-by:` on the commits that carry his work).
- "AOSSIE" footer on every page.

## 3. Data source: the real release layout

Location: Hugging Face dataset `AOSSIE/openverifiable-enwiki-20260901-20260918-r1-evidence`, under `release/<run>/`. Today there are two runs: `simple-run` and `unlearning-3i-atlas`.

| File | What it holds | Used by |
|---|---|---|
| `anchor.json` (`schema: ovl.release-anchor.v1`) | chain head index and sha256, chain/hashes file sha256, final model and state digests, code commit, dataset | Overview, Verify |
| `anchor.digicert.tsr`, `anchor.freetsa.tsr` | RFC 3161 timestamps over `anchor.json` (6,007 and 4,644 bytes) | Anchor, Verify |
| `chain.jsonl` | 38 entries: `index`, `kind` (simple-run counts: 1 initial, 30 progress, 1 base, 3 transition, 2 epoch, 1 final; fork entries in the unlearning run), `control` (phase, global_step, epoch, pos), `model`, `state`, `previous`, `time`; entry 0 also has `data`, `environment`, `recipe` | Chain page |
| `verifications.jsonl` | per-segment results: `checker`, `code`, `gpu`, `driver`, `result`, `segment` | Overview, Segments |
| `hardware-map.jsonl` | per hardware: `segment`, `result`, `hardware`, `driver`, `software`, `note` | Hardware page |
| `recipe.json` | model shape, schedule, seed, batch, boundary_every | Overview, Recipe |
| `release-hashes.txt` | sha256 of every file | Data page |
| `README.md` | walkthrough | linked, not re-rendered |

Loading: the browser fetches only these small files (all under 20 KB) over the HF `resolve/` URLs, pinned to a dataset revision. It never fetches the `.pt` files. A build-time script can also snapshot them into `public/data/<run>/`, so the site works offline and in tests.

Contracts: one TypeScript type and one validator per file, in `src/data/contracts.ts` and `validate.ts`, following #122's pattern. If a file fails validation, the page shows "this file doesn't match the expected format" with the field name. It never renders partial data silently.

Open question for rajat: the format `ovl.py verify` writes to `verifications.jsonl` differs from the published lines. Which one is canonical? The adapter should accept the published one now and add the other once he answers.

## 4. Pages

Each page's question is its lede. Every number on a page comes from a validated release file; nothing is hardcoded. Status colour is always paired with a word and an icon (MATCH / MISMATCH / NOT CHECKED).

1. **Overview (`/`)**: "What was trained, and is the evidence intact?"
   - Verdict banner computed in the browser: chain links OK (38/38), head equals `anchor.json.chain_head_sha256`, sha256 of the fetched `chain.jsonl` equals `anchor.json.chain_jsonl_sha256` and its line in `release-hashes.txt`, segments MATCH 37/37 from `verifications.jsonl`. Any failed check turns the banner red and names the check.
   - Release name (`dataset`, `path`), dataset `commit`, training start/end (chain entry 0 and 37 `time`, shown as UTC), anchor time (from the `.tsr` files, not `anchor.json`; 2026-10-20 14:38:43 UTC per the release README), final model digest `final_model`, recipe summary (6 layers, 384 dim, seed 1234).
   - Honest result line: all 46 rows of `verifications.jsonl` are MATCH. The only MISMATCH rows in the release are in `hardware-map.jsonl` (segment 32 on RTX 4090, H100 and x86 CPU); the Overview says these are the expected cross-architecture divergence, not a failed check, and links to the Hardware page.
   - Honest scope line: who checked ("project second pod" for all 46 lines today; independent checks 0 beyond the fresh-machine re-run of segment 21).
2. **Chain (`/chain`)**: "Is the training record unbroken, entry by entry?"
   - Table of 38 rows: `index`, `kind`, `control.phase`, `control.global_step`, short `previous`, computed own hash (sha256 of `json.dumps(entry, sort_keys=True)`, the rule in `simple/ovl.py` `read_chain`), and a link icon that is green only when row *i*'s `previous` equals row *i−1*'s computed hash.
   - Each row has an anchor (`/chain#37`) and clicking it opens the raw JSON for that entry (a `<details>` block, no modal). Entry 0 additionally shows `data`, `environment`, `recipe`.
   - Canonicalisation note: Python's `json.dumps` default separators (`", "`, `": "`) and float repr must be reproduced exactly; we recompute from the raw line where possible and test against all 38 published hashes.
3. **Verify it yourself (`/verify`)**: "How do I check this without trusting you?" Three tiers, each with prerequisites, the exact command, expected output and a copy button:
   - No GPU: hash links, seed re-generation, file hashes against `release-hashes.txt` (`sha256sum -c`).
   - Timestamp: the two `openssl ts -verify` commands from the release README; expected `Verification: OK`.
   - GPU (RTX 5090, image `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`): `bash code/simple/verify_segment.sh`, `K=3 …`, `SEGMENTS="7 8" …`; expected `MATCH`/`MISMATCH` per segment, ~20 min each. Shows the catch probability 1 − ((N−1)/N)^M with N = 37.
4. **Hardware map (`/hardware`)**: "On which hardware do the bits reproduce?" A matrix from `hardware-map.jsonl`: hardware × driver × software → result, with the `note`. MISMATCH rows (segment 32 on RTX 4090, H100, CPU) are shown plainly, not hidden, with the copy: "Expected: other GPU architectures run different low-level kernels, so bits differ. This is not evidence of tampering." A sentence states the trust assumption: bit-exact checks need an RTX 5090; drivers 570.195.03 and 595.91.07 agree.
5. **Data (`/data`)**: "Exactly which files and data went in, and what's covered?" The `release-hashes.txt` table (file, sha256, link to HF, size from the HF tree API), the prepared-data ID and the data digests in chain entry 0 (`data.wikipedia`, `data.conversation`), the rebuild-from-raw result, and coverage: which segments were checked, and for the unlearning run, the excluded article and the shared range 0–30.
6. **Anchor (`/anchor`)**: "When was this release fixed, and how do I check the timestamp?" Renders `anchor.json` field by field (it has no timestamp field of its own: `schema`, `dataset`, `path`, `commit`, `chain_entries`, `chain_head_index`, `chain_head_sha256`, `chain_jsonl_sha256`, `release_hashes_sha256`, `final_model`, `final_state`). The RFC 3161 proofs are the separate files `anchor.digicert.tsr` and `anchor.freetsa.tsr`; the page lists both `.tsr` files with the TSA names, and shows the verify commands. States clearly: a timestamp proves the release existed unchanged by that time, not when training happened. (Parsing the `.tsr` genTime in the browser is optional; a build-time step can extract it with `openssl ts -reply -text`.)

Second wave, after the six above are reviewed: **Segments (`/segments`)** (37-cell grid plus the verifications table) and **Runs (`/runs`)** (`simple-run`, `unlearning-3i-atlas`, later releases). All pages take a `:run` prefix (`/r/:run/chain`) so a new run reuses them.

Not in scope now: Try / chat page. Inference stays as #122's `DisabledAdapter` until rajat wants it.

## 5. Data contracts

One adapter, every page reads through it: `loadRelease(run) → Release`, built on #122's `contracts.ts` + `validate.ts` pattern (hand-written type guards that throw `SnapshotError` with the field path; no zod, to keep deps lean). Validation runs once per run; a failed file produces a per-file error state, never partial data.

| File | Contract (required fields) | Checks beyond shape |
|---|---|---|
| `anchor.json` | `schema === "ovl.release-anchor.v1"`, `chain_entries` int, `chain_head_index` int, `chain_head_sha256`, `chain_jsonl_sha256`, `release_hashes_sha256`, `final_model`, `final_state` (64-hex), `commit` (40-hex), `dataset`, `path` | `chain_head_index === chain_entries − 1`; unknown schema id → "unsupported schema" error |
| `chain.jsonl` | per line: `index` int, `kind` ∈ {initial, progress, base, transition, epoch, final, + fork kinds}, `previous` 64-hex or null (only index 0), `model`, `state` 64-hex, `time` number, `control` {`phase`, `global_step`, `epoch`, `phase_step`, `cursor`, `pos`[2], `batches`}; index 0 also `data`, `environment`, `recipe` | index contiguous from 0; every `previous` link; head hash vs anchor; last `model`/`state` vs anchor `final_model`/`final_state`; file sha256 vs anchor |
| `verifications.jsonl` | `segment` int, `result` ∈ {MATCH, MISMATCH}, `checker`, `code`, `gpu`, `driver` | segment in 0..N−2; duplicates allowed (multiple checkers), aggregated per segment |
| `hardware-map.jsonl` | `segment` int or string (e.g. `"all 37"`), `result`, `hardware`, `driver`, `software`, `note` | keep string segments as labels, don't coerce |
| `release-hashes.txt` | lines `<64-hex>␠␠<path>` | file's own sha256 vs anchor `release_hashes_sha256`; `chain.jsonl` line equals anchor |
| `recipe.json` | `model` {…}, `schedule` {…}, `seed`, `batch_size`, `context`, `boundary_every` | sha256 of canonical JSON vs chain entry 0 `recipe` |
| `*.tsr` | binary, size-limited | not parsed in v1; linked + verify command |

Loader rules taken from #178's `src/data/load.ts`: same-origin or allow-listed HF `resolve/<revision>/` URLs only, `credentials: 'omit'`, a byte cap per file (1 MB is plenty; all files < 20 KB), fatal UTF-8 decoding. Never fetches `.pt` files.

## 6. What we keep from #122 and port from #178

**From #122 (Panshul, `feat/frontend-overview`), kept as the base:**
- Deps: only `react`, `react-dom`, `react-router-dom`; dev: vite, vitest, typescript, testing-library, Playwright. No Tailwind, no animation libs.
- `src/data/contracts.ts`, `src/data/validate.ts` (extended from the generic snapshot to per-file release contracts), `src/data/adapters/snapshotAdapter.ts` (becomes `sources/static.ts`), fixtures in `src/data/fixtures/` (moved to `tests/fixtures/`).
- `src/layout/AppShell.tsx`, `StatusMark.tsx`, `ClaimStrip.tsx` (the "may claim / may not claim" strip suits the Overview), `src/inference/DisabledAdapter.ts`, `src/styles/tokens.css` + `base.css`.
- Pages `OverviewPage.tsx`, `VerifyPage.tsx` reworked onto real data; `EvidenceListPage.tsx`/`EvidenceDetailPage.tsx` become Chain and Data; `Journey.tsx`, `TryPage.tsx`, `ReleasesPage.tsx` dropped or folded into Runs.
- Tests `tests/validate.test.ts`, `tests/adapter.test.ts`.

**From #178 (adi, `feat/frontend-evidence-explorer`), ported:**
- `playwright.config.ts`, `tests/browser/frontend.spec.ts` (keyboard/skip-link/mobile-menu test, 200 % zoom + reduced-motion test, 390/1440 px overflow test, error-state tests "snapshot errors never fall back to success", digest copy test), `scripts/static-server.mjs`.
- `@axe-core/playwright` (devDependency) and the axe pattern.
- `src/data/load.ts` loader safeguards (above); `isStale` idea for a "snapshot older than N days" note.
- `@fontsource/ibm-plex-mono` + `licenses/IBM-Plex-Mono-OFL.txt` for hashes.
- `tests/contracts.test.ts` cases for unsupported schema and HTTP failure.

**Not ported:** `components/ui/globe.tsx`, `container-scroll-animation.tsx`, `spotlight-card.tsx`, `demo.tsx`, `cobe`, `framer-motion`, Tailwind/`tailwind-merge`/`clsx`, `components.json`, the extra display fonts (Bricolage, Instrument Sans, Newsreader), committed `screenshots/*.png`, `CHECKLIST.md`/`PR_HANDOFF.md`/`VALIDATION_REPORT.md`, `zod` (#122's validators already cover it).

## 7. Look and feel

Models (see `REFERENCES.md` for notes on each): https://reproduce.debian.net, https://tests.reproducible-builds.org/debian/reproducible.html, Sigstore Rekor search https://search.sigstore.dev, https://slsa.dev, plus crt.sh (https://crt.sh), OpenSSF Scorecard viewer (https://scorecard.dev/viewer/) and Hugging Face dataset cards (https://huggingface.co/docs/hub/datasets-cards). Shared traits: plain tables, monospace hashes, permalinks per record, raw data one click away, no hero animation.

**Palette (cross-checked against the reference notes, `REFERENCES.md`, same values and ratios).** Source: AOSSIE brand guide, https://github.com/AOSSIE-Org/Website/blob/main/brand/Brand.md (read from a local clone of AOSSIE-Org/Website: Golden Wallet `#FFCD00` primary, Baggy Green `#00843D` secondary, Primary Brand Dark `#282C33`, Dark Mode Surface `#0F1115`, border `#E6E6E6`, font Inter). Neutral light/dark theme + one accent (gold); green/amber/red are for status only. Contrast ratios computed with the WCAG 2.x formula:

| Role | Light | Ratio | Dark | Ratio |
|---|---|---|---|---|
| Background / text | `#FFFFFF` / `#282C33` | 14.02 | `#0F1115` / `#E6E6E6` | 15.14 |
| Muted text | `#5C6370` on white | 6.05 | `#A0A6B0` | 7.72 |
| Accent (fill: active nav, focus ring, banner rule) | `#FFCD00` with `#282C33` text on it | 9.33 | `#FFCD00` on `#0F1115` | 12.58 |
| Accent as text/link | `#7A5A00` (darkened gold) | 6.38 | `#FFCD00` | 12.58 |
| MATCH (✓ + "MATCH") | `#15803D` | 5.02 | `#4ADE80` | 10.84 |
| PENDING / NOT CHECKED (○ + "PENDING") | `#B45309` | 5.02 | `#FBBF24` | 11.32 |
| MISMATCH (✕ + "MISMATCH") | `#B91C1C` | 6.47 | `#F87171` | 6.83 |
| Border | `#E6E6E6` (decorative only, 1.25) | — | `#3A3D45` | — |

Status colours are deliberately not brand colours: Baggy Green `#00843D` (4.81 on white, 3.93 on `#0F1115`, fails in dark) is used only in the header/footer logo and accents, so "brand" is never confused with "verified". Each status always has its own colour, icon and text label. Gold on white is 1.5:1, so gold is never used for text on light backgrounds. All pairs pass AA (4.5:1) for body text; axe enforces it.

Type: Inter (AOSSIE brand font) with system fallback for text; IBM Plex Mono for hashes, commands and raw JSON.

Components: `Hash` (short form + full on expand + copy), `CopyCommand` (pre block, copy button, expected-output block), `StatusBadge` (icon + word + colour), `VerdictBanner`, `DataTable` (sortable, sticky header, row permalinks), `RawJson`, `FileError`.

**Footer (cross-checked against the reference notes, `REFERENCES.md`).** What real AOSSIE sites do:
- aossie.org ([AOSSIE-Org/Website](https://github.com/AOSSIE-Org/Website), `src/components/Footer.tsx`, `src/messages/en.json`): "© Australian Open Source Software Innovation and Education (AOSSIE)." + logo `/brand/icons/aossie_logo.svg`.
- Zplit ([AOSSIE-Org/Zplit-Website](https://github.com/AOSSIE-Org/Zplit-Website)): "© {year} AOSSIE. All rights reserved." + "Made with ♥ by AOSSIE Community" linking https://aossie.org; its hero uses "Made by" + AOSSIE link.
- Resonate ([AOSSIE-Org/Resonate-Website](https://github.com/AOSSIE-Org/Resonate-Website)): "Made with 🤍 by AOSSIE Community", AOSSIE → https://aossie.org, Other Projects → https://aossie.org/projects.
- PictoPy ([AOSSIE-Org/PictoPy-Website](https://github.com/AOSSIE-Org/PictoPy-Website)): "Made with love by AOSSIE team".

Proposal: keep #122's line "OpenVerifiableLLM is an AOSSIE project" and add "Made with ♥ by [AOSSIE](https://aossie.org) Community" (Zplit/Resonate wording) with the AOSSIE logo, then links: GitHub repo, licence (repo LICENSE), HF dataset release (pinned revision), Discord, other AOSSIE projects. Second line: "© {year} AOSSIE." Rajat to confirm (question 3).

## 8. Architecture and folder layout

```
frontend/
  src/
    data/
      contracts.ts       # per-file types, schema ids        (from #122)
      validate.ts        # one validator per file            (from #122, extended)
      load.ts            # bounded fetch                     (from #178)
      sources/hf.ts      # HF resolve/<revision>/ URLs, pinned
      sources/static.ts  # public/data/<run>/ snapshot       (from #122 snapshotAdapter)
      release.ts         # loadRelease(run) -> Release — the single adapter
      checks/chain.ts    # link + head + file hash (WebCrypto)
      runs.ts            # list of runs + revision pins (only file to edit for a new run)
    pages/               # Overview, Chain, Verify, Hardware, Data, Anchor (+ Segments, Runs)
    components/          # Hash, CopyCommand, StatusBadge, VerdictBanner, DataTable, RawJson
    layout/              # AppShell, Nav, Footer                (from #122)
    inference/           # DisabledAdapter only                (from #122)
    styles/tokens.css, base.css
  scripts/snapshot.mjs   # pull small release files into public/data/<run>/ and record sha256
  scripts/static-server.mjs                                   (from #178)
  tests/unit/  tests/e2e/  tests/fixtures/
```

Scaling to many runs: `runs.ts` lists runs with a pinned HF revision; routes are `/r/:run/...` with `/` redirecting to the latest; each run's files are fetched lazily, only for the page open. A new run is one line in `runs.ts` plus a snapshot. A new file format is a new schema id + validator; old runs keep their old validator. Large chains (thousands of entries) use a paged table (100 rows) and hash checking in a Web Worker.

## 9. Accessibility

WCAG 2.2 AA. Semantic tables with `<th scope>`, captions stating the question; status never by colour alone; skip link; visible focus ring (gold, 3 px, ≥ 3:1 against both themes); copy buttons announce "Copied" via `aria-live="polite"`; `prefers-reduced-motion` respected (there is little motion anyway); usable at 200 % zoom and 320 px width; `prefers-color-scheme` + manual toggle.

## 10. Tests and CI

- Unit (vitest): every validator with a good fixture and bad fixtures (tampered `previous`, reordered index, truncated file, wrong schema id, head ≠ anchor, file hash ≠ `release-hashes.txt`). Golden test: recomputed hashes equal all 38 published `previous` values of the real `simple-run` chain.
- Playwright, per page (one smoke + one axe each, as a table-driven spec):

| Page | Smoke assertion | axe |
|---|---|---|
| Overview | banner reads "Evidence intact" and "37/37 MATCH"; model digest starts `ec9a2b31` | 0 violations, light + dark |
| Chain | 38 rows; `/chain#37` opens entry 37 raw JSON; tampered fixture shows a broken link at that row | 0 |
| Verify | three tiers; copy button copies the exact `verify_segment.sh` command | 0 |
| Hardware | 5 rows; RTX 4090 row shows MISMATCH text | 0 |
| Data | hashes table row count = lines in `release-hashes.txt` | 0 |
| Anchor | shows `chain_head_sha256` and both TSA names | 0 |

- Ported from #178: keyboard/skip-link, 390 px + 1440 px no-overflow, 200 % zoom, error states never show success.
- CI: GitHub Actions job on `frontend/**`: `npm ci`, typecheck, unit, build, Playwright (chromium) against the static snapshot. No network in CI.

## 11. Performance budget

JS ≤ 120 KB gzip initial (React + router + app), CSS ≤ 20 KB, fonts: Inter + Plex Mono subsets, `font-display: swap`, ≤ 100 KB. LCP ≤ 2.0 s on Lighthouse mobile; per-run data < 50 KB. Static build, deployable on GitHub Pages. Budget checked in CI with a size check on `dist/`.

## 12. Split of work

- **Panshul:** data layer (`contracts.ts`, `validate.ts`, `release.ts`, `checks/chain.ts`, `runs.ts`, snapshot script), Overview, Chain, Data, Anchor, CI.
- **adi:** Playwright + axe port and the per-page spec, Verify and Hardware pages, `CopyCommand`/`DataTable` components (from his views), keyboard/zoom tests.
- Shared: design tokens and footer (review each other's). Commits carrying adi's work use `Co-authored-by: AdityasagarR123 <…>`.

## 13. Milestones (small reviewable commits, one PR)

1. Scaffold from #122; remove Journey/Try; add Playwright config + static server from #178.
2. Contracts + validators for the 7 files, fixtures, unit tests (incl. 38-hash golden test).
3. Sources + `loadRelease`; snapshot script; `public/data/simple-run/`.
4. Layout, tokens, footer, `StatusBadge`, `Hash`.
5. Overview + Chain (with smoke + axe).
6. Verify + Hardware (with smoke + axe).
7. Data + Anchor (with smoke + axe).
8. `/r/:run` routing + unlearning run.
9. CI job, size budget, README (how to add a run).

## 14. Open questions for rajat

1. Which `verifications.jsonl` format is canonical: the published lines or what `ovl.py verify` writes?
2. Read HF directly at a pinned revision, or commit a snapshot under `frontend/public/data/`? (Plan: snapshot, HF as the cited source.)
3. Footer and logo: is "Made by AOSSIE" + logo right, or is there a project-site convention you prefer?
4. Scope: `simple-run` + `unlearning-3i-atlas` only, or legacy releases too?
5. Target `main` (`frontend/`) even though `simple/` (branch `simple-production-loop`) isn't merged yet?
6. Will the "real data layout" after the release change any file names or schema ids (e.g. a `ovl.chain.v1` id on chain lines)?
7. Should independent (non-project) checks be submitted somewhere the site can read (a file in the dataset, or GitHub issues)?
8. Hosting: GitHub Pages under `aossie-org.github.io/OpenVerifiableLLM`, or elsewhere?
