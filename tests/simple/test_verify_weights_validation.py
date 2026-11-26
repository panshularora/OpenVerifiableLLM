"""Regression tests for ovl.py verify --weights state-dict validation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import torch

os.environ.setdefault("OVL_DEVICE", "cpu")
os.environ.setdefault("OVL_TIMESTAMP", "0")

ROOT = Path(__file__).resolve().parents[2]


def _make_run(tmp_path):
    e0 = {"index": 0, "kind": "initial", "state": "0" * 64, "model": "0" * 64, "previous": None}
    h0 = hashlib.sha256(json.dumps(e0, sort_keys=True).encode()).hexdigest()
    e1 = {"index": 1, "kind": "final", "state": "1" * 64, "model": "1" * 64, "previous": h0}
    (tmp_path / "chain.jsonl").write_text(json.dumps(e0) + "\n" + json.dumps(e1) + "\n", encoding="utf-8")


def test_verify_weights_rejects_checkpoint_dict_cleanly(tmp_path):
    _make_run(tmp_path)
    ckpt = tmp_path / "ckpt-00001.pt"
    torch.save({"control": {"phase": "wikipedia"}, "model": {"w": torch.zeros(2)}, "opt": {}}, ckpt)

    env = {**os.environ, "OVL_DEVICE": "cpu", "OVL_TIMESTAMP": "0"}
    res = subprocess.run(
        [sys.executable, str(ROOT / "simple" / "ovl.py"), "verify",
         "--run", str(tmp_path), "--no-anchor", "--weights", str(ckpt)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert res.returncode != 0
    assert "is not a model state dict" in res.stderr
    assert "Traceback" not in res.stderr


def test_verify_weights_rejects_empty_dict_cleanly(tmp_path):
    _make_run(tmp_path)
    empty = tmp_path / "empty.pt"
    torch.save({}, empty)

    env = {**os.environ, "OVL_DEVICE": "cpu", "OVL_TIMESTAMP": "0"}
    res = subprocess.run(
        [sys.executable, str(ROOT / "simple" / "ovl.py"), "verify",
         "--run", str(tmp_path), "--no-anchor", "--weights", str(empty)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert res.returncode != 0
    assert "is not a model state dict" in res.stderr
    assert "Traceback" not in res.stderr
