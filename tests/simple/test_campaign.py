"""Checker campaign summary: the bound's numbers, class rules, disputes, follow-ups, and fail-closed inputs.

Reports are built in memory in the shape receipts_report.py prints; the last test builds one from
signed receipts with the World of test_receipt.py and runs it through to the table.
"""
import calendar
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simple"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign  # noqa: E402

N = 37


def fp(c):
    return c * 64


def key(f, statuses, k=1, segs=None, independent=False):
    checks = []
    for j, st in enumerate(statuses, 1):
        s = None if st in ("MISSING", "PENDING") else (segs[j - 1] if segs else list(range(j % (N - k + 1), j % (N - k + 1) + k)))
        checks.append({"j": j, "round": 1000 + j, "status": st, "segments": s,
                       "url": None if s is None else f"https://example/{f[:4]}/{j}"})
    return {"key_fp": f, "handle_claim": "someone", "independent": independent, "registered": "2026-11-09T08:30:00Z",
            "R0": 1000, "k": k, "m": len(statuses), "checks": checks}


def report(keys, disputes=(), n=N, source="github", rejected=()):
    return {"source": source, "release": {"chain_entries": n + 1}, "segments": n, "keys": keys,
            "rejected": list(rejected), "independent_match_segments": [], "disputes": list(disputes),
            "independent_match_draws": 0, "p_detect_one_forged": 0.0}


class TestBound(unittest.TestCase):
    def test_ten_single_segment_draws_of_37(self):
        b = campaign.bound(N, [1] * 10, 0.05)
        self.assertEqual(b["f_max"], 9)
        self.assertEqual(b["f_max_fraction"], 0.2432)
        self.assertAlmostEqual(b["continuous_fraction"], 0.2589, places=4)

    def test_f_max_is_the_last_f_above_alpha(self):
        self.assertGreater(campaign.miss(N, 9, [1] * 10), 0.05)
        self.assertLessEqual(campaign.miss(N, 10, [1] * 10), 0.05)

    def test_detect_one_forged_with_100_draws(self):
        self.assertAlmostEqual(campaign.p_detect(N, [1] * 100), 0.9354, places=4)
        self.assertAlmostEqual(campaign.p_detect(N, [1] * 10), 1 - (36 / 37) ** 10)

    def test_exact_hypergeometric_for_several_segments_per_check(self):
        # one check of 3 segments misses 2 forged ones with C(35,3)/C(37,3)
        self.assertEqual(campaign.miss(N, 2, [3]), campaign.Fraction(35 * 34 * 33, 37 * 36 * 35))
        self.assertEqual(campaign.miss(N, 35, [3]), 0)  # fewer than 3 honest segments left

    def test_no_checks_bounds_nothing(self):
        b = campaign.bound(N, [], 0.05)
        self.assertEqual((b["f_max"], b["continuous_fraction"], b["p_detect_one_forged"]), (N, 1.0, 0.0))

    def test_every_segment_drawn_bounds_to_zero(self):
        self.assertEqual(campaign.f_max(N, [N], 0.05), 0)

    def test_alpha_edges(self):
        # with one draw of 1 segment, miss(F) = (37 - F)/37; alpha exactly on a value is not "above" it
        F = campaign.Fraction
        self.assertEqual(campaign.f_max(N, [1], F(27, 37)), 9)
        self.assertEqual(campaign.f_max(N, [1], F(27, 37) - F(1, 10**9)), 10)
        self.assertEqual(campaign.f_max(N, [1], F(27, 37) + F(1, 10**9)), 9)
        self.assertEqual(campaign.f_max(N, [1], 1e-12), N - 1)
        for bad in (0, 1, -0.1, 1.5, float("nan")):
            with self.assertRaises(campaign.Invalid):
                campaign.campaign(report([]), {}, bad)

    def test_smaller_alpha_gives_a_looser_bound(self):
        self.assertGreaterEqual(campaign.f_max(N, [1] * 10, 0.01), campaign.f_max(N, [1] * 10, 0.05))


