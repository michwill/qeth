from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.skipif(
        os.environ.get("QETH_RUN_HIDAPI_REPRO") != "1",
        reason="set QETH_RUN_HIDAPI_REPRO=1 to run the macOS HIDAPI repro",
    ),
    pytest.mark.skipif(
        sys.platform != "darwin",
        reason="HIDAPI repro targets macOS CoreFoundation threading behavior",
    ),
]


def test_hidapi_threading_repro_matrix() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "repro_hidapi_macos.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--mode",
            "matrix",
            "--iterations",
            "1",
        ],
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
