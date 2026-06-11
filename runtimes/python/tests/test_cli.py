"""CLI : `vignemale --help` et `vignemale check` (sortie protojson parseable)."""

import json
import os
import subprocess
import sys

from conftest import EXAMPLES


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "vignemale.cli", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_help():
    r = run_cli("--help")
    assert r.returncode == 0
    assert "run" in r.stdout and "check" in r.stdout


def test_check_emits_meta_json():
    r = run_cli("check", os.path.join(EXAMPLES, "shop"))
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["modulePath"] == "shop"
    assert {s["name"] for s in data["svcs"]} == {"catalog", "orders"}


def test_check_raw():
    r = run_cli("check", "--raw", os.path.join(EXAMPLES, "typed.py"))
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert "ChatRequest" in data["models"]
