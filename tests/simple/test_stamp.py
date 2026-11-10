"""Offline checks of simple/stamp.py. No network: --check fails before certificate download."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
STAMP = ROOT / "simple" / "stamp.py"


def run_stamp(run, extra=None):
    cmd = [PYTHON, str(STAMP), str(run)]
    if extra:
        cmd.extend(extra)
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{ROOT / 'simple'}"
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


class StampCheckTests(unittest.TestCase):
    def test_check_on_empty_chain_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "chain.jsonl").write_text("")
            r = run_stamp(d, ["--check"])
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 1)
            self.assertIn("chain.jsonl", out)
            self.assertIn("no entries", out)
            self.assertNotIn("Traceback", out)

    def test_check_on_bad_json_line_reports_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "chain.jsonl").write_text('{"index": 0}\nnot-json\n')
            r = run_stamp(d, ["--check"])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("line 2", r.stderr)
            self.assertNotIn("Traceback", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
