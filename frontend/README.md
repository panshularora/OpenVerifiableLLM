# OpenVerifiableLLM frontend

Static public record. It does **not** train, sign, or chat.

Node 20.19+ or 22.12+.

```sh
cd frontend
npm install
npm run dev
npm run test
npm run typecheck
npm run build
npx playwright install chromium   # once
npm run e2e
```

Dev: http://localhost:5173 — HashRouter (`/#/evidence`).

## Snapshot packs

The app never scrapes `goal_state.json`. Default load is the public-shaped pack.

| `?snapshot=` | What it is |
| --- | --- |
| *(default)* `public` | Project-shaped fixture (not an approved public snapshot): G01/G02-like PASS, models **not released**, full replay NOT_RUN |
| `missing-parent` | Test: child names a missing parent |
| `superseded` | Test: old FAIL stays after a later PASS |
| `empty` | Test: zero checks — shown as an error |

Example: `/#/?snapshot=missing-parent`  
Test packs are not linked in the public footer.

## This UI will not claim

- Models are available
- G02 reconstruction, full replay, or independent audit passed
- `locallyRecomputed` is true
- A copyable CLI
- Chat answers come from a released OpenVerifiableLLM model

## Maintainer still needs to provide

- Approved public snapshot + git revision (M3)
- Tested verify commands
- GitHub Pages `base` path
- Inference backend after a real release

Do not edit `src/` (pipeline), keys, or `project/` from this app.
