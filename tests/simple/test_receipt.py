"""Check receipts (roadmap item 6): honest receipts verify, and every tampering or skipped rule fails.

Offline: drand rounds are real quicknet rounds recorded in fixtures/drand_quicknet_rounds.json, the
release anchors are the published main-run and exclusion-fork anchor.json files, and GitHub comments
come from an in-memory mirror shaped like the API's JSON: merged pull requests for registrations,
issue comments for outcomes.
"""
import base64
import calendar
import collections
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simple"))
import beacon  # noqa: E402
import receipt  # noqa: E402
import receipts_report  # noqa: E402
import stamp  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
ROUNDS = json.loads((FIX / "drand_quicknet_rounds.json").read_text())
SIG = {int(r): v["signature"] for r, v in ROUNDS["rounds"].items()}
MAIN_ANCHOR, FORK_ANCHOR = FIX / "simple-run-anchor.json", FIX / "fork-anchor.json"
MAIN_TIME = calendar.timegm((2026, 10, 20, 14, 38, 43))  # both TSAs on the main anchor
FORK_TIME = calendar.timegm((2026, 10, 31, 14, 28, 0))   # both TSAs on the fork anchor
REG_TIME = "2026-11-09T08:30:00Z"
REPO = "https://github.com/AOSSIE-Org/OpenVerifiableLLM/issues/9999#issuecomment-"
PULL = "https://github.com/AOSSIE-Org/OpenVerifiableLLM/pull/"


def resign(env, key, change):
    """Re-sign an envelope after change(statement): a well-formed lie, not a broken signature."""
    body = json.loads(base64.b64decode(env["payload"]))
    change(body)
    return receipt.seal(key, env["payloadType"], body)


class World:
    """One release, keys, and a mirror of GitHub comments."""

    def __init__(self, tmp, anchor=MAIN_ANCHOR, anchor_time=MAIN_TIME):
        self.tmp, self.anchor, self.anchor_time = Path(tmp), anchor, anchor_time
        self.release, _ = receipt.release_of(anchor, anchor_time)
        self.posts, self.n = {}, 0
        self.rounds = self.tmp / "rounds.json"
        self.rounds.write_text(json.dumps({str(r): s for r, s in SIG.items()}))

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = receipt.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def common(self):
        return ["--anchor", self.anchor, "--anchor-time", self.anchor_time,
                "--posts", self.tmp / "posts.json", "--rounds", self.rounds]

    def key(self, name="a"):
        path = self.tmp / f"{name}.key"
        code, out, _ = self.cli("keygen", "--out", path)
        assert code == 0
        return path, receipt.load_key(path)

    def post(self, env, when, edited=None):
        self.n += 1
        url = f"{REPO}{self.n}"
        self.posts[url] = {"created_at": when, "updated_at": edited or when,
                           "issue_url": "https://api.github.com/repos/AOSSIE-Org/OpenVerifiableLLM/issues/9999",
                           "body": "Check receipt\n\n```json\n" + json.dumps(env, indent=1) + "\n```\n"}
        self.save()
        return url

    def save(self):
        (self.tmp / "posts.json").write_text(json.dumps(self.posts))

    def merge(self, env, when, **change):
        """A pull request adding checkers/<keyid>.json, merged into main at `when`."""
        self.n += 1
        url = f"{PULL}{self.n}"
        name = f"checkers/{env['signatures'][0]['keyid']}.json"
        self.posts[url] = {"merged": True, "merged_at": when, "base_ref": "main",
                           "files": [{"filename": name, "status": "added"}], "content": json.dumps(env, indent=1)} | change
        self.save()
        return url

    def registration_envelope(self, keypath, k=1, m=3):
        code, out, err = self.cli("register", "--key", keypath, "--k", k, "--m", m, *self.common()[:4])
        assert code == 0, err
        return json.loads(out)

    def register(self, keypath, k=1, m=3, when=REG_TIME):
        return self.merge(self.registration_envelope(keypath, k, m), when)

    def report(self, segments, match=True):
        path = self.tmp / "local-verifications.jsonl"
        line = {"time": 1794000000.0, "environment": {"gpu": "NVIDIA GeForce RTX 5090", "torch": "2.8.0"},
                "chain_head": "f" * 64, "checks": [{"segment": [s, s + 1], "match": match} for s in segments],
                "result": "PASS" if match else "FAIL"}
        path.write_text(json.dumps(line) + "\n")
        return path

    def derive(self, reg, j):
        code, out, err = self.cli("derive", "--registration", reg, "--j", j, "--quiet", *self.common())
        assert code == 0, err
        return [int(x) for x in out.split()]

    def outcome(self, reg, keypath, j, previous=None, match=True, when="2026-11-09T09:00:00Z", abandon=False):
        prev = ["--previous", previous] if previous else []
        if abandon:
            code, out, err = self.cli("abandon", "--registration", reg, "--j", j, "--key", keypath,
                                      "--reason", "pod out of memory", *prev, *self.common())
        else:
            rep = self.report(self.derive(reg, j), match)
            code, out, err = self.cli("sign", "--registration", reg, "--j", j, "--key", keypath,
                                      "--report", rep, *prev, *self.common())
        assert code == 0, err
        return self.post(json.loads(out), when), json.loads(out)

    def verify(self, url):
        return self.cli("verify", url, *self.common())


