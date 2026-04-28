"""Locate an LTspice installation on the current host."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Install:
    """A detected LTspice install."""

    exe: Path          # the executable to invoke
    version: str       # "17.2.4", "26.0.1", or "unknown"
    path: str          # canonical root (app bundle dir on macOS, parent otherwise)
    source: str        # "env:SIM_LTSPICE_EXE", "default-path:/Applications", etc.


def _macos_native_version(app_dir: Path) -> str | None:
    info = app_dir / "Contents" / "Info.plist"
    if not info.is_file():
        return None
    try:
        proc = subprocess.run(
            ["plutil", "-extract", "CFBundleShortVersionString",
             "raw", "-o", "-", str(info)],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    v = (proc.stdout or "").strip()
    return v or None


def _make_install(exe: Path, source: str) -> Install | None:
    if not exe.is_file():
        return None
    version: str | None = None
    app_dir: Path | None = None
    if sys.platform == "darwin":
        parent = exe.parent.parent.parent
        if parent.suffix == ".app":
            app_dir = parent
            version = _macos_native_version(parent)
    return Install(
        exe=exe,
        version=version or "unknown",
        path=str(app_dir) if app_dir else str(exe.parent),
        source=source,
    )


def _candidates_env() -> list[tuple[Path, str]]:
    override = os.environ.get("SIM_LTSPICE_EXE")
    if not override:
        return []
    p = Path(override).expanduser()
    return [(p, "env:SIM_LTSPICE_EXE")] if p.is_file() else []


def _candidates_macos() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for base in (
        Path("/Applications/LTspice.app/Contents/MacOS/LTspice"),
        Path.home() / "Applications/LTspice.app/Contents/MacOS/LTspice",
    ):
        if base.is_file():
            out.append((base, "default-path:/Applications"))
    return out


def _candidates_windows() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    user = os.environ.get("USERPROFILE", "")
    candidates: list[tuple[Path, str]] = []
    if user:
        candidates.append((
            Path(user) / r"AppData\Local\Programs\ADI\LTspice\LTspice.exe",
            "default-path:LocalAppData",
        ))
    candidates.extend([
        (Path(r"C:\Program Files\ADI\LTspice\LTspice.exe"), "default-path:Program Files"),
        (Path(r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe"), "default-path:LTspiceXVII"),
        (Path(r"C:\Program Files (x86)\LTC\LTspiceXVII\XVIIx64.exe"), "default-path:LTspiceXVII-x86"),
        (Path(r"C:\Program Files (x86)\LTC\LTspiceIV\scad3.exe"), "default-path:LTspiceIV"),
    ])
    for p, src in candidates:
        if p.is_file():
            out.append((p, src))
    return out


def find_ltspice() -> list[Install]:
    """Return every LTspice install discovered on this host.

    Honors `$SIM_LTSPICE_EXE` first (agent override), then platform
    default paths. Linux users running LTspice under wine should set
    `$SIM_LTSPICE_EXE` explicitly.
    """
    finders = [_candidates_env]
    if sys.platform == "darwin":
        finders.append(_candidates_macos)
    elif sys.platform == "win32":
        finders.append(_candidates_windows)

    found: dict[str, Install] = {}
    for finder in finders:
        try:
            cands = finder()
        except Exception:
            continue
        for path, source in cands:
            inst = _make_install(path, source=source)
            if inst is None:
                continue
            key = str(inst.exe.resolve())
            found.setdefault(key, inst)
    return list(found.values())
