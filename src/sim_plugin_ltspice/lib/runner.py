"""Subprocess wrapper to run LTspice on a netlist or schematic.

`run_net` runs a `.net` / `.cir` / `.sp` file directly. `run_asc`
flattens a `.asc` schematic to a temporary netlist via the pure-Python
`schematic_to_netlist` path, then delegates to `run_net` — no LTspice
binary in the authoring loop, only in the actual solve.

Both produce a typed `RunResult` that folds together the subprocess
outcome, the structured `.log` (via `sim_ltspice.log.parse_log`), and
the `.raw` trace names (via `sim_ltspice.raw.trace_names`).
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .asc import read_asc
from .install import Install, find_ltspice
from .log import LogResult, parse_log
from .netlist import schematic_to_netlist, write_net
from .raw import trace_names
from .symbols import SymbolCatalog


NETLIST_SUFFIXES = (".net", ".cir", ".sp")

# Default subprocess-level timeout for a single LTspice invocation.
# 300 s covers every simulation we ever run in tests while still
# failing fast on the Windows session-0 hang (see
# https://github.com/svd-ai-lab/sim-ltspice/issues/<TBD>). Users with
# legitimately long sweeps can pass ``timeout=`` explicitly; pass
# ``timeout=None`` to restore the pre-0.2 unbounded behaviour.
DEFAULT_TIMEOUT_S = 300.0


class LtspiceError(Exception):
    """Base class for sim_ltspice errors."""


class LtspiceNotInstalled(LtspiceError):
    """Raised when no LTspice install is discoverable on this host."""


class UnsupportedInput(LtspiceError):
    """Raised when the input file is not a netlist this runner accepts."""


# A sentinel distinct from `None` so callers can explicitly say
# "disable the timeout" via ``timeout=None``.
_UNSET: float | None = -1.0


@dataclass
class RunResult:
    """Outcome of a single LTspice batch invocation."""

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    script: Path
    started_at: str
    log: LogResult
    log_path: Path | None
    raw_path: Path | None
    raw_traces: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when LTspice exited cleanly AND no errors were logged."""
        return self.exit_code == 0 and not self.log.errors