class TestCampaign(unittest.TestCase):
    def test_only_independent_checks_count_for_the_headline(self):
        rep = report([key(fp("a"), ["MATCH"] * 10), key(fp("b"), ["MATCH"] * 5), key(fp("c"), ["MATCH"] * 5)])
        out = campaign.campaign(rep, {fp("a"): "independent", fp("b"): "contributor", fp("c"): "project"})
        self.assertEqual(out["status"], "CLEAN")
        self.assertEqual(out["bound_independent"]["checks"], 10)
        self.assertEqual(out["bound_independent"]["f_max"], 9)
        self.assertEqual(out["bound_all_classes"]["checks"], 20)
        self.assertIn("at most 9 of 37 segments (24.3%) forged at 95% confidence", out["headline"])
        rows = {r["class"]: r for r in out["table"]}
        self.assertEqual(rows["independent"]["match_checks"], 10)
        self.assertEqual(rows["overall"]["match_checks"], 20)
        self.assertEqual(rows["overall"]["keys"], 3)

    def test_report_independent_flag_does_not_override_classes(self):
        rep = report([key(fp("a"), ["MATCH"] * 3, independent=True)])
        out = campaign.campaign(rep, {fp("a"): "contributor"})
        self.assertEqual(out["bound_independent"]["checks"], 0)
        self.assertTrue(any("classes file says contributor" in x for x in out["notes"]))

    def test_author_key_marked_independent_is_rejected(self):
        rep = report([key(fp("a"), ["MATCH"])])
        with self.assertRaisesRegex(campaign.Invalid, "release author"):
            campaign.campaign(rep, {fp("a"): "independent"}, authors=[fp("a")])
        out = campaign.campaign(rep, {fp("a"): "contributor"}, authors=[fp("a")])
        self.assertEqual(out["bound_independent"]["checks"], 0)
        with self.assertRaises(campaign.Invalid):
            campaign.campaign(rep, {}, authors=["not-a-key"])

    def test_mismatch_makes_the_campaign_disputed(self):
        rep = report([key(fp("a"), ["MATCH", "MISMATCH", "MATCH"]), key(fp("b"), ["MATCH+MISMATCH"], k=2)])
        out = campaign.campaign(rep, {fp("a"): "independent", fp("b"): "contributor"})
        self.assertEqual(out["status"], "DISPUTED")
        self.assertEqual([(d["key_fp"], d["j"]) for d in out["disputes"]], [(fp("a"), 2), (fp("b"), 1)])
        self.assertEqual(out["bound_independent"]["checks"], 2)  # the mismatched check counts toward nothing
        self.assertTrue(campaign.render(out).startswith("campaign status: DISPUTED\nMISMATCH disputes:"))

    def test_report_disputes_alone_make_it_disputed(self):
        out = campaign.campaign(report([], disputes=[4]), {})
        self.assertEqual(out["status"], "DISPUTED")
        self.assertEqual(out["disputed_segments"], [4])

    def test_follow_ups_and_pending(self):
        rep = report([key(fp("a"), ["MATCH", "ABANDONED", "MISSING", "WRONG_STACK", "ERROR+MATCH", "PENDING"])])
        out = campaign.campaign(rep, {fp("a"): "independent"})
        self.assertEqual(out["status"], "CLEAN")
        self.assertEqual([f["status"] for f in out["follow_up"]], ["ABANDONED", "MISSING", "WRONG_STACK", "ERROR+MATCH"])
        row = {r["class"]: r for r in out["table"]}["independent"]
        self.assertEqual((row["match_checks"], row["abandoned"], row["missing"], row["incomplete"], row["pending"]),
                         (1, 1, 1, 2, 1))
        self.assertIn("not recomputed", campaign.render(out))

    def test_headline_names_independent_drop_outs(self):
        rep = report([key(fp("a"), ["MATCH"] * 10 + ["ABANDONED", "MISSING", "PENDING"]),
                      key(fp("b"), ["MATCH", "ABANDONED", "ERROR"])])
        out = campaign.campaign(rep, {fp("a"): "independent", fp("b"): "contributor"})
        b = out["bound_independent"]
        self.assertEqual((b["f_max"], b["dropped"]), (9, 2))  # b's drop-outs are not independent; PENDING isn't one
        self.assertEqual(b["dropped_by_status"], {"abandoned": 1, "missing": 1, "incomplete": 0})
        self.assertIn("at most 9 of 37 segments (24.3%) forged at 95% confidence, from 10 independent MATCH "
                      "checks; assumes the 2 abandoned, missing or incomplete independent checks (1 abandoned, "
                      "1 missing, 0 incomplete) were unrelated to their drawn segments", out["headline"])
        self.assertIn("a drop-out can hide a forged one", out["headline"])
        line = next(x for x in campaign.render(out).splitlines() if x.startswith("bound (independent"))
        self.assertIn("assumes the 2 abandoned, missing or incomplete", line)  # on the headline line itself

    def test_headline_counts_incomplete_and_singular(self):
        rep = report([key(fp("a"), ["MATCH", "WRONG_STACK"])])
        out = campaign.campaign(rep, {fp("a"): "independent"})
        self.assertEqual(out["bound_independent"]["dropped"], 1)
        self.assertIn("the 1 abandoned, missing or incomplete independent check (0 abandoned, 0 missing, "
                      "1 incomplete) were", out["headline"])

    def test_headline_without_drop_outs_says_so(self):
        out = campaign.campaign(report([key(fp("a"), ["MATCH"] * 3)]), {fp("a"): "independent"})
        self.assertEqual(out["bound_independent"]["dropped"], 0)
        self.assertIn("no independent check was abandoned, missing or incomplete", out["headline"])

    def test_unclassified_keys_are_listed_and_count_nowhere(self):
        rep = report([key(fp("a"), ["MATCH"] * 4), key(fp("d"), ["MATCH"] * 4)])
        out = campaign.campaign(rep, {fp("a"): "independent"})
        self.assertEqual(out["unclassified"], [fp("d")])
        self.assertEqual(out["bound_independent"]["checks"], 4)
        self.assertEqual(out["bound_all_classes"]["checks"], 4)
        self.assertIn("unclassified keys", campaign.render(out))

    def test_covered_counts_distinct_segments(self):
        rep = report([key(fp("a"), ["MATCH"] * 3, segs=[[5], [5], [6]])])
        out = campaign.campaign(rep, {fp("a"): "independent"})
        row = {r["class"]: r for r in out["table"]}["independent"]
        self.assertEqual((row["covered"], row["match_draws"]), (2, 3))
        self.assertEqual(out["bound_independent"]["checks"], 3)  # repeated draws are still independent draws

    def test_empty_report(self):
        out = campaign.campaign(report([]), {})
        self.assertEqual(out["status"], "CLEAN")
        self.assertEqual(out["bound_independent"]["f_max"], N)
        self.assertIn("at most 37 of 37 segments (100.0%)", out["headline"])
        self.assertIn("CLEAN means no MISMATCH", campaign.render(out))

    def test_mirror_and_rejected_are_flagged(self):
        out = campaign.campaign(report([], source="mirror", rejected=[{"url": "u", "reason": "r"}]), {})
        text = campaign.render(out)
        self.assertIn("--posts mirror", text)
        self.assertIn("1 rejected posts", text)


