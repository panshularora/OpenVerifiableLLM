"""EXPERIMENTAL per-step trace comparison, on synthetic traces only (nothing writes real ones yet)."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simple"))
import bisect_trace as bt  # noqa: E402


def h(*parts):
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def trace(n=10, segment=7, start=70001, run="a", split=None, recover=None, batches=False, data_split=None, stride=1):
    """Synthetic trace; from step index `split` the state hash depends on `run`, until index `recover`."""
    rows = []
    for i in range(n):
        apart = split is not None and i >= split and (recover is None or i < recover)
        row = {"segment": segment, "step": start + i * stride, "hash": h("state", i, run if apart else "")}
        if batches:
            row["batches"] = h("batches", i, run if data_split is not None and i >= data_split else "")
            if data_split is not None and i >= data_split:
                row["hash"] = h("state", i, run)
        rows.append(row)
    return rows


def text(rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


def run(a, b):
    return bt.compare(bt.parse(text(a), "a"), bt.parse(text(b), "b"))


class TestCompare(unittest.TestCase):
    def test_identical(self):
        out = run(trace(), trace(run="b"))
        self.assertEqual(out["verdict"], "IDENTICAL")
        self.assertEqual((out["steps"], out["compared"]), ([70001, 70010], 10))
        self.assertTrue(bt.describe(out).startswith("IDENTICAL: segment 7"))

    def test_diverged_and_persists(self):
        out = run(trace(split=6), trace(run="b", split=6))
        self.assertEqual((out["verdict"], out["first_differing_row_step"], out["last_equal_step"]), ("DIVERGED", 70007, 70006))
        self.assertEqual(out["first_divergence_range"], [70007, 70007])  # stride 1: the exact step
        self.assertIn("the first divergence is at step 70007", bt.describe(out))
        self.assertEqual((out["differing_rows"], out["persists"], out["recovers_at"], out["ends_equal"]),
                         (4, True, None, False))
        self.assertIn("persists to the end", bt.describe(out))

    def test_diverged_at_the_first_step(self):
        out = run(trace(split=0), trace(run="b", split=0))
        self.assertEqual((out["first_differing_row_step"], out["last_equal_step"]), (70001, None))
        self.assertEqual(out["first_divergence_range"], [None, 70001])
        self.assertIn("at or before step 70001", bt.describe(out))

    def test_diverged_at_the_last_step(self):
        out = run(trace(split=9), trace(run="b", split=9))
        self.assertEqual((out["first_differing_row_step"], out["differing_rows"], out["persists"]), (70010, 1, True))

    def test_recovers(self):
        out = run(trace(split=3, recover=5), trace(run="b", split=3, recover=5))
        self.assertEqual((out["first_differing_row_step"], out["recovers_at"], out["persists"], out["ends_equal"]),
                         (70004, 70006, False, True))
        self.assertIn("equal again at the row for step 70006", bt.describe(out))
        self.assertIn("unexpected", bt.describe(out))

    def test_recovers_then_splits_again(self):
        a = trace(split=2, recover=4)
        b = trace(run="b", split=2, recover=4)
        b[-1]["hash"] = h("other")
        out = run(a, b)
        self.assertEqual((out["recovers_at"], out["ends_equal"], out["differing_rows"]), (70005, False, 3))
        self.assertIn("but not at the end", bt.describe(out))

    def test_data_divergence_is_named(self):
        out = run(trace(batches=True, data_split=4), trace(run="b", batches=True, data_split=4))
        self.assertEqual((out["first_differing_row_step"], out["first_fields"]), (70005, ["batches", "hash"]))
        self.assertIn("read different data", bt.describe(out))
        same_data = run(trace(batches=True, split=4), trace(run="b", batches=True, split=4))
        self.assertEqual(same_data["first_fields"], ["hash"])
        self.assertNotIn("different data", bt.describe(same_data))

    def test_stride_reports_the_range_of_the_first_divergence(self):
        a = trace(start=70100, stride=100, split=4)
        b = trace(start=70100, stride=100, run="b", split=4)
        out = run(a, b)
        self.assertEqual((out["stride"], out["first_differing_row_step"], out["last_equal_step"]), (100, 70500, 70400))
        self.assertEqual(out["first_divergence_range"], [70401, 70500])
        self.assertIn("first differing row at step 70500", bt.describe(out))
        self.assertIn("somewhere in steps 70401..70500", bt.describe(out))
        self.assertIn("rows every 100 steps", bt.describe(out))
        first = run(trace(start=70100, stride=100, split=0), trace(start=70100, stride=100, run="b", split=0))
        self.assertEqual(first["first_divergence_range"], [None, 70100])

    def test_stride_identical(self):
        out = run(trace(start=1000, stride=50), trace(start=1000, stride=50, run="b"))
        self.assertEqual((out["verdict"], out["stride"], out["steps"]), ("IDENTICAL", 50, [1000, 1450]))

    def test_single_step(self):
        self.assertEqual(run(trace(n=1), trace(n=1))["verdict"], "IDENTICAL")


class TestFailClosed(unittest.TestCase):
    def bad_parse(self, t, msg=""):
        with self.assertRaisesRegex(bt.Invalid, msg):
            bt.parse(t)

    def test_malformed_rows(self):
        good = trace(n=3)
        self.bad_parse("", "empty")
        self.bad_parse("{", "not valid JSON")
        self.bad_parse(text(good) + "\n", "blank")
        self.bad_parse("[1, 2]\n", "exactly")
        for change in ({"extra": 1}, {"hash": "A" * 64}, {"hash": "ab"}, {"step": 0}, {"step": "1"},
                       {"segment": -1}, {"segment": True}, {"batches": 5}):
            rows = [dict(r) for r in good]
            rows[1].update(change)
            with self.subTest(change=change):
                self.bad_parse(text(rows))
        rows = [dict(r) for r in good]
        del rows[2]["hash"]
        self.bad_parse(text(rows), "exactly")

    def test_steps_must_be_a_fixed_stride_and_one_segment(self):
        rows = trace(n=4)
        self.bad_parse(text([rows[0], rows[1], rows[3]]), "no gaps")  # a gap after a stride of 1
        self.bad_parse(text([rows[0], rows[0]]), "no gaps")
        self.bad_parse(text([rows[1], rows[0]]), "no gaps")
        wide = trace(n=4, start=700, stride=100)
        self.bad_parse(text([wide[0], wide[1], wide[3]]), "no gaps")  # a gap in a stride of 100
        self.bad_parse(text(trace(n=3, start=750, stride=100)), "multiples of the stride 100")
        self.bad_parse(text(trace(n=2) + trace(n=2, segment=8, start=70003)), "one segment")

    def test_fields_must_be_the_same_on_every_row(self):
        rows = trace(n=3, batches=True)
        del rows[2]["batches"]
        self.bad_parse(text(rows), "fields differ")

    def test_misaligned(self):
        for a, b in ((trace(), trace(segment=8)), (trace(), trace(start=70002)), (trace(), trace(n=9)),
                     (trace(), trace(batches=True)), (trace(start=700, stride=100), trace(start=700, stride=50)),
                     (trace(start=600, n=4, stride=200), trace(start=600, n=7, stride=100))):
            with self.subTest(b=b[0]), self.assertRaisesRegex(bt.Invalid, "misaligned"):
                run(a, b)


class TestCli(unittest.TestCase):
    def cli(self, a, b, *args):
        with tempfile.TemporaryDirectory() as tmp:
            pa, pb = Path(tmp) / "a.jsonl", Path(tmp) / "b.jsonl"
            pa.write_text(a)
            pb.write_text(b)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = bt.main([str(pa), str(pb), *args])
        return code, out.getvalue(), err.getvalue()

    def test_exit_status(self):
        self.assertEqual(self.cli(text(trace()), text(trace()))[0], 0)
        code, out, _ = self.cli(text(trace(split=2)), text(trace(run="b", split=2)), "--json")
        self.assertEqual((code, json.loads(out)["first_differing_row_step"]), (2, 70003))
        code, _, err = self.cli(text(trace()), text(trace(n=3)))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("FAIL: misaligned"))

    def test_unreadable_file_fails(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = bt.main(["/nonexistent/a.jsonl", "/nonexistent/b.jsonl"])
        self.assertEqual(code, 1)
        self.assertIn("can't read", err.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "a.jsonl"
            bad.write_bytes(b"\xff\xfe")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = bt.main([str(bad), str(bad)])
        self.assertEqual(code, 1)
        self.assertIn("can't read", err.getvalue())


if __name__ == "__main__":
    unittest.main()