class TestBeacon(unittest.TestCase):
    def test_recorded_rounds_verify_and_randomness_is_hash_of_signature(self):
        for r, v in ROUNDS["rounds"].items():
            self.assertEqual(beacon.verify_round(int(r), v["signature"]).hex(), v["randomness"])

    def test_signature_for_another_round_fails(self):
        with self.assertRaises(ValueError):
            beacon.verify_round(1001, SIG[1000])

    def test_flipped_bit_and_garbage_fail(self):
        bad = bytearray.fromhex(SIG[1000]); bad[-1] ^= 1
        for s in (bytes(bad).hex(), "00" * 48, "zz", SIG[1000][:-2]):
            with self.assertRaises(ValueError):
                beacon.verify_round(1000, s)

    def test_round_mapping_at_the_anchor_times(self):
        self.assertEqual(beacon.next_round(MAIN_TIME), 33234587)
        # the fork anchor time is exactly the start of round 33,551,172, so the first round after it is 33,551,173
        self.assertEqual(beacon.round_time(33551172), FORK_TIME)
        self.assertEqual(beacon.round_at(FORK_TIME), 33551172)
        self.assertEqual(beacon.next_round(FORK_TIME), 33551173)
        self.assertEqual(beacon.round_at(beacon.GENESIS - 1), 0)

    def test_gentime_parsing(self):
        self.assertEqual(receipt.gentime("Oct 20 14:38:43 2026 GMT"), MAIN_TIME)
        self.assertEqual(receipt.gentime("Oct 31 14:28:00.5 2026 GMT"), FORK_TIME + 1)
        with self.assertRaises(receipt.Invalid):
            receipt.gentime("?")


class TestSelection(unittest.TestCase):
    def test_known_answer(self):
        seed = receipt.seed_for(bytes(32), "00" * 32, "11" * 32, 1)
        self.assertEqual(receipt.select(seed, 37, 1), receipt.select(seed, 37, 1))
        three = receipt.select(seed, 37, 3)
        self.assertEqual(len(set(three)), 3)
        self.assertTrue(all(0 <= s < 37 for s in three))
        self.assertNotEqual(receipt.seed_for(bytes(32), "00" * 32, "11" * 32, 2), seed)

    def test_uniform(self):
        n, t = 37, 37000
        counts = collections.Counter(receipt.select(receipt.seed_for(bytes(32), "00" * 32, f"{i:064x}", 1), n, 1)[0]
                                     for i in range(t))
        chi2 = sum((counts[s] - t / n) ** 2 / (t / n) for s in range(n))
        self.assertLess(chi2, 59.0)  # 36 df, critical value at p = 0.01 is about 58.6


