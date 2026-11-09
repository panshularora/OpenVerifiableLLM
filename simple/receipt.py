"""Signed check receipts with segments picked by public randomness (roadmap item 6, format v0).

Today a check result is a pasted line, and the checker picks the segment. A receipt instead is signed
with the checker's Ed25519 key, and its segment comes from a drand round that the checker can't choose:

  1. keygen    make a key; its fingerprint key_fp is SHA-256 of the raw public key
  2. register  sign a registration that binds the key to one release anchor and declares k
               (segments per check) and m (number of checks). Post it as a GitHub issue comment.
  3. schedule  R0 is the first drand round after the comment's server-side created_at; check j uses
               round R0 + j. The registration must be posted after the release anchor's RFC 3161 time.
  4. derive    segments for check j from round R0 + j, the chain head, key_fp and j. Anyone can
               recompute them, whether or not the checker reports them.
  5. sign      after `verify_segment.sh`, sign the result as an in-toto Statement in a DSSE envelope;
     abandon   or sign an ABANDONED notice. Post one outcome per j, in order, with no gaps.
  6. verify    re-derive everything from public data and fail closed on any difference.

  python simple/receipt.py keygen --out ~/.ovl/checker.key
  python simple/receipt.py register --anchor RUN/anchor.json --key ~/.ovl/checker.key --k 1 --m 3
  python simple/receipt.py schedule --registration URL --anchor RUN/anchor.json
  python simple/receipt.py derive --registration URL --anchor RUN/anchor.json --j 1 [--quiet]
  python simple/receipt.py sign --registration URL --anchor RUN/anchor.json --j 1 --key KEY \\
        --report RUN/local-verifications.jsonl [--previous URL_OF_OUTCOME_J-1]
  python simple/receipt.py abandon --registration URL --anchor RUN/anchor.json --j 1 --key KEY --reason TEXT
  python simple/receipt.py verify URL_OF_OUTCOME --anchor RUN/anchor.json

Posts are read from the GitHub REST API (set GITHUB_TOKEN to raise the rate limit), or from a JSON
mirror given with --posts that maps each URL to the API's {created_at, updated_at, body}.
Trust assumptions: the drand quicknet threshold, and GitHub's server clock for created_at. A signature
shows who made a claim, not that they ran the check or that they are independent of the project.
See docs/receipts.md.
"""
import argparse
import base64
import calendar
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beacon  # noqa: E402

REGISTRATION_TYPE = "application/vnd.ovllm.checker-registration.v0+json"
STATEMENT_TYPE = "application/vnd.in-toto+json"
REGISTRATION_SCHEMA = "ovllm.checker-registration/v0"
RECEIPT_SCHEMA = "ovllm.check-receipt/v0"
PREDICATE_TYPE = "https://github.com/AOSSIE-Org/OpenVerifiableLLM/check-receipt/v0"
SELECTION = "ovllm-select-v0"
OUTCOMES = ("MATCH", "MISMATCH", "WRONG_STACK", "ERROR")  # plus ABANDONED, only via `abandon`
MAX_M = 64
TIME_SOURCE = "github.issue_comment.created_at"
COMMENT_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)#issuecomment-(\d+)$")


class Invalid(Exception):
    """A receipt or registration that must be rejected."""


# ---------- encoding and signatures ----------

def canonical(obj):
    """RFC 8785 canonical JSON; used only for content ids, never for signing (DSSE signs raw bytes)."""
    import rfc8785
    return rfc8785.dumps(obj)


def envelope_id(env):
    return hashlib.sha256(canonical(env)).hexdigest()


def pae(payload_type, payload):
    """DSSE v1 pre-authentication encoding."""
    t = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(t), t, len(payload), payload)


