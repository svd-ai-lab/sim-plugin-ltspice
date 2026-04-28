"""Unit tests for sim_plugin_ltspice.lib.symbols."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from sim_plugin_ltspice.lib.symbols import Pin, SymbolCatalog, parse_asy


def _make_asy(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


class TestParseAsy:
    def test_resistor_like(self, tmp_path):
        asy = _make_asy(tmp_path / "res.asy", """\
Version 4
SymbolType CELL
LINE Normal 16 88 16 96
LINE Normal 0 80 16 88
SYMATTR Value R
SYMATTR Prefix R
SYMATTR Description A resistor
PIN 16 16 NONE 0
PINATTR PinName A
PINATTR SpiceOrder 1
PIN 16 96 NONE 0
PINATTR PinName B
PINATTR SpiceOrder 2
""")
        sym = parse_asy(asy)
        assert sym.name == "res"
        assert sym.prefix == "R"
        assert sym.symbol_type == "CELL"
        assert sym.description == "A resistor"
        assert sym.default_value == "R"
        assert len(sym.pins) == 2
        assert sym.pins[0] == Pin(name="A", x=16, y=16, spice_order=1)
        assert sym.pins[1] == Pin(name="B", x=16, y=96, spice_order=2)

    def test_opamp_with_subcircuit_model(self, tmp_path):
        asy = _make_asy(tmp_path / "LT1001.asy", """\
Version 4
SymbolType CELL
LINE Normal -32 32 32 64
WINDOW 0 16 32 Left 2
SYMATTR Value LT1001
SYMATTR Prefix X
SYMATTR SpiceModel LTC.lib
SYMATTR Value2 LT1001
SYMATTR Description Precision Operational Amplifier
PIN -32 80 NONE 0
PINATTR PinName In+
PINATTR SpiceOrder 1
PIN -32 48 NONE 0
PINATTR PinName In-
PINATTR SpiceOrder 2
PIN 0 32 NONE 0
PINATTR PinName V+
PINATTR SpiceOrder 3
PIN 0 96 NONE 0
PINATTR PinName V-
PINATTR SpiceOrder 4
PIN 32 64 NONE 0
PINATTR PinName OUT
PINATTR SpiceOrder 5
""")
        sym = parse_asy(asy)
        assert sym.prefix == "X"
        assert sym.spice_model == "LTC.lib"
        assert len(sym.pins) == 5
        pin_order = [p.name for p in sym.ordered_pins()]
        assert pin_order == ["In+", "In-", "V+", "V-", "OUT"]

    def test_missing_spice_order_sorts_last(self, tmp_path):
        asy = _make_asy(tmp_path / "weird.asy", """\
Version 4
SymbolType CELL
PIN 0 0 NONE 0
PINATTR PinName first
PINATTR SpiceOrder 1
PIN 10 0 NONE 0
PINATTR PinName noorder
PIN 20 0 NONE 0
PINATTR PinName second
PINATTR SpiceOrder 2
""")
        sym = parse_asy(asy)
        ordered = sym.ordered_pins()
        assert [p.name for p in ordered] == ["first", "second", "noorder"]


class TestSymbolCatalog:
    def test_explicit_search_path(self, tmp_path):
        root = tmp_path / "sym"
        root.mkdir()
        _make_asy(root / "mycap.asy", """\
Version 4
SymbolType CELL
SYMATTR Prefix C
PIN 0 0 NONE 0
PINATTR PinName A
PINATTR SpiceOrder 1
PIN 16 0 NONE 0
PINATTR PinName B
PINATTR SpiceOrder 2
""")
        cat = SymbolCatalog(search_paths=[root])
        assert "mycap" in cat
        sym = cat.find("mycap")
        assert sym is not None
        assert sym.prefix == "C"

    def test_case_insensitive_lookup(self, tmp_path):
        root = tmp_path / "sym"
        root.mkdir()
        _make_asy(root / "Res.asy", "Version 4\nSymbolType CELL\nSYMATTR Prefix R\n")
        cat = SymbolCatalog(search_paths=[root])
        assert cat.find("res") is not None
        assert cat.find("RES") is not None
        assert cat.find("Res") is not None

    def test_categories_from_subdir(self, tmp_path):
        root = tmp_path / "sym"
        opamps = root / "Opamps"
        opamps.mkdir(parents=True)
        _make_asy(opamps / "LT1001.asy", "Version 4\nSymbolType CELL\nSYMATTR Prefix X\n")
        _make_asy(root / "res.asy", "Version 4\nSymbolType CELL\nSYMATTR Prefix R\n")

        cat = SymbolCatalog(search_paths=[root])
        cats = cat.categories()
        # Top-level symbols get category "" (empty string).
        assert "res" in cats[""]
        assert "LT1001" in cats["Opamps"]

    def test_env_override(self, tmp_path, monkeypatch):
        root = tmp_path / "override_sym"
        root.mkdir()
        _make_asy(root / "myres.asy", "Version 4\nSymbolType CELL\nSYMATTR Prefix R\n")
        monkeypatch.setenv("SIM_LTSPICE_SYM_PATHS", str(root))
        cat = SymbolCatalog()
        assert cat.find("myres") is not None

    def test_missing_name_returns_none(self, tmp_path):
        cat = SymbolCatalog(search_paths=[tmp_path])
        assert cat.find("does_not_exist") is None

    def test_cache_reuses_result(self, tmp_path):
        root = tmp_path / "sym"
        root.mkdir()
        _make_asy(root / "a.asy", "Version 4\nSymbolType CELL\nSYMATTR Prefix R\n")
        cat = SymbolCatalog(search_paths=[root])
        first = cat.find("a")
        second = cat.find("a")
        assert first is second


@pytest.mark.integration
class TestAgainstShippedLibrary:
    """Validate against the real LTspice symbol library when present.

    Skipped on CI runners without LTspice installed.
    """

    def _default_lib_dir(self) -> Path | None:
        if sys.platform == "darwin":
            p = Path.home() / "Library/Application Support/LTspice/lib/sym"
            return p if p.is_dir() else None
        if sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA")
            if local:
                p = Path(local) / "LTspice/lib/sym"
                return p if p.is_dir() else None
        return None

    def test_finds_core_passives(self):
        lib = self._default_lib_dir()
        if lib is None:
            pytest.skip("LTspice symbol library not installed on this host")
        cat = SymbolCatalog(search_paths=[lib])
        for name in ("res", "cap", "ind", "voltage", "current"):
            sym = cat.find(name)
            assert sym is not None, f"missing core symbol: {name}"
            # Every passive has exactly 2 pins with SpiceOrders 1 and 2.
            orders = sorted(p.spice_order for p in sym.pins if p.spice_order)
            assert orders == [1, 2], f"{name}: expected [1,2], got {orders}"

    def test_finds_analog_devices_opamp(self):
        lib = self._default_lib_dir()
        if lib is None:
            pytest.skip("LTspice symbol library not installed on this host")
        cat = SymbolCatalog(search_paths=[lib])
        sym = cat.find("LT1001")
        if sym is None:
            pytest.skip("LT1001 symbol not shipped with this LTspice install")
        assert sym.prefix == "X"  # subcircuit instance
        # LT1001 has 5 pins: In+, In-, V+, V-, OUT
        assert len(sym.pins) == 5
        pin_names = {p.name for p in sym.pins}
        assert {"In+", "In-", "V+", "V-", "OUT"}.issubset(pin_names)