class TestReceipts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.w = World(self._tmp.name)
        self.keypath, self.key = self.w.key("a")
        self.reg = self.w.register(self.keypath)

    def tearDown(self):
        self._tmp.cleanup()

    def assertFails(self, url, text):
        code, out, err = self.w.verify(url)
        self.assertEqual(code, 1, out)
        self.assertIn(text, err)

    def test_keygen_is_owner_only_and_never_overwrites(self):
        self.assertEqual(os.stat(self.keypath).st_mode & 0o777, 0o600)
        self.assertEqual(len(self.keypath.with_name("a.key.pub").read_bytes()), 32)
        code, _, err = self.w.cli("keygen", "--out", self.keypath)
        self.assertEqual(code, 1)
        self.assertIn("refusing", err)

    def test_keygen_refuses_a_git_work_tree(self):
        repo = self.w.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        for out in (repo / "a.key", repo / "keys" / "a.key"):
            code, _, err = self.w.cli("keygen", "--out", out)
            self.assertEqual(code, 1)
            self.assertIn("git work tree", err)
            self.assertFalse(out.exists())
        code, _, _ = self.w.cli("keygen", "--out", repo / "a.key", "--allow-in-repo")
        self.assertEqual(code, 0)

    def test_missing_key_file_fails_cleanly(self):
        rep = self.w.report(self.w.derive(self.reg, 1))
        code, _, err = self.w.cli("sign", "--registration", self.reg, "--j", 1, "--key", self.w.tmp / "nope.key",
                                  "--report", rep, *self.w.common())
        self.assertEqual(code, 1)
        self.assertIn("FAIL: can't read key", err)

    def test_schedule_is_registration_round_plus_j(self):
        code, out, _ = self.w.cli("schedule", "--registration", self.reg, *self.w.common())
        s = json.loads(out)
        self.assertEqual(s["R0"], ROUNDS["R0"])
        self.assertEqual([v["round"] for v in s["rounds"].values()], [ROUNDS["R0"] + j for j in (1, 2, 3)])

    def test_honest_sequence_verifies(self):
        u1, _ = self.w.outcome(self.reg, self.keypath, 1)
        u2, _ = self.w.outcome(self.reg, self.keypath, 2, previous=u1, match=False)
        for url, kind in ((u1, "MATCH"), (u2, "MISMATCH")):
            code, out, err = self.w.verify(url)
            self.assertEqual(code, 0, err)
            self.assertEqual({r["outcome"] for r in json.loads(out)["results"]}, {kind})
            self.assertIn("receipts_report.py", json.loads(out)["first_registration"])

    def test_altered_payload_fails_signature(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1, match=False)
        body = json.loads(base64.b64decode(env["payload"]))
        body["predicate"]["results"][0]["outcome"] = "MATCH"
        env["payload"] = base64.b64encode(json.dumps(body).encode()).decode()
        self.assertFails(self.w.post(env, "2026-11-09T09:01:00Z"), "bad signature")

    def test_truncated_and_retyped_envelopes_fail(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        self.assertFails(self.w.post({k: v for k, v in env.items() if k != "signatures"}, "2026-11-09T09:01:00Z"),
                         "not a DSSE envelope")
        self.assertFails(self.w.post(env | {"payloadType": "application/json"}, "2026-11-09T09:01:00Z"),
                         "payloadType")

    def test_other_key_fails(self):
        _, other = self.w.key("b")
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        self.assertFails(self.w.post(resign(env, other, lambda b: None), "2026-11-09T09:01:00Z"), "keyid")

    def test_sign_refuses_an_unregistered_key(self):
        other, _ = self.w.key("b")
        rep = self.w.report(self.w.derive(self.reg, 1))
        code, _, err = self.w.cli("sign", "--registration", self.reg, "--j", 1, "--key", other, "--report", rep,
                                  *self.w.common())
        self.assertEqual(code, 1)
        self.assertIn("not the registered key", err)

    def schedule_fails(self, reg, text):
        code, _, err = self.w.cli("schedule", "--registration", reg, *self.w.common())
        self.assertEqual(code, 1)
        self.assertIn(text, err)

    def test_registration_signed_by_another_key_fails(self):
        _, other = self.w.key("b")
        env, _ = receipt.Posts(self.w.posts).registration(self.reg)
        forged = resign(env, other, lambda b: None)  # other key signs a body naming key a
        reg2 = self.w.merge(forged, REG_TIME)
        code, _, err = self.w.cli("schedule", "--registration", reg2, *self.w.common())
        self.assertEqual(code, 1)
        self.assertIn("keyid", err)

    def test_registration_not_after_the_anchor_fails(self):
        for anchor, t in ((MAIN_ANCHOR, MAIN_TIME), (FORK_ANCHOR, FORK_TIME)):
            w = World(self._tmp.name, anchor, t)
            kp, _ = w.key(f"k{t}")
            for when, ok in ((t, False), (t - 60, False), (t + 1, True)):
                reg = w.register(kp, when=receipt.iso(when))
                code, _, err = w.cli("schedule", "--registration", reg, *w.common())
                self.assertEqual(code == 0, ok, err)

    def test_registration_for_another_release_fails(self):
        fork = World(self._tmp.name, FORK_ANCHOR, FORK_TIME)
        code, _, err = fork.cli("schedule", "--registration", self.reg, "--anchor", FORK_ANCHOR,
                                "--anchor-time", FORK_TIME, "--posts", self.w.tmp / "posts.json")
        self.assertEqual(code, 1)
        self.assertIn("different release", err)

    def test_registration_as_an_issue_comment_fails(self):
        # a comment can be deleted after its draws are known and posted again: the grinding hole
        env, _ = receipt.Posts(self.w.posts).registration(self.reg)
        self.schedule_fails(self.w.post(env, REG_TIME), "not a GitHub pull request URL")

    def test_unmerged_or_off_main_registration_fails(self):
        env = self.w.registration_envelope(self.keypath)
        self.schedule_fails(self.w.merge(env, REG_TIME, merged=False, merged_at=None), "is not merged")
        self.schedule_fails(self.w.merge(env, REG_TIME, base_ref="dev"), "not main")

    def test_registration_pull_request_must_add_only_its_file(self):
        env = self.w.registration_envelope(self.keypath)
        name = f"checkers/{env['signatures'][0]['keyid']}.json"
        for files in ([{"filename": name, "status": "modified"}],
                      [{"filename": name, "status": "added"}, {"filename": "simple/ovl.py", "status": "modified"}],
                      [{"filename": "checkers/" + "0" * 64 + ".json", "status": "added"}],
                      [{"filename": "keys/a.json", "status": "added"}], []):
            code, _, err = self.w.cli("schedule", "--registration", self.w.merge(env, REG_TIME, files=files),
                                      *self.w.common())
            self.assertEqual(code, 1)
            self.assertRegex(err, "checkers/|not the key_fp")

    def test_registration_file_named_for_another_key_fails(self):
        _, other = self.w.key("b")
        env = self.w.registration_envelope(self.keypath)
        name = "checkers/" + receipt.fingerprint(receipt.public_hex(other)) + ".json"
        self.schedule_fails(self.w.merge(env, REG_TIME, files=[{"filename": name, "status": "added"}]),
                            "not the key_fp")

    def test_registration_content_must_be_one_envelope(self):
        env = self.w.registration_envelope(self.keypath)
        for content in ("not json", json.dumps([env]), json.dumps(env | {"signatures": []}), None):
            self.schedule_fails(self.w.merge(env, REG_TIME, content=content), "registration file")

    def test_registry_repository_is_pinned(self):
        url = self.reg.replace("AOSSIE-Org/OpenVerifiableLLM", "someone/OpenVerifiableLLM")
        self.w.posts[url] = self.w.posts[self.reg]
        self.w.save()
        self.schedule_fails(url, "is not in AOSSIE-Org/OpenVerifiableLLM")
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        u = self.w.post(env, "2026-11-09T09:01:00Z").replace("AOSSIE-Org", "someone")
        self.w.posts[u] = self.w.posts[f"{REPO}{self.w.n}"]
        self.w.save()
        code, _, err = self.w.verify(u)
        self.assertIn("is not in AOSSIE-Org/OpenVerifiableLLM", err)

    def test_mirror_use_is_flagged(self):
        u1, _ = self.w.outcome(self.reg, self.keypath, 1)
        code, out, err = self.w.verify(u1)
        self.assertEqual(json.loads(out)["source"], "mirror")
        self.assertIn("only as trustworthy as whoever wrote the mirror", err)

    def test_live_reader_maps_the_github_api(self):
        env = self.w.registration_envelope(self.keypath)
        name = f"checkers/{env['signatures'][0]['keyid']}.json"
        api = "https://api.github.com/repos/AOSSIE-Org/OpenVerifiableLLM"
        replies = {f"{api}/pulls/7": {"merged": True, "merged_at": REG_TIME, "base": {"ref": "main"},
                                      "merge_commit_sha": "abc"},
                   f"{api}/pulls/7/files?per_page=100&page=1": [{"filename": name, "status": "added"}],
                   f"{api}/contents/{name}?ref=abc": json.dumps(env)}
        with mock.patch.object(receipt.Posts, "_get", side_effect=lambda u, raw=False: replies[u]):
            got, t = receipt.Posts().registration(PULL + "7")
        self.assertEqual((got, t), (env, receipt.parse_iso(REG_TIME)))

    def test_registration_k_and_m_bounds(self):
        for k, m in ((0, 1), (38, 1), (1, 0), (1, receipt.MAX_M + 1)):
            code, _, _ = self.w.cli("register", "--key", self.keypath, "--k", k, "--m", m, *self.w.common()[:4])
            self.assertEqual(code, 1)

    def test_wrong_round_fails(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        for r in (ROUNDS["R0"], ROUNDS["R0"] + 2):  # one earlier, and the round of j = 2
            def move(b, r=r):
                b["predicate"]["challenge"]["round"] = r
                b["predicate"]["challenge"]["signature"] = SIG[r]
            self.assertFails(self.w.post(resign(env, self.key, move), "2026-11-09T09:01:00Z"), "R0 + j")

    def test_wrong_R0_fails(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        def shift(b):
            b["predicate"]["challenge"]["R0"] -= 1
        self.assertFails(self.w.post(resign(env, self.key, shift), "2026-11-09T09:01:00Z"), "R0 + j")

    def test_forged_drand_signature_fails(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        def forge(b):
            s = bytearray.fromhex(b["predicate"]["challenge"]["signature"]); s[-1] ^= 1
            b["predicate"]["challenge"]["signature"] = bytes(s).hex()
        url = self.w.post(resign(env, self.key, forge), "2026-11-09T09:01:00Z")
        self.assertFails(url, "differs from the recorded round")
        # without a recorded copy, the BLS check itself rejects it
        with self.assertRaisesRegex(receipt.Invalid, "drand round"):
            receipt.verify(url, receipt.Posts(self.w.posts), self.w.release, MAIN_TIME, rounds=None)

    def test_altered_segment_fails(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        def move(b):
            s = b["predicate"]["results"][0]["segment"][0]
            b["predicate"]["results"][0]["segment"] = [(s + 1) % 37, (s + 1) % 37 + 1]
        self.assertFails(self.w.post(resign(env, self.key, move), "2026-11-09T09:01:00Z"), "drawn segments")

    def test_posted_before_its_round_fails(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        early = receipt.iso(beacon.round_time(ROUNDS["R0"] + 1) - 1)
        self.assertFails(self.w.post(env, early), "before its drand round")

    def test_wrong_release_binding_fails(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        def swap(b):
            b["subject"][0]["digest"]["sha256"] = "0" * 64
        self.assertFails(self.w.post(resign(env, self.key, swap), "2026-11-09T09:01:00Z"), "different release")

    def test_report_for_other_segments_is_refused(self):
        seg = self.w.derive(self.reg, 1)
        rep = self.w.report([(seg[0] + 1) % 37])
        code, _, err = self.w.cli("sign", "--registration", self.reg, "--j", 1, "--key", self.keypath,
                                  "--report", rep, *self.w.common())
        self.assertEqual(code, 1)
        self.assertIn("the draw is", err)

    def test_gaps_and_counter_rules(self):
        u1, _ = self.w.outcome(self.reg, self.keypath, 1)
        # j = 2 without its previous outcome
        code, _, err = self.w.cli("abandon", "--registration", self.reg, "--j", 2, "--key", self.keypath,
                                  "--reason", "x", *self.w.common())
        self.assertIn("needs the URL of outcome 1", err)
        # j = 3 pointing at outcome 1 (skipping 2)
        code, _, err = self.w.cli("abandon", "--registration", self.reg, "--j", 3, "--key", self.keypath,
                                  "--reason", "x", "--previous", u1, *self.w.common())
        self.assertIn("is not outcome 2", err)
        # j beyond m, and j = 0
        for j in (4, 0):
            code, _, err = self.w.cli("derive", "--registration", self.reg, "--j", j, *self.w.common())
            self.assertIn("outside 1..m", err)
        # j = 1 claiming a previous outcome
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        def claim(b):
            b["predicate"]["challenge"]["previous_outcome"] = {"url": u1, "sha256": "0" * 64}
        self.assertFails(self.w.post(resign(env, self.key, claim), "2026-11-09T09:01:00Z"), "no previous outcome")

    def test_previous_outcome_hash_must_match(self):
        u1, _ = self.w.outcome(self.reg, self.keypath, 1)
        _, env = self.w.outcome(self.reg, self.keypath, 2, previous=u1)
        def wrong(b):
            b["predicate"]["challenge"]["previous_outcome"]["sha256"] = "0" * 64
        self.assertFails(self.w.post(resign(env, self.key, wrong), "2026-11-09T09:02:00Z"), "previous_outcome")

    def test_abandoned_notice_verifies_with_its_segment(self):
        u1, _ = self.w.outcome(self.reg, self.keypath, 1, abandon=True)
        code, out, err = self.w.verify(u1)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["results"][0]["segment"][0], self.w.derive(self.reg, 1)[0])

    def test_mixed_abandoned_and_match_fails(self):
        w = World(self._tmp.name)
        kp, key = w.key("c")
        reg = w.register(kp, k=2, m=1)
        _, env = w.outcome(reg, kp, 1)
        def mix(b):
            b["predicate"]["results"][1]["outcome"] = "ABANDONED"
        url = w.post(resign(env, key, mix), "2026-11-09T09:01:00Z")
        code, _, err = w.verify(url)
        self.assertEqual(code, 1)
        self.assertIn("mixed", err)

    def test_edited_outcome_fails(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        self.assertFails(self.w.post(env, "2026-11-09T09:01:00Z", edited="2026-11-09T10:00:00Z"), "edited")

    def test_two_envelopes_in_one_comment_fail(self):
        _, env = self.w.outcome(self.reg, self.keypath, 1)
        url = self.w.post(env, "2026-11-09T09:01:00Z")
        self.w.posts[url]["body"] += "\n```json\n" + json.dumps(env) + "\n```\n"
        self.w.save()
        self.assertFails(url, "exactly one DSSE envelope")

    def test_non_comment_url_fails(self):
        self.assertFails("https://github.com/AOSSIE-Org/OpenVerifiableLLM/commit/abc", "not a GitHub issue comment")


class TestReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.w = World(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_report(self, independent=(), now=None):
        posts = receipt.Posts(self.w.posts)
        return receipts_report.report(list(self.w.posts), posts, self.w.release, self.w.anchor_time,
                                      set(independent), now or calendar.timegm((2026, 11, 9, 12, 0, 0)),
                                      72 * 3600, lambda r: SIG[r])

    def test_gap_missing_abandoned_duplicate_and_coverage(self):
        a, _ = self.w.key("a")
        b, _ = self.w.key("b")
        fa = receipt.fingerprint(receipt.public_hex(receipt.load_key(a)))
        fb = receipt.fingerprint(receipt.public_hex(receipt.load_key(b)))
        ra = self.w.register(a, m=3)
        rb = self.w.register(b, m=3, when="2026-11-09T08:30:01Z")  # same R0 (3 s rounds), different key_fp
        a1, _ = self.w.outcome(ra, a, 1)
        a2, _ = self.w.outcome(ra, a, 2, previous=a1, abandon=True)
        self.w.outcome(ra, a, 2, previous=a1)  # a second outcome for j = 2 is rejected, the first counts
        b1, _ = self.w.outcome(rb, b, 1)
        out = self.run_report(independent={fa})
        seq = {k["key_fp"]: [c["status"] for c in k["checks"]] for k in out["keys"]}
        self.assertEqual(seq[fa], ["MATCH", "ABANDONED", "PENDING"])
        self.assertEqual(seq[fb], ["MATCH", "PENDING", "PENDING"])
        self.assertEqual(len([r for r in out["rejected"] if "second outcome" in r["reason"]]), 1)
        self.assertEqual(out["independent_match_draws"], 1)  # b's MATCH is not independent
        self.assertEqual(out["independent_match_segments"], self.w.derive(ra, 1))
        # after the 72 h deadline, a's j = 3 and b's j = 2, 3 become MISSING, with their recomputed segments
        late = self.run_report(independent={fa}, now=calendar.timegm((2026, 11, 13, 12, 0, 0)))
        checks = {k["key_fp"]: k["checks"] for k in late["keys"]}
        self.assertEqual([c["status"] for c in checks[fb]], ["MATCH", "MISSING", "MISSING"])
        self.assertEqual(checks[fb][1]["segments"], self.w.derive(rb, 2))
        self.assertEqual(round(late["p_detect_one_forged"], 4), round(1 / 37, 4))

    def test_skipped_check_cannot_be_papered_over(self):
        a, key = self.w.key("a")
        ra = self.w.register(a, m=3)
        a1, _ = self.w.outcome(ra, a, 1)
        rep = self.w.report(self.w.derive(ra, 2))
        code, out, err = self.w.cli("sign", "--registration", ra, "--j", 2, "--key", a, "--report", rep,
                                    "--previous", a1, *self.w.common())
        e2 = json.loads(out)  # j = 2 is signed but never posted
        seg3 = self.w.derive(ra, 3)

        def point(b):
            c = b["predicate"]["challenge"]
            c.update(j=3, round=ROUNDS["R0"] + 3, signature=SIG[ROUNDS["R0"] + 3],
                     previous_outcome={"url": REPO + "424242", "sha256": receipt.envelope_id(e2)})
            b["predicate"]["results"] = [dict(b["predicate"]["results"][0], segment=[seg3[0], seg3[0] + 1])]
        bad = self.w.post(resign(e2, key, point), "2026-11-09T09:05:00Z")
        code, _, err = self.w.verify(bad)
        self.assertEqual(code, 1)
        self.assertIn("not in the posts mirror", err)  # live, GitHub returns 404 for the unposted comment
        out = self.run_report(now=calendar.timegm((2026, 11, 13, 12, 0, 0)))
        self.assertEqual([c["status"] for c in out["keys"][0]["checks"]], ["MATCH", "MISSING", "MISSING"])
        self.assertTrue(any(r["url"] == bad for r in out["rejected"]))

    def test_dispute_and_second_registration(self):
        a, _ = self.w.key("a")
        b, _ = self.w.key("b")
        ra = self.w.register(a, k=37, m=1)
        rb = self.w.register(b, k=37, m=1)
        self.w.register(a, k=1, m=1, when="2026-11-09T08:40:00Z")  # can't re-register to get a new R0
        self.w.outcome(ra, a, 1, match=True)
        self.w.outcome(rb, b, 1, match=False)
        out = self.run_report(independent=set())
        self.assertEqual(out["disputes"], list(range(37)))
        self.assertTrue(any("already registered" in r["reason"] for r in out["rejected"]))

    def test_re_registration_after_deleting_the_file_does_not_reset_the_schedule(self):
        a, _ = self.w.key("a")
        first = self.w.register(a, m=2)
        env = json.loads(self.w.posts[first]["content"])
        name = self.w.posts[first]["files"][0]["filename"]
        deletion = self.w.merge(env, "2026-11-09T08:30:03Z", files=[{"filename": name, "status": "removed"}])
        second = self.w.merge(env, "2026-11-09T08:30:06Z")  # same file added again, a later merged_at
        late, _ = self.w.outcome(second, a, 1, when="2026-11-09T09:00:00Z")
        out = self.run_report(now=calendar.timegm((2026, 11, 13, 12, 0, 0)))
        self.assertEqual(len(out["keys"]), 1)
        self.assertEqual(out["keys"][0]["R0"], ROUNDS["R0"])
        self.assertEqual([c["status"] for c in out["keys"][0]["checks"]], ["MISSING", "MISSING"])
        reasons = {r["url"]: r["reason"] for r in out["rejected"]}
        self.assertIn("checkers/", reasons[deletion])
        self.assertIn("already registered", reasons[second])
        self.assertIn("doesn't count", reasons[late])

    def test_report_marks_its_source(self):
        self.assertEqual(self.run_report()["source"], "mirror")


class TestStampDigest(unittest.TestCase):
    def test_needs_a_sha256_digest(self):
        with self.assertRaises(ValueError):
            stamp.stamp_digest("/nonexistent", "x", b"short", "x")

    def test_existing_replies_are_kept_and_no_request_is_made(self):
        with tempfile.TemporaryDirectory() as d:
            for name in stamp.TSA:
                (Path(d) / f"receipt-ab.{name}.tsr").write_bytes(b"kept")
            with mock.patch.object(stamp.urllib.request, "urlopen") as urlopen:
                stamp.stamp_digest(d, "receipt-ab", bytes(32), "receipt ab")
            urlopen.assert_not_called()
            self.assertEqual({p.read_bytes() for p in Path(d).iterdir()}, {b"kept"})


if __name__ == "__main__":
    unittest.main()
