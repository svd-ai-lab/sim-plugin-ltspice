"""Build the wheel and assert that bundled skill files actually ship.

This locks the layout decision: ``_skills/`` lives inside the package, so
hatchling picks it up via ``packages = ["src/sim_plugin_ltspice"]``
without any ``force-include`` clause. If a future refactor moves
``_skills/`` outside the package, this test fails immediately.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_wheel_contains_skills(tmp_path: Path) -> None:
    out_dir = tmp_path / "dist"
    out_dir.mkdir()

    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"build failed: {proc.stderr[-2000:]}"

    wheels = list(out_dir.glob("sim_plugin_ltspice-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())

    required = {
        "sim_plugin_ltspice/__init__.py",
        "sim_plugin_ltspice/driver.py",
        "sim_plugin_ltspice/_skills/ltspice/SKILL.md",
    }
    missing = required - names
    assert not missing, f"missing from wheel: {missing}"