def load_key(path):
    """A raw 32-byte Ed25519 seed, the key-file layout of the earlier src/signing.py (#92), so a
    PyNaCl SigningKey file works here unchanged."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = Path(path).expanduser().read_bytes()
    if len(raw) != 32:
        raise Invalid(f"{path} is not a raw 32-byte Ed25519 key")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_hex(key):
    return key.public_key().public_bytes_raw().hex()


def fingerprint(public_key_hex):
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()


def seal(key, payload_type, body):
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    sig = key.sign(pae(payload_type, payload))
    return {"payloadType": payload_type, "payload": base64.b64encode(payload).decode(),
            "signatures": [{"keyid": fingerprint(public_hex(key)), "sig": base64.b64encode(sig).decode()}]}


def open_envelope(env, payload_type, public_key_hex):
    """Verify a single-signature DSSE envelope and return its decoded payload."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(env, dict) or set(env) != {"payloadType", "payload", "signatures"}:
        raise Invalid("not a DSSE envelope")
    if env["payloadType"] != payload_type:
        raise Invalid(f"payloadType is {env['payloadType']!r}, expected {payload_type!r}")
    sigs = env["signatures"]
    if not isinstance(sigs, list) or len(sigs) != 1 or not isinstance(sigs[0], dict):
        raise Invalid("expected exactly one signature")
    if sigs[0].get("keyid") != fingerprint(public_key_hex):
        raise Invalid("signature keyid is not the registered key")
    try:
        payload = base64.b64decode(env["payload"], validate=True)
        sig = base64.b64decode(sigs[0]["sig"], validate=True)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(sig, pae(payload_type, payload))
    except (InvalidSignature, ValueError, TypeError) as e:
        raise Invalid(f"bad signature: {e or 'does not verify'}") from None
    try:
        body = json.loads(payload)
    except ValueError:
        raise Invalid("payload is not JSON") from None
    if not isinstance(body, dict):
        raise Invalid("payload is not a JSON object")
    return body


def unverified_payload(env):
    try:
        return json.loads(base64.b64decode(env["payload"], validate=True))
    except Exception:
        raise Invalid("envelope payload is not base64 JSON") from None


# ---------- the release ----------

def gentime(text):
    """Unix time from openssl's 'Oct 20 14:38:43 2026 GMT' (fractional seconds allowed)."""
    m = re.fullmatch(r"(\w{3}) +(\d+) (\d\d):(\d\d):(\d\d)(?:\.\d+)? (\d{4}) GMT", text.strip())
    if not m:
        raise Invalid(f"unreadable timestamp time {text!r}")
    mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].index(m[1]) + 1
    t = calendar.timegm((int(m[6]), mon, int(m[2]), int(m[3]), int(m[4]), int(m[5])))
    return t + 1 if "." in text else t  # round a fractional time up, so "strictly after" stays strict


def release_of(anchor_path, anchor_time=None):
    """The release a registration binds to, plus the anchor's RFC 3161 time (the latest of both TSAs).

    Both timestamps must cover the anchor's exact bytes; anchor_time skips that check (tests, offline)."""
    path = Path(anchor_path)
    data = path.read_bytes()
    a = json.loads(data)
    if not isinstance(a, dict) or not {"chain_entries", "chain_head_sha256"} <= a.keys():
        raise Invalid(f"{path} is not an anchor file")
    if anchor_time is None:
        import stamp
        times = []
        with tempfile.TemporaryDirectory() as d:
            snapshot = Path(d) / "anchor.json"
            snapshot.write_bytes(data)
            certs = stamp.freetsa_certs(d)
            for name in stamp.TSA:
                tsr = path.with_name(f"{path.stem}.{name}.tsr")
                if not tsr.exists():
                    raise Invalid(f"timestamp {tsr} not found")
                ok, when = stamp.verify_reply(tsr, name, ["-data", str(snapshot)], certs)
                if not ok:
                    raise Invalid(f"timestamp {tsr} does not cover {path}")
                times.append(gentime(when))
        anchor_time = max(times)
    release = {"dataset": a.get("dataset"), "run_path": a.get("path"), "anchor_sha256": hashlib.sha256(data).hexdigest(),
               "chain_entries": a["chain_entries"], "chain_head_sha256": a["chain_head_sha256"]}
    return release, anchor_time


# ---------- public posts ----------

def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def parse_iso(s):
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        raise Invalid(f"bad timestamp {s!r}") from None