def run_net(
    script: Path | str,
    *,
    install: Install | None = None,
    timeout: float | None = _UNSET,  # type: ignore[assignment]
    ini: Path | str | None = None,
    sym_paths: Sequence[Path | str] = (),
) -> RunResult:
    """Run an LTspice batch simulation on a `.net` / `.cir` / `.sp` netlist.

    Returns a `RunResult` with parsed `.log` and `.raw` trace names.
    Does not raise on convergence errors — inspect `result.ok` or
    `result.log.errors` for those. Raises `LtspiceNotInstalled` if no
    install is discoverable and none was passed explicitly, and
    `UnsupportedInput` for non-netlist suffixes.

    Parameters
    ----------
    timeout
        Seconds to wait before killing LTspice. Default is
        ``DEFAULT_TIMEOUT_S`` (300 s), which is long enough for any
        sane simulation but fails fast on the Windows-session-0 hang
        where LTspice never produces output. Pass ``timeout=None``
        explicitly to restore unbounded waiting, or any positive float
        to tighten the bound.
    ini
        Optional path to a custom `LTspice.ini` to use instead of the
        per-user default (`%APPDATA%\\LTspice.ini` on Windows,
        `~/Library/Preferences/LTspice.ini` on macOS). Useful for
        reproducible CI runs where you don't want host state (window
        positions, recent files, search paths) bleeding into results.
        Forwarded as ``-ini <path>``.
    sym_paths
        Extra symbol / library search paths to inject for this run.
        Forwarded as ``-I<path>`` flags. LTspice requires `-I<path>`
        to be the **last** argument with **no space** after `-I`;
        we handle that ordering for you.

    On timeout the child process is terminated and the returned
    ``RunResult`` has ``exit_code != 0`` and a ``stderr`` describing
    the timeout — no ``TimeoutExpired`` exception propagates.
    """
    # Resolve the sentinel ``_UNSET`` → default. Explicit ``None`` keeps
    # meaning "no timeout".
    effective_timeout: float | None
    if timeout is _UNSET:
        effective_timeout = DEFAULT_TIMEOUT_S
    else:
        effective_timeout = timeout

    script = Path(script).resolve()
    if script.suffix.lower() not in NETLIST_SUFFIXES:
        raise UnsupportedInput(
            f"run_net accepts {NETLIST_SUFFIXES} (got {script.suffix}). "
            f"For .asc schematics use run_asc()."
        )

    if install is None:
        installs = find_ltspice()
        if not installs:
            raise LtspiceNotInstalled(
                "LTspice not found. Set $SIM_LTSPICE_EXE or install LTspice "
                "from analog.com."
            )
        install = installs[0]

    # Native macOS LTspice accepts only '-b <netlist>'. Windows / wine
    # additionally accept '-Run' (same effect).
    cmd: list[str] = [str(install.exe)]
    if ini is not None:
        # `-ini <path>` placed early so it can't be confused with
        # `-I<path>` (no-space) symbol-path injection.
        cmd += ["-ini", str(Path(ini).expanduser().resolve())]
    if sys.platform == "darwin":
        cmd += ["-b", script.as_posix()]
    else:
        cmd += ["-Run", "-b", script.as_posix()]
    # `-I<path>` MUST be the last argument and there is NO space
    # between `-I` and `<path>` (LTspice 26 docs).
    for path in sym_paths:
        cmd.append(f"-I{Path(path).expanduser().resolve()}")

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        # subprocess.run already terminates and reaps the child on
        # Python 3.5+, but captured output may be bytes (when no
        # decode happened) — coerce defensively.
        def _as_str(buf: bytes | str | None) -> str:
            if buf is None:
                return ""
            if isinstance(buf, bytes):
                return buf.decode("utf-8", errors="replace")
            return buf

        exit_code = 124  # GNU `timeout` convention
        stdout = _as_str(exc.stdout)
        stderr = (
            f"sim_ltspice: LTspice timed out after {effective_timeout:g}s "
            f"(script={script}). The process has been terminated. "
            "On Windows, SSH session-0 spawns hang indefinitely — run "
            "from an interactive desktop session instead, or pass "
            "timeout=<seconds> to tighten the bound.\n"
            + _as_str(exc.stderr)
        )
    duration = time.monotonic() - t0

    log_path = script.with_suffix(".log")
    raw_path = script.with_suffix(".raw")
    log_result = parse_log(log_path) if log_path.is_file() else LogResult()
    traces = trace_names(raw_path) if raw_path.is_file() else []

    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=round(duration, 3),
        script=script,
        started_at=started,
        log=log_result,
        log_path=log_path if log_path.is_file() else None,
        raw_path=raw_path if raw_path.is_file() else None,
        raw_traces=traces,
    )


def run_asc(
    script: Path | str,
    *,
    install: Install | None = None,
    catalog: SymbolCatalog | None = None,
    timeout: float | None = _UNSET,  # type: ignore[assignment]
    ini: Path | str | None = None,
    sym_paths: Sequence[Path | str] = (),
) -> RunResult:
    """Run an LTspice batch simulation on a `.asc` schematic.

    Flattens the schematic to a sibling `.net` via the pure-Python
    `schematic_to_netlist` path, then delegates to `run_net`. The
    intermediate netlist is written next to the `.asc` (overwriting any
    existing file at that path, mirroring LTspice's own `-netlist`
    convention) so the resulting `.log` and `.raw` land alongside it.

    Parameters
    ----------
    catalog
        SymbolCatalog used to resolve `SYMBOL` placements to `.asy`
        definitions. Defaults to ``SymbolCatalog()``, which auto-discovers
        from ``$LTSPICE_SYM_PATH`` or the platform's `lib/sym/` tree.
    install, timeout, ini, sym_paths
        Forwarded to `run_net`.

    Raises
    ------
    UnsupportedInput
        If the file suffix is not `.asc`.
    FlattenError
        If a placed symbol cannot be resolved against the catalog or a
        SYMBOL placement is missing its `InstName`.
    LtspiceNotInstalled
        From `run_net`, if no install is discoverable.
    """
    script = Path(script).resolve()
    if script.suffix.lower() != ".asc":
        raise UnsupportedInput(
            f"run_asc accepts .asc (got {script.suffix}). "
            f"For SPICE netlists use run_net()."
        )

    schem = read_asc(script)
    netlist = schematic_to_netlist(schem, catalog or SymbolCatalog())
    net_path = script.with_suffix(".net")
    write_net(netlist, net_path)
    return run_net(
        net_path,
        install=install,
        timeout=timeout,
        ini=ini,
        sym_paths=sym_paths,
    )
