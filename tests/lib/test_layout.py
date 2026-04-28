"""Layout engine: netlist → schematic round-trips and topology gates."""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_plugin_ltspice.lib.layout import UnsupportedTopology, netlist_to_schematic
from sim_plugin_ltspice.lib.netlist import (
    Directive,
    Element,
    Netlist,
    schematic_to_netlist,
)
from sim_plugin_ltspice.lib.schematic import Rotation
from sim_plugin_ltspice.lib.symbols import Pin, SymbolCatalog, SymbolDef


def _fake_catalog(defs: list[SymbolDef]) -> SymbolCatalog:
    """Bypass disk walk — the netlist module needs a catalog to flatten."""
    cat = SymbolCatalog.__new__(SymbolCatalog)
    cat._search_paths = []
    cat._index = {}
    cat._cache = {d.name.casefold(): d for d in defs}

    def _find(name, _self=cat):
        return _self._cache.get(name.casefold())

    cat.find = _find   # type: ignore[assignment]
    return cat


def _stock_catalog() -> SymbolCatalog:
    """Matches layout._CANONICAL_PINS exactly so the round-trip works."""
    def mk(name: str, prefix: str, p1: tuple[int, int], p2: tuple[int, int]) -> SymbolDef:
        return SymbolDef(
            name=name, path=Path(f"/fake/{name}.asy"), prefix=prefix,
            pins=[Pin(name="1", x=p1[0], y=p1[1], spice_order=1),
                  Pin(name="2", x=p2[0], y=p2[1], spice_order=2)],
        )

    return _fake_catalog([
        mk("res", "R", (16, 16), (16, 96)),
        mk("cap", "C", (16, 0),  (16, 64)),
        mk("ind", "L", (16, 16), (16, 96)),
        mk("voltage", "V", (0, 16), (0, 96)),
    ])


# ----------------------------------------------------------------------
# Supported topology: RC low-pass
# ----------------------------------------------------------------------

class TestRCLowPass:
    def _rc_net(self) -> Netlist:
        return Netlist(
            title="* rc",
            elements=[
                Element("V1", ["in", "0"], "PULSE(0 1 0 1u 1u 1m 2m)"),
                Element("R1", ["in", "out"], "1k"),
                Element("C1", ["out", "0"], "100n"),
            ],
            directives=[Directive(".tran", "5m")],
        )

    def test_places_source_resistor_capacitor(self):
        schem = netlist_to_schematic(self._rc_net())
        kinds = {p.attrs["InstName"]: p for p in schem.symbols}
        assert set(kinds) == {"V1", "R1", "C1"}
        assert kinds["V1"].rotation is Rotation.R0
        assert kinds["R1"].rotation is Rotation.R270   # horizontal series
        assert kinds["C1"].rotation is Rotation.R0     # shunt to ground

    def test_flags_include_user_nets_and_ground(self):
        schem = netlist_to_schematic(self._rc_net())
        labels = {f.net for f in schem.flags}
        assert "0" in labels
        assert "in" in labels
        assert "out" in labels

    def test_roundtrip_through_flattener(self):
        """Layout → Schematic → re-flatten must yield the original topology."""
        original = self._rc_net()
        schem = netlist_to_schematic(original)
        reflat = schematic_to_netlist(schem, _stock_catalog())

        original_sig = sorted((e.name, tuple(e.nodes), e.tail)
                              for e in original.elements)
        reflat_sig = sorted((e.name, tuple(e.nodes), e.tail)
                            for e in reflat.elements)
        assert reflat_sig == original_sig

    def test_directive_survives_roundtrip(self):
        schem = netlist_to_schematic(self._rc_net())
        reflat = schematic_to_netlist(schem, _stock_catalog())
        cmds = [d.command for d in reflat.directives]
        assert ".tran" in cmds


# ----------------------------------------------------------------------
# Longer chain: RLC, multi-stage ladder
# ----------------------------------------------------------------------

class TestRLCLadder:
    def test_three_stage_ladder(self):
        """V1 → R1 → L1 → C1 → ground, all series with one shunt."""
        net = Netlist(
            title="* rlc",
            elements=[
                Element("V1", ["in", "0"], "1"),
                Element("R1", ["in", "a"], "50"),
                Element("L1", ["a", "out"], "10u"),
                Element("C1", ["out", "0"], "1n"),
            ],
            directives=[Directive(".ac", "dec 100 1 1Meg")],
        )
        schem = netlist_to_schematic(net)
        names = {p.attrs["InstName"] for p in schem.symbols}
        assert names == {"V1", "R1", "L1", "C1"}

        reflat = schematic_to_netlist(schem, _stock_catalog())
        reflat_sig = sorted((e.name, tuple(e.nodes), e.tail)
                            for e in reflat.elements)
        orig_sig = sorted((e.name, tuple(e.nodes), e.tail)
                          for e in net.elements)
        assert reflat_sig == orig_sig


# ----------------------------------------------------------------------
# Parallel shunts on the same rail node
# ----------------------------------------------------------------------

class TestMultipleShunts:
    def test_parallel_rc_on_output(self):
        """Two shunts on the same rail node should both land with ground flags."""
        net = Netlist(
            title="* parallel",
            elements=[
                Element("V1", ["in", "0"], "1"),
                Element("R1", ["in", "out"], "1k"),
                Element("C1", ["out", "0"], "100n"),
                Element("R2", ["out", "0"], "10k"),
            ],
        )
        schem = netlist_to_schematic(net)
        names = {p.attrs["InstName"] for p in schem.symbols}
        assert names == {"V1", "R1", "C1", "R2"}

        reflat = schematic_to_netlist(schem, _stock_catalog())
        by_name = {e.name: sorted(e.nodes) for e in reflat.elements}
        # Both shunts on 'out' and '0'
        assert by_name["C1"] == ["0", "out"]
        assert by_name["R2"] == ["0", "out"]


# ----------------------------------------------------------------------
# Topology gates
# ----------------------------------------------------------------------

class TestUnsupportedTopologies:
    def test_subcircuit_ref_rejected(self):
        net = Netlist(elements=[
            Element("X1", ["a", "b", "c"], "LT1001")
        ])
        with pytest.raises(UnsupportedTopology, match="subcircuit"):
            netlist_to_schematic(net)

    def test_bjt_rejected(self):
        net = Netlist(elements=[
            Element("Q1", ["c", "b", "e"], "2N3904")
        ])
        with pytest.raises(UnsupportedTopology, match="multi-terminal"):
            netlist_to_schematic(net)

    def test_missing_source_rejected(self):
        net = Netlist(elements=[
            Element("R1", ["a", "b"], "1k"),
            Element("C1", ["b", "0"], "1n"),
        ])
        with pytest.raises(UnsupportedTopology, match="requires at least one"):
            netlist_to_schematic(net)

    def test_floating_source_rejected(self):
        net = Netlist(elements=[
            Element("V1", ["a", "b"], "1"),
            Element("R1", ["a", "0"], "1k"),
            Element("C1", ["b", "0"], "1n"),
        ])
        with pytest.raises(UnsupportedTopology, match="ground"):
            netlist_to_schematic(net)

    def test_branching_rail_rejected(self):
        """A rail net touching two series devices should fail clearly."""
        net = Netlist(elements=[
            Element("V1", ["in", "0"], "1"),
            Element("R1", ["in", "a"], "1k"),
            Element("R2", ["in", "b"], "1k"),   # also branches off 'in'
            Element("C1", ["a", "0"], "1n"),
            Element("C2", ["b", "0"], "1n"),
        ])
        with pytest.raises(UnsupportedTopology, match="series branches"):
            netlist_to_schematic(net)