class Posts:
    """Reads public posts (GitHub issue comments). mirror maps URL -> the API's comment JSON."""

    def __init__(self, mirror=None, fetch=None):
        self.mirror, self.fetch = mirror, fetch or self._api

    @staticmethod
    def _api(url):
        owner, repo, _, cid = COMMENT_URL.match(url).groups()
        req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{cid}",
                                     headers={"Accept": "application/vnd.github+json"})
        if os.environ.get("GITHUB_TOKEN"):
            req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
        with urllib.request.urlopen(req, timeout=30) as f:
            return json.loads(f.read())

    def get(self, url):
        """Return (envelope, post time). Only an unedited issue comment counts as published."""
        m = COMMENT_URL.match(url or "")
        if not m:
            raise Invalid(f"{url!r} is not a GitHub issue comment URL")
        if self.mirror is not None:
            if url not in self.mirror:
                raise Invalid(f"{url} is not in the posts mirror")
            c = self.mirror[url]
        else:
            try:
                c = self.fetch(url)
            except Exception as e:
                raise Invalid(f"could not read {url}: {e}") from None
        if not str(c.get("issue_url", "")).endswith(f"/repos/{m[1]}/{m[2]}/issues/{m[3]}") and "issue_url" in c:
            raise Invalid(f"{url}: comment belongs to another issue")
        created = parse_iso(c.get("created_at"))
        if c.get("updated_at", c.get("created_at")) != c.get("created_at"):
            # created_at survives edits, so an edited comment could carry an envelope chosen after its rounds
            raise Invalid(f"{url} was edited after it was posted; post a new comment instead")
        return extract(c.get("body") or "", url), created