class TestFailClosed(unittest.TestCase):
    def bad(self, rep, classes=None, msg=""):
        with self.assertRaisesRegex(campaign.Invalid, msg):
            campaign.campaign(rep, classes or {})

    def test_unknown_report_schema(self):
        good = report([key(fp("a"), ["MATCH"])])
        for change in ({"schema": "v9"}, {"keys": None}):
            self.bad(good | change)
        missing = dict(good)
        del missing["disputes"]
        self.bad(missing, msg="unknown report schema")
        self.bad([], msg="unknown report schema")
        for f, v in (("segments", 0), ("segments", True), ("source", "elsewhere"), ("disputes", [37])):
            self.bad(good | {f: v})

    def test_bad_key_and_check_entries(self):
        def broken(change):
            rep = report([key(fp("a"), ["MATCH", "MATCH"])])
            change(rep["keys"][0])
            return rep
        self.bad(broken(lambda k: k.update(extra=1)), msg="key entry")
        self.bad(broken(lambda k: k.update(key_fp="A" * 64)), msg="bad key_fp")
        self.bad(broken(lambda k: k.update(m=3)))
        self.bad(broken(lambda k: k.update(k=0)))
        self.bad(broken(lambda k: k["checks"][1].update(j=1)), msg="check schema")
        self.bad(broken(lambda k: k["checks"][0].update(status="OK")))
        self.bad(broken(lambda k: k["checks"][0].update(status="MISMATCH+MATCH")))  # not as the report sorts it
        self.bad(broken(lambda k: k["checks"][0].update(segments=[40])))
        self.bad(broken(lambda k: k["checks"][0].update(segments=[1, 2])))
        self.bad(broken(lambda k: k["checks"][0].update(segments=None)), msg="without its segments")

    def test_duplicate_key_in_report(self):
        a = key(fp("a"), ["MATCH"])
        self.bad(report([a, copy.deepcopy(a)]), msg="listed twice")

    def test_malformed_classes(self):
        rep = report([])
        self.bad(rep, ["independent"], msg="JSON object")
        self.bad(rep, {"abc": "independent"}, msg="bad key_fp")
        self.bad(rep, {fp("a"): "friend"}, msg="class")
        self.bad(rep, {fp("a"): None})


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, rep_text, classes_text, *args):
        (self.tmp / "r.json").write_text(rep_text)
        (self.tmp / "c.json").write_text(classes_text)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = campaign.main([str(self.tmp / "r.json"), "--classes", str(self.tmp / "c.json"), *args])
        return code, out.getvalue(), err.getvalue()

    def test_duplicate_keys_in_classes_file_fail(self):
        text = '{"%s": "contributor", "%s": "independent"}' % (fp("a"), fp("a"))
        code, _, err = self.run_cli(json.dumps(report([])), text)
        self.assertEqual(code, 1)
        self.assertIn("FAIL: classes: duplicate key", err)

    def test_bad_json_and_missing_file_fail(self):
        code, _, err = self.run_cli("{", "{}")
        self.assertEqual((code, err.startswith("FAIL: report: not valid JSON")), (1, True))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = campaign.main([str(self.tmp / "nope.json"), "--classes", str(self.tmp / "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("can't read", err.getvalue())

    def test_exit_status_and_json(self):
        rep = report([key(fp("a"), ["MATCH"] * 10)])
        code, out, _ = self.run_cli(json.dumps(rep), json.dumps({fp("a"): "independent"}), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["bound_independent"]["f_max"], 9)
        rep["keys"][0]["checks"][0]["status"] = "MISMATCH"
        code, out, _ = self.run_cli(json.dumps(rep), json.dumps({fp("a"): "independent"}))
        self.assertEqual(code, 2)
        self.assertIn("DISPUTED", out)
        code, _, err = self.run_cli(json.dumps(rep), "{}", "--alpha", "1")
        self.assertEqual(code, 1)
        self.assertIn("alpha", err)

    def test_author_flag(self):
        code, _, err = self.run_cli(json.dumps(report([])), json.dumps({fp("a"): "independent"}),
                                    "--author", fp("a"))
        self.assertEqual(code, 1)
        self.assertIn("release author", err)


class TestEndToEnd(unittest.TestCase):
    """Signed receipts -> receipts_report.report -> campaign table."""

    def test_receipts_to_table(self):
        import receipt
        import test_receipt as tr
        with tempfile.TemporaryDirectory() as tmp:
            w = tr.World(tmp)
            a, _ = w.key("a")
            b, _ = w.key("b")
            fa = receipt.fingerprint(receipt.public_hex(receipt.load_key(a)))
            fb = receipt.fingerprint(receipt.public_hex(receipt.load_key(b)))
            ra = w.register(a, m=2)
            rb = w.register(b, m=2, when="2026-11-09T08:30:01Z")
            a1, _ = w.outcome(ra, a, 1)
            w.outcome(ra, a, 2, previous=a1, abandon=True)
            w.outcome(rb, b, 1)
            import receipts_report
            rep = receipts_report.report(list(w.posts), receipt.Posts(w.posts), w.release, w.anchor_time, {fb},
                                         calendar.timegm((2026, 11, 13, 12, 0, 0)), 72 * 3600, lambda r: tr.SIG[r])
            rep = json.loads(json.dumps(rep))  # as read back from the printed JSON
            out = campaign.campaign(rep, {fa: "independent", fb: "contributor"})
        self.assertEqual(out["status"], "CLEAN")
        rows = {r["class"]: r for r in out["table"]}
        self.assertEqual((rows["independent"]["match_checks"], rows["independent"]["abandoned"]), (1, 1))
        self.assertEqual((rows["contributor"]["match_checks"], rows["contributor"]["missing"]), (1, 1))
        self.assertEqual(out["bound_independent"]["checks"], 1)  # b is flagged independent in the report, not here
        self.assertEqual(out["bound_independent"]["f_max"], 35)  # (37 - F)/37 > 0.05 up to F = 35
        self.assertEqual(out["bound_all_classes"]["checks"], 2)
        self.assertEqual([f["status"] for f in out["follow_up"]], ["ABANDONED", "MISSING"])
        self.assertIsNotNone(out["follow_up"][1]["segments"])
        self.assertIn("campaign status: CLEAN", campaign.render(out))


if __name__ == "__main__":
    unittest.main()
