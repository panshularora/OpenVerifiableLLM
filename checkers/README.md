# Checker registrations

Each file here is one checker's signed registration for check receipts, named `<key_fp>.json`.
Make one with `python simple/receipt.py register` and add it in a pull request that adds only that
file. The registration counts from the moment the pull request is merged into `main`; its checks
use the drand rounds after that time. See [docs/design/check-receipts.md](../docs/design/check-receipts.md).

Experimental: this folder is part of a prototype for discussion and is not an agreed project process.