def extract(body, where):
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)\n```", body, re.S) or [body]
    found = []
    for b in blocks:
        try:
            obj = json.loads(b)
        except ValueError:
            continue
        if isinstance(obj, dict) and "payloadType" in obj:
            found.append(obj)
    if len(found) != 1:
        raise Invalid(f"{where}: expected exactly one DSSE envelope, found {len(found)}")
    return found[0]


# ---------- schedule and selection ----------

def seed_for(randomness, chain_head_hex, key_fp_hex, j):
    return hashlib.sha256(b"OVLLM-check-seed-v0" + randomness + bytes.fromhex(chain_head_hex)
                          + bytes.fromhex(key_fp_hex) + j.to_bytes(8, "big")).digest()


def draw(seed, n, ctr):
    """Uniform integer in [0, n) by rejection sampling over 64-bit words; returns (value, next counter)."""
    limit = (1 << 64) - ((1 << 64) % n)
    while True:
        x = int.from_bytes(hashlib.sha256(b"OVLLM-check-select-v0" + seed + ctr.to_bytes(4, "big")).digest()[:8], "big")
        ctr += 1
        if x < limit:
            return x % n, ctr


def select(seed, n, k):
    """k distinct segments out of n by a partial Fisher-Yates shuffle with unbiased draws, sorted."""
    pool, ctr, out = list(range(n)), 0, []
    for _ in range(k):
        i, ctr = draw(seed, len(pool), ctr)
        out.append(pool.pop(i))
    return sorted(out)


class Registration:
    def __init__(self, url, posts, release, anchor_time):
        self.url = url
        env, self.time = posts.get(url)
        claimed = unverified_payload(env)
        pk = claimed.get("public_key") if isinstance(claimed, dict) else None
        if not isinstance(pk, str) or not re.fullmatch(r"[0-9a-f]{64}", pk):
            raise Invalid("registration has no 32-byte public_key")
        body = open_envelope(env, REGISTRATION_TYPE, pk)
        want = {"_type", "key_fp", "public_key", "release", "k", "m", "beacon", "handle_claim"}
        if set(body) != want or body["_type"] != REGISTRATION_SCHEMA:
            raise Invalid("registration does not match format v0")
        if body["key_fp"] != fingerprint(pk):
            raise Invalid("registration key_fp is not SHA-256 of its public key")
        if body["release"] != release:
            raise Invalid("registration is for a different release anchor")
        if body["beacon"] != {"network": "drand/quicknet", "chain_hash": beacon.CHAIN_HASH}:
            raise Invalid("registration names a different beacon")
        n = release["chain_entries"] - 1
        for f, hi in (("k", n), ("m", MAX_M)):
            if type(body[f]) is not int or not 1 <= body[f] <= hi:
                raise Invalid(f"registration {f} must be an integer in 1..{hi}")
        if not self.time > anchor_time:  # R2: after the anchor, so the training was committed first
            raise Invalid(f"registration posted {iso(self.time)}, not after the anchor time {iso(anchor_time)}")
        self.body, self.sha256, self.release, self.n = body, envelope_id(env), release, n
        self.key_fp, self.public_key, self.k, self.m = body["key_fp"], pk, body["k"], body["m"]
        self.R0 = beacon.next_round(self.time)  # R3

    def round(self, j):
        if type(j) is not int or not 1 <= j <= self.m:
            raise Invalid(f"j={j!r} is outside 1..m={self.m}")
        return self.R0 + j

    def segments(self, j, signature_hex):
        r = self.round(j)
        try:
            rand = beacon.verify_round(r, signature_hex)
        except ValueError as e:
            raise Invalid(f"drand round {r}: {e}") from None
        return select(seed_for(rand, self.release["chain_head_sha256"], self.key_fp, j), self.n, self.k)

    def ref(self):
        return {"url": self.url, "sha256": self.sha256, "time_source": TIME_SOURCE, "time": iso(self.time)}


# ---------- outcomes ----------

def statement(reg, j, signature_hex, results, previous, extra):
    predicate = {
        "schema": RECEIPT_SCHEMA, "release": reg.release,
        "challenge": {"registration": reg.ref(), "R0": reg.R0, "j": j, "m": reg.m, "round": reg.round(j),
                      "previous_outcome": previous, "beacon_chain_hash": beacon.CHAIN_HASH,
                      "signature": signature_hex, "selection": SELECTION, "k": reg.k},
        "results": results, "checker": {"key_fp": reg.key_fp}, **extra}
    return {"_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "anchor.json", "digest": {"sha256": reg.release["anchor_sha256"]}},
                        {"name": "chain-head", "digest": {"sha256": reg.release["chain_head_sha256"]}}],
            "predicateType": PREDICATE_TYPE, "predicate": predicate}


def previous_ref(reg, j, url, posts):
    """Outcome j-1 by the same key under the same registration (R1: no gaps)."""
    if j == 1:
        if url:
            raise Invalid("check 1 has no previous outcome")
        return None
    if not url:
        raise Invalid(f"check {j} needs the URL of outcome {j - 1} (no gaps are allowed)")
    env, _ = posts.get(url)
    p = open_envelope(env, STATEMENT_TYPE, reg.public_key).get("predicate", {})
    ch = p.get("challenge", {}) if isinstance(p, dict) else {}
    if ch.get("j") != j - 1 or ch.get("registration", {}).get("sha256") != reg.sha256:
        raise Invalid(f"{url} is not outcome {j - 1} of this registration")
    return {"url": url, "sha256": envelope_id(env)}


def results_from_report(line, segments, outcome=None):
    """Map one local-verifications.jsonl line (written by `ovl.py verify`) onto the drawn segments."""
    report = json.loads(line)
    got = {tuple(c["segment"]): c["match"] for c in report.get("checks", []) if "segment" in c}
    want = {(s, s + 1) for s in segments}
    if set(got) != want:
        raise Invalid(f"report checked segments {sorted(s for s, _ in got)}, but the draw is {segments}")
    res = [{"segment": [s, s + 1], "outcome": outcome or ("MATCH" if got[(s, s + 1)] is True else "MISMATCH")}
           for s in segments]
    return res, report


def verify(url, posts, release, anchor_time, rounds=None):
    """Check one published outcome (§ Step 4 of docs/receipts.md). Returns its predicate or raises Invalid."""
    env, posted = posts.get(url)
    claimed = unverified_payload(env)
    p = claimed.get("predicate") if isinstance(claimed, dict) else None
    ch = p.get("challenge") if isinstance(p, dict) else None
    if not isinstance(ch, dict) or not isinstance(ch.get("registration"), dict):
        raise Invalid("not a check receipt")
    reg = Registration(ch["registration"].get("url"), posts, release, anchor_time)  # 3, R2
    st = open_envelope(env, STATEMENT_TYPE, reg.public_key)  # 1
    p = st.get("predicate")
    if st.get("_type") != "https://in-toto.io/Statement/v1" or st.get("predicateType") != PREDICATE_TYPE \
            or not isinstance(p, dict) or p.get("schema") != RECEIPT_SCHEMA:
        raise Invalid("not an ovllm check receipt v0")
    if st.get("subject") != statement(reg, 1, "", [], None, {})["subject"] or p.get("release") != release:
        raise Invalid("receipt is bound to a different release")  # 2
    ch = p.get("challenge", {})
    if p.get("checker") != {"key_fp": reg.key_fp}:
        raise Invalid("receipt's checker is not the registered key")
    if ch.get("registration") != reg.ref():
        raise Invalid("receipt's registration reference does not match the published registration")
    j = ch.get("j")
    if ch.get("m") != reg.m or ch.get("k") != reg.k or ch.get("selection") != SELECTION \
            or ch.get("beacon_chain_hash") != beacon.CHAIN_HASH:
        raise Invalid("receipt's k, m, selection or beacon differ from the registration")
    if ch.get("R0") != reg.R0 or ch.get("round") != reg.round(j):  # 4 (range), 5
        raise Invalid(f"round must be R0 + j = {reg.R0} + {j}")
    prev = ch.get("previous_outcome")
    if previous_ref(reg, j, prev.get("url") if isinstance(prev, dict) else None, posts) != prev:  # 4, R1
        raise Invalid(f"previous_outcome does not match the published outcome {j - 1}")
    signature = ch.get("signature")
    if rounds is not None and str(reg.round(j)) in rounds and rounds[str(reg.round(j))] != signature:
        raise Invalid("drand signature differs from the recorded round")
    segments = reg.segments(j, signature)  # 6
    if not posted > beacon.round_time(reg.round(j)):  # 7: can't report a draw before it existed
        raise Invalid("receipt was posted before its drand round existed")
    res = p.get("results")
    if not isinstance(res, list) or [r.get("segment") for r in res if isinstance(r, dict)] != [[s, s + 1] for s in segments]:
        raise Invalid(f"results must cover exactly the drawn segments {segments}")  # 8
    kinds = {r.get("outcome") for r in res}
    if not (kinds <= set(OUTCOMES) or kinds == {"ABANDONED"}):  # 9
        raise Invalid(f"unknown or mixed outcomes {sorted(map(str, kinds))}")
    return p | {"_posted": posted, "_url": url, "_key_fp": reg.key_fp, "_registration": reg.url}


# ---------- CLI ----------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("keygen"); s.add_argument("--out", required=True)
    s = sub.add_parser("register"); s.add_argument("--key", required=True)
    s.add_argument("--k", type=int, default=1); s.add_argument("--m", type=int, default=1)
    s.add_argument("--handle", help="optional GitHub/Discord handle: a claim, not proof of independence")
    for name in ("schedule", "derive", "sign", "abandon", "verify"):
        s = sub.add_parser(name)
        if name == "verify":
            s.add_argument("url", help="GitHub issue comment URL of the outcome")
        else:
            s.add_argument("--registration", required=True, help="GitHub issue comment URL of the registration")
        if name in ("derive", "sign", "abandon"):
            s.add_argument("--j", type=int, required=True)
        if name in ("sign", "abandon"):
            s.add_argument("--key", required=True); s.add_argument("--previous")
        if name == "derive":
            s.add_argument("--quiet", action="store_true")
        if name == "sign":
            s.add_argument("--report", required=True, help="local-verifications.jsonl from ovl.py verify")
            s.add_argument("--outcome", choices=("WRONG_STACK", "ERROR"))
            s.add_argument("--code-commit", default="")
        if name == "abandon":
            s.add_argument("--reason", required=True)
    for s in sub.choices.values():
        if s.prog.split()[-1] != "keygen":
            s.add_argument("--anchor", required=True, help="the release's anchor.json (with its .tsr files)")
            s.add_argument("--anchor-time", type=int, help="skip the anchor timestamp check and use this unix time")
            s.add_argument("--posts", help="JSON mirror of the GitHub comments, instead of the live API")
            s.add_argument("--rounds", help="JSON {round: signature} of drand rounds, instead of fetching")
    a = p.parse_args(argv)
    try:
        return run(a)
    except Invalid as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


def run(a):
    if a.cmd == "keygen":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        out = Path(a.out).expanduser()
        if out.exists():
            raise Invalid(f"{out} exists; refusing to overwrite a key")
        out.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # secret: owner-only, never overwritten
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes_raw())
        out.with_name(out.name + ".pub").write_bytes(key.public_key().public_bytes_raw())
        print(json.dumps({"key_fp": fingerprint(public_hex(key)), "public_key": public_hex(key)}))
        return 0
    if a.anchor_time is not None:
        print("WARNING: --anchor-time: the anchor's RFC 3161 timestamps are not checked", file=sys.stderr)
    release, anchor_time = release_of(a.anchor, a.anchor_time)
    if a.cmd == "register":
        key = load_key(a.key)
        n = release["chain_entries"] - 1
        if not (1 <= a.k <= n and 1 <= a.m <= MAX_M):
            raise Invalid(f"k must be in 1..{n} and m in 1..{MAX_M}")
        body = {"_type": REGISTRATION_SCHEMA, "key_fp": fingerprint(public_hex(key)), "public_key": public_hex(key),
                "release": release, "k": a.k, "m": a.m,
                "beacon": {"network": "drand/quicknet", "chain_hash": beacon.CHAIN_HASH}, "handle_claim": a.handle}
        print(json.dumps(seal(key, REGISTRATION_TYPE, body), indent=1))
        return 0
    posts = Posts(json.loads(Path(a.posts).read_text()) if a.posts else None)
    rounds = json.loads(Path(a.rounds).read_text()) if a.rounds else None

    def signature(r):
        if rounds is not None:
            if str(r) not in rounds:
                raise Invalid(f"drand round {r} is not in {a.rounds}")
            return rounds[str(r)]
        try:
            return beacon.fetch_round(r)["signature"]
        except OSError as e:  # never fall back to local randomness
            raise Invalid(str(e)) from None

    if a.cmd == "verify":
        out = verify(a.url, posts, release, anchor_time, rounds)
        print(json.dumps({"verdict": "VALID", "key_fp": out["_key_fp"], "j": out["challenge"]["j"],
                          "m": out["challenge"]["m"], "round": out["challenge"]["round"], "results": out["results"]}))
        return 0
    reg = Registration(a.registration, posts, release, anchor_time)
    if a.cmd == "schedule":
        print(json.dumps({"key_fp": reg.key_fp, "registered": iso(reg.time), "R0": reg.R0, "k": reg.k, "m": reg.m,
                          "rounds": {j: {"round": reg.round(j), "available": iso(beacon.round_time(reg.round(j)))}
                                     for j in range(1, reg.m + 1)}}))
        return 0
    r = reg.round(a.j)
    if time.time() < beacon.round_time(r):
        raise Invalid(f"round {r} starts at {iso(beacon.round_time(r))}; it doesn't exist yet")
    sig = signature(r)
    segments = reg.segments(a.j, sig)
    if a.cmd == "derive":
        print(" ".join(map(str, segments)) if a.quiet else json.dumps({"j": a.j, "round": r, "segments": segments}))
        return 0
    key = load_key(a.key)
    if public_hex(key) != reg.public_key:
        raise Invalid("this key is not the registered key")
    prev = previous_ref(reg, a.j, a.previous, posts)
    if a.cmd == "abandon":
        st = statement(reg, a.j, sig, [{"segment": [s, s + 1], "outcome": "ABANDONED"} for s in segments], prev,
                       {"reason": a.reason, "finished": iso(time.time())})
    else:
        lines = Path(a.report).read_text(encoding="utf-8").splitlines()
        if not lines:
            raise Invalid(f"{a.report} is empty")
        results, report = results_from_report(lines[-1], segments, a.outcome)
        st = statement(reg, a.j, sig, results, prev, {
            "code": {"repo": "AOSSIE-Org/OpenVerifiableLLM", "commit": a.code_commit},
            "stack": report.get("environment"), "final_state_digest": report.get("chain_head"),
            "local_report_sha256": hashlib.sha256(lines[-1].encode()).hexdigest(),
            "started": iso(report.get("time", 0)), "finished": iso(time.time())})
    print(json.dumps(seal(key, STATEMENT_TYPE, st), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
