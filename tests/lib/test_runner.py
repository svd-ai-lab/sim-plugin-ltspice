"""Runner tests — unit coverage without LTspice invocation."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sim_plugin_ltspice.lib import (
    NETLIST_SUFFIXES,
    LtspiceNotInstalled,
    UnsupportedInput,
    run_asc,
    run_net,
)
from sim_plugin_ltspice.lib.netlist import FlattenError
from sim_plugin_ltspice.lib.runner import DEFAULT_TIMEOUT_S


FIXTURES = Path(__file__).parent / "fixtures"


def test_known_suffixes_accepted():
    assert set(NETLIST_SUFFIXES) == {".net", ".cir", ".sp"}


def test_raises_on_wrong_suffix(tmp_path, monkeypatch):
    p = tmp_path / "x.txt"
    p.write_text("not a netlist")
    with pytest.raises(UnsupportedInput):
        run_net(p)


def test_raises_when_not_installed(monkeypatch):
    monkeypatch.setattr("sim_plugin_ltspice.lib.runner.find_ltspice", lambda: [])
    with pytest.raises(LtspiceNotInstalled):
        run_net(FIXTURES / "ltspice_good.net")


class TestTimeout:
    """Default 300-second timeout must survive into the subprocess call."""

    def _stub_install(self, monkeypatch, tmp_path):
        """Return a fake LTspice install so find_ltspice() is short-circuited."""
        from sim_plugin_ltspice.lib.install import Install

        fake_exe = tmp_path / "LTspice"
        fake_exe.write_text("#!/bin/sh\nexit 0\n")
        fake_exe.chmod(0o755)
        fake = Install(
            exe=fake_exe, version="test", path=str(tmp_path), source="test"
        )
        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.find_ltspice", lambda: [fake])

    def test_default_timeout_is_300s(self, monkeypatch, tmp_path):
        """The default timeout is exposed as a module-level constant."""
        assert DEFAULT_TIMEOUT_S == 300.0

    def test_default_timeout_propagates_to_subprocess(self, monkeypatch, tmp_path):
        """If caller doesn't pass ``timeout=``, subprocess.run sees 300s."""
        self._stub_install(monkeypatch, tmp_path)
        captured: dict[str, object] = {}

        def fake_run(*_args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.subprocess.run", fake_run)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        run_net(script)
        assert captured["timeout"] == DEFAULT_TIMEOUT_S

    def test_explicit_none_disables_timeout(self, monkeypatch, tmp_path):
        """Passing ``timeout=None`` restores pre-0.2 unbounded behaviour."""
        self._stub_install(monkeypatch, tmp_path)
        captured: dict[str, object] = {}

        def fake_run(*_args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.subprocess.run", fake_run)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        run_net(script, timeout=None)
        assert captured["timeout"] is None

    def test_custom_timeout_wins(self, monkeypatch, tmp_path):
        self._stub_install(monkeypatch, tmp_path)
        captured: dict[str, object] = {}

        def fake_run(*_args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.subprocess.run", fake_run)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        run_net(script, timeout=5.0)
        assert captured["timeout"] == 5.0

    def test_timeout_yields_failure_result_not_exception(self, monkeypatch, tmp_path):
        """A TimeoutExpired from subprocess translates to exit_code=124."""
        self._stub_install(monkeypatch, tmp_path)

        def fake_run(*_args, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd="LTspice", timeout=kwargs.get("timeout", 0), output="", stderr=""
            )

        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.subprocess.run", fake_run)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        result = run_net(script, timeout=0.1)
        assert result.exit_code == 124
        assert "timed out" in result.stderr
        assert "session-0" in result.stderr  # keep the Windows-SSH hint


class TestArgvShape:
    """Cover the LTspice CLI flag construction in run_net.

    These tests intercept ``subprocess.run`` and assert on the argv
    that *would* have been passed to LTspice. No real binary is run.
    """

    def _stub_install(self, monkeypatch, tmp_path):
        from sim_plugin_ltspice.lib.install import Install

        fake_exe = tmp_path / "LTspice"
        fake_exe.write_text("#!/bin/sh\nexit 0\n")
        fake_exe.chmod(0o755)
        fake = Install(
            exe=fake_exe, version="test", path=str(tmp_path), source="test"
        )
        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.find_ltspice", lambda: [fake])
        return fake_exe

    def _capture(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run(args, **kwargs):
            captured["args"] = list(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.subprocess.run", fake_run)
        return captured

    def test_default_argv_unchanged(self, monkeypatch, tmp_path):
        """Without ini/sym_paths, argv has no -ini / -I flags."""
        self._stub_install(monkeypatch, tmp_path)
        captured = self._capture(monkeypatch)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        run_net(script)
        argv = captured["args"]
        assert not any(a == "-ini" for a in argv)
        assert not any(a.startswith("-I") and a != "-ini" for a in argv)

    def test_ini_param_inserted_before_b(self, monkeypatch, tmp_path):
        """-ini <path> appears in argv when caller passes ini=."""
        self._stub_install(monkeypatch, tmp_path)
        captured = self._capture(monkeypatch)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        ini = tmp_path / "clean.ini"
        ini.write_text("[Options]\n")

        run_net(script, ini=ini)
        argv = captured["args"]
        assert "-ini" in argv
        ini_idx = argv.index("-ini")
        assert argv[ini_idx + 1] == str(ini.resolve())
        # -ini must come before -b
        assert ini_idx < argv.index("-b")

    def test_sym_paths_appear_as_I_flags(self, monkeypatch, tmp_path):
        """sym_paths=[a, b] becomes -Ia, -Ib at the end of argv (no space)."""
        self._stub_install(monkeypatch, tmp_path)
        captured = self._capture(monkeypatch)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        a = tmp_path / "a"
        a.mkdir()
        b = tmp_path / "b"
        b.mkdir()

        run_net(script, sym_paths=[a, b])
        argv = captured["args"]
        # Last two args must be -I<a>, -I<b> in order
        assert argv[-2] == f"-I{a.resolve()}"
        assert argv[-1] == f"-I{b.resolve()}"

    def test_sym_paths_strictly_after_script(self, monkeypatch, tmp_path):
        """LTspice docs require -I<path> to be the LAST arg."""
        self._stub_install(monkeypatch, tmp_path)
        captured = self._capture(monkeypatch)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        sym_dir = tmp_path / "syms"
        sym_dir.mkdir()

        run_net(script, sym_paths=[sym_dir])
        argv = captured["args"]
        i_idx = next(i for i, a in enumerate(argv) if a.startswith("-I"))
        script_idx = argv.index(script.as_posix())
        assert i_idx > script_idx

    def test_ini_and_sym_paths_combine(self, monkeypatch, tmp_path):
        """Both flags together — ini early, -I last, deck in the middle."""
        self._stub_install(monkeypatch, tmp_path)
        captured = self._capture(monkeypatch)
        script = tmp_path / "rc.net"
        script.write_text("* empty\n.end\n")
        ini = tmp_path / "clean.ini"
        ini.write_text("[Options]\n")
        sym_dir = tmp_path / "syms"
        sym_dir.mkdir()

        run_net(script, ini=ini, sym_paths=[sym_dir])
        argv = captured["args"]
        # Order check: -ini <ini> ... <script> -I<sym>
        ini_idx = argv.index("-ini")
        script_idx = argv.index(script.as_posix())
        i_idx = next(i for i, a in enumerate(argv) if a.startswith("-I"))
        assert ini_idx < script_idx < i_idx

    def test_run_asc_forwards_ini_and_sym_paths(self, monkeypatch, tmp_path):
        """run_asc must forward ini= and sym_paths= to run_net."""
        asc = tmp_path / "rc.asc"
        _write_rc_asc(asc)

        captured: dict[str, object] = {}

        def fake_run_net(script, **kwargs):
            captured.update(kwargs)
            from sim_plugin_ltspice.lib.log import LogResult
            from sim_plugin_ltspice.lib.runner import RunResult
            return RunResult(
                exit_code=0, stdout="", stderr="",
                duration_s=0.0, script=Path(script),
                started_at="t", log=LogResult(),
                log_path=None, raw_path=None, raw_traces=[],
            )

        monkeypatch.setattr("sim_plugin_ltspice.lib.runner.run_net", fake_run_net)
        ini = tmp_path / "clean.ini"
        ini.write_text("[Options]\n")
        sym_dir = tmp_path / "syms"
        sym_dir.mkdir()

        run_asc(asc, catalog=_rc_catalog(), ini=ini, sym_paths=[sym_dir])
        assert captured["ini"] == ini
        assert list(captured["sym_paths"]) == [sym_dir]


@pytest.mark.integration
def test_rc_transient_runs_end_to_end(tmp_path):
    """Real LTspice batch. Skipped if no install is visible."""
    import shutil

    from sim_plugin_ltspice.lib import find_ltspice

    if not find_ltspice():
        pytest.skip("LTspice not installed on this host")

    net = tmp_path / "rc.net"
    shutil.copyfile(FIXTURES / "ltspice_good.net", net)

    result = run_net(net)
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert result.ok, f"log errors: {result.log.errors}"
    assert "vout_pk" in result.log.measures
    assert result.log.measures["vout_pk"].value == pytest.approx(1.0, rel=5e-3)
    assert "V(out)" in result.raw_traces
    assert result.log_path and result.log_path.is_file()
    assert result.raw_path and result.raw_path.is_file()


# ----------------------------------------------------------------------
# run_asc
# ----------------------------------------------------------------------


def _rc_catalog():
    """Synthetic catalog covering the symbols used in `_rc_asc`."""
    from sim_plugin_ltspice.lib.symbols import Pin, SymbolCatalog, SymbolDef

    cat = SymbolCatalog.__new__(SymbolCatalog)
    cat._search_paths = []
    cat._index = {}
    defs = [
        SymbolDef(
            name="res", path=Path("/fake/res.asy"), prefix="R",
            pins=[Pin("1", 16, 16, spice_order=1), Pin("2", 16, 96, spice_order=2)],
        ),
        SymbolDef(
            name="cap", path=Path("/fake/cap.asy"), prefix="C",
            pins=[Pin("1", 16, 0, spice_order=1), Pin("2", 16, 64, spice_order=2)],
        ),
        SymbolDef(
            name="voltage", path=Path("/fake/voltage.asy"), prefix="V",
            pins=[Pin("1", 0, 16, spice_order=1), Pin("2", 0, 96, spice_order=2)],
        ),
    ]
    cat._cache = {d.name.casefold(): d for d in defs}
    cat.find = lambda name, _c=cat: _c._cache.get(name.casefold())  # type: ignore[assignment]
    return cat


def _write_rc_asc(path: Path) -> None:
    """Minimal RC schematic — voltage source, R, C, OUT label, .tran directive."""
    path.write_text(
        "Version 4\n"
        "SHEET 1 880 680\n"
        "SYMBOL voltage 0 0 R0\n"
        "SYMATTR InstName V1\n"
        "SYMATTR Value PULSE(0 1 0 1u 1u 1m 2m)\n"
        "SYMBOL res 96 0 R0\n"
        "SYMATTR InstName R1\n"
        "SYMATTR Value 1k\n"
        "SYMBOL cap 192 16 R0\n"
        "SYMATTR InstName C1\n"
        "SYMATTR Value 1u\n"
        "WIRE 0 16 112 16\n"
        "WIRE 112 96 112 16\n"
        "WIRE 112 96 208 96\n"
        "WIRE 208 16 208 80\n"
        "WIRE 0 96 0 16\n"
        "WIRE 0 96 208 96\n"
        "FLAG 208 16 OUT\n"
        "FLAG 0 96 0\n"
        "TEXT 0 200 Left 2 !.tran 0 5m 0 1u\n",
        encoding="utf-8",
    )


def test_run_asc_rejects_non_asc(tmp_path):
    p = tmp_path / "x.net"
    p.write_text("* not an asc\n")
    with pytest.raises(UnsupportedInput, match="run_asc accepts .asc"):
        run_asc(p)


def test_run_asc_propagates_flatten_error(tmp_path):
    """A symbol the catalog can't resolve should raise FlattenError verbatim."""
    asc = tmp_path / "bogus.asc"
    asc.write_text(
        "Version 4\n"
        "SHEET 1 880 680\n"
        "SYMBOL no_such_symbol 0 0 R0\n"
        "SYMATTR InstName X1\n",
        encoding="utf-8",
    )
    from sim_plugin_ltspice.lib.symbols import SymbolCatalog
    empty = SymbolCatalog.__new__(SymbolCatalog)
    empty._search_paths = []
    empty._index = {}
    empty._cache = {}
    empty.find = lambda name, _c=empty: None  # type: ignore[assignment]

    with pytest.raises(FlattenError, match="no_such_symbol"):
        run_asc(asc, catalog=empty)


def test_run_asc_writes_sibling_netlist_and_delegates(monkeypatch, tmp_path):
    """run_asc should flatten to <stem>.net next to the .asc and call run_net."""
    asc = tmp_path / "rc.asc"
    _write_rc_asc(asc)

    captured: dict[str, object] = {}

    def fake_run_net(script, **kwargs):
        captured["script"] = Path(script)
        captured.update(kwargs)
        from sim_plugin_ltspice.lib.log import LogResult
        from sim_plugin_ltspice.lib.runner import RunResult
        return RunResult(
            exit_code=0, stdout="", stderr="",
            duration_s=0.0, script=Path(script),
            started_at="t", log=LogResult(),
            log_path=None, raw_path=None, raw_traces=[],
        )

    monkeypatch.setattr("sim_plugin_ltspice.lib.runner.run_net", fake_run_net)
    result = run_asc(asc, catalog=_rc_catalog(), timeout=42.0)

    expected_net = asc.with_suffix(".net")
    assert captured["script"] == expected_net
    assert captured["timeout"] == 42.0
    assert expected_net.is_file()
    text = expected_net.read_text()
    assert "V1" in text and "R1" in text and "C1" in text
    assert ".tran" in text.lower()
    assert result.exit_code == 0


def test_run_asc_passes_install_through(monkeypatch, tmp_path):
    """If caller pins an Install, run_asc must forward it to run_net."""
    from sim_plugin_ltspice.lib.install import Install
    asc = tmp_path / "rc.asc"
    _write_rc_asc(asc)

    fake_install = Install(
        exe=tmp_path / "fake-ltspice", version="t", path=str(tmp_path), source="test",
    )
    captured: dict[str, object] = {}

    def fake_run_net(script, **kwargs):
        captured.update(kwargs)
        from sim_plugin_ltspice.lib.log import LogResult
        from sim_plugin_ltspice.lib.runner import RunResult
        return RunResult(
            exit_code=0, stdout="", stderr="",
            duration_s=0.0, script=Path(script),
            started_at="t", log=LogResult(),
            log_path=None, raw_path=None, raw_traces=[],
        )

    monkeypatch.setattr("sim_plugin_ltspice.lib.runner.run_net", fake_run_net)
    run_asc(asc, install=fake_install, catalog=_rc_catalog())

    assert captured["install"] is fake_install


@pytest.mark.integration
def test_run_asc_montecarlo_end_to_end(tmp_path):
    """Real LTspice on the bundled montecarlo.asc fixture. Skipped if no install."""
    import shutil

    from sim_plugin_ltspice.lib import find_ltspice

    if not find_ltspice():
        pytest.skip("LTspice not installed on this host")

    asc = tmp_path / "montecarlo.asc"
    shutil.copyfile(FIXTURES / "montecarlo.asc", asc)

    result = run_asc(asc)
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert (tmp_path / "montecarlo.net").is_file()
    assert result.log_path and result.log_path.is_file()
