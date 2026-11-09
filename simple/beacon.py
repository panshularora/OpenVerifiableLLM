"""Public randomness from the drand quicknet beacon, verified offline.

A check's segment must come from randomness nobody chose. drand quicknet publishes a threshold BLS
signature every 3 seconds; the round's randomness is SHA-256 of that signature. quicknet is unchained,
so one round verifies on its own against the pinned group public key, with no earlier rounds.

  python simple/beacon.py ROUND        # fetch ROUND, verify it, print its randomness
  python simple/beacon.py --at UNIX_T  # print the round current at time t and the first round after t

Rounds are pinned to the chain hash below; a relay that serves anything else fails verification.
"""
import argparse
import functools
import hashlib
import json
import sys
import urllib.request

CHAIN_HASH = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
PUBLIC_KEY = ("83cf0f2896adee7eb8b5f01fcad3912212c437e0073e911fb90022d3e760183c8c4b450b6a0a6c3ac6a5776a2d1064510d"
              "1fec758c921cc22b0e17e63aaf4bcb5ed66304de9cf809bd274ca73bab4af5a6e9c76a4bc09e76eae8991ef5ece45a")
GENESIS = 1692803367
PERIOD = 3
DST = b"BLS_SIG_BLS12381G1_XMD:SHA-256_SSWU_RO_NUL_"  # scheme bls-unchained-g1-rfc9380
RELAYS = ("https://api.drand.sh", "https://drand.cloudflare.com")


def round_at(t):
    """The round current at unix time t (drand's CurrentRound); 0 before genesis."""
    return 0 if t < GENESIS else int((t - GENESIS) // PERIOD) + 1


def next_round(t):
    """The first round that starts strictly after unix time t."""
    return round_at(t) + 1


def round_time(r):
    """Unix time at which round r starts."""
    return GENESIS + (r - 1) * PERIOD


@functools.lru_cache(maxsize=256)
def verify_round(r, signature_hex):
    """Return the round's randomness (32 bytes) if signature_hex is quicknet's signature for round r.

    Raises ValueError otherwise. The check is the BLS pairing e(G2, sig) == e(pk, H(sha256(r as u64 BE))).
    """
    try:  # imported here so the rest of the tooling works without the BLS dependency installed
        from py_ecc.bls.hash_to_curve import hash_to_G1
        from py_ecc.bls.point_compression import decompress_G1, decompress_G2
        from py_ecc.optimized_bls12_381 import G2, is_inf, pairing
    except ImportError as e:
        raise ValueError("py_ecc is needed to verify drand rounds: pip install --require-hashes "
                         "-r requirements/preparation.lock") from e
    if not isinstance(r, int) or r < 1:
        raise ValueError(f"bad round {r!r}")
    try:
        sig = bytes.fromhex(signature_hex)
    except (TypeError, ValueError):
        raise ValueError("signature is not hex") from None
    if len(sig) != 48:
        raise ValueError(f"signature is {len(sig)} bytes, expected a 48-byte compressed G1 point")
    pk = bytes.fromhex(PUBLIC_KEY)
    try:
        s = decompress_G1(int.from_bytes(sig, "big"))
        p = decompress_G2((int.from_bytes(pk[:48], "big"), int.from_bytes(pk[48:], "big")))
    except Exception as e:  # py_ecc raises plain ValueError/AssertionError for off-curve points
        raise ValueError(f"signature is not a valid G1 point: {e}") from None
    if is_inf(s):
        raise ValueError("signature is the point at infinity")
    h = hash_to_G1(hashlib.sha256(r.to_bytes(8, "big")).digest(), DST, hashlib.sha256)
    if pairing(G2, s) != pairing(p, h):
        raise ValueError(f"signature does not verify for round {r}")
    return hashlib.sha256(sig).digest()


def fetch_round(r, relays=RELAYS, timeout=20):
    """Fetch round r from the first relay that answers; returns {"round", "signature"}. Never verifies."""
    errors = []
    for relay in relays:
        try:
            with urllib.request.urlopen(f"{relay}/{CHAIN_HASH}/public/{r}", timeout=timeout) as f:
                body = json.loads(f.read())
            if body.get("round") != r:
                raise ValueError(f"relay answered round {body.get('round')}")
            return {"round": r, "signature": body["signature"]}
        except Exception as e:  # try the next relay; the caller decides what a total failure means
            errors.append(f"{relay}: {e}")
    raise OSError(f"could not fetch drand round {r}: " + "; ".join(errors))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("round", nargs="?", type=int)
    p.add_argument("--at", type=float, help="unix time")
    a = p.parse_args(argv)
    if a.at is not None:
        print(json.dumps({"round_at": round_at(a.at), "next_round": next_round(a.at)}))
        return 0
    if a.round is None:
        p.error("give a ROUND or --at")
    got = fetch_round(a.round)
    try:
        rand = verify_round(a.round, got["signature"])
    except ValueError as e:
        print(f"round {a.round}: INVALID ({e})", file=sys.stderr)
        return 1
    print(json.dumps({"round": a.round, "signature": got["signature"], "randomness": rand.hex()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
