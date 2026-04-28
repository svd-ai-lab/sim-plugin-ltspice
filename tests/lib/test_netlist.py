"""Unit + integration tests for `sim_plugin_ltspice.lib.netlist`."""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_plugin_ltspice.lib.netlist import (
    Element,
    FlattenError,
    Netlist,
    parse_net,
    schematic_to_netlist,
    write_net,
)
from sim_plugin_ltspice.lib.schematic import (
    Flag,
    Placement,
    Rotation,
    Schematic,
    TextDirective,
    TextKind,
    Wire,
)
from sim_plugin_ltspice.lib.symbols import Pin, SymbolCatalog, SymbolDef


_FIXTURES = Path(__file__).parent / "fixtures"


# ----------------------------------------------------------------------
# parse_net
# ----------------------------------------------------------------------

class TestParseNet:
    def test_parses_rc_lowpass(self):
        net = parse_net(_FIXTURES / "ltspice_good.net")
        assert net.title.startswith("RC low-pass")
        assert len(net.elements) == 3
        v1, r1, c1 = net.elements
        assert v1.name == "V1"
        assert v1.nodes == ["in", "0"]
        assert v1.tail == "PULSE(0 1 0 1u 1u 1m 2m)"
        assert r1.name == "R1"
        assert r1.nodes == ["in", "out"]
        assert r1.tail == "1k"
        assert c1.tail == "100n"
        cmds = [d.command for d in net.directives]
        assert cmds == [".tran", ".meas"]
        assert net.directives[1].args == "TRAN vout_pk MAX V(out)"

    def test_stops_at_dot_end(self, tmp_path):
        body = (
            "* stop at .end\n"
            "R1 a b 1k\n"
            ".end\n"
            "R2 c d 2k\n"                         # should be dropped
        )
        p = tmp_path / "stop.net"
        p.write_bytes(body.encode("utf-8"))
        net = parse_net(p)
        assert [e.name for e in net.elements] == ["R1"]

    def test_folds_plus_continuations(self, tmp_path):
        body = (
            "* pulse across two lines\n"
            "V1 a 0 PULSE(0 1\n"
            "+ 0 1u 1u 1m 2m)\n"
        )
        p = tmp_path / "cont.net"
        p.write_bytes(body.encode("utf-8"))
        net = parse_net(p)
        assert len(net.elements) == 1
        assert net.elements[0].tail == "PULSE(0 1 0 1u 1u 1m 2m)"

    def test_subcircuit_reference(self, tmp_path):
        body = (
            "* X1 subckt ref\n"
            "X1 in out vcc vee LT1001\n"
            ".end\n"
        )
        p = tmp_path / "x.net"
        p.write_bytes(body.encode("utf-8"))
        net = parse_net(p)
        (x1,) = net.elements
        assert x1.name == "X1"
        assert x1.nodes == ["in", "out", "vcc", "vee"]
        assert x1.tail == "LT1001"


# ----------------------------------------------------------------------
# write_net
# ----------------------------------------------------------------------

class TestWriteNet:
    def test_roundtrip_rc(self, tmp_path):
        src = parse_net(_FIXTURES / "ltspice_good.net")
        out = tmp_path / "rc.net"
        write_net(src, out)
        back = parse_net(out)

        assert back.title == src.title
        assert [(e.name, e.nodes, e.tail) for e in back.elements] == [
            (e.name, e.nodes, e.tail) for e in src.elements
        ]
        assert [(d.command, d.args) for d in back.directives] == [
            (d.command, d.args) for d in src.directives
        ]

    def test_emits_crlf(self, tmp_path):
        n = Netlist(title="t", elements=[Element("R1", ["a", "b"], "1k")])
        p = tmp_path / "crlf.net"
        write_net(n, p)
        raw = p.read_bytes()
        assert b"\r\n" in raw
        for i, b in enumerate(raw):
            if b == 0x0A:
                assert i > 0 and raw[i - 1] == 0x0D

    def test_emits_dot_end_sentinel(self, tmp_path):
        p = tmp_path / "end.net"
        write_net(Netlist(title="x"), p)
        assert p.read_text(encoding="utf-8").strip().endswith(".end")


# ----------------------------------------------------------------------
# schematic_to_netlist — in-memory catalog
# ----------------------------------------------------------------------

def _fake_catalog(defs: list[SymbolDef]) -> SymbolCatalog:
    """Wrap a list of SymbolDefs as a faked catalog (bypass disk walk)."""
    cat = SymbolCatalog.__new__(SymbolCatalog)
    cat._search_paths = []
    cat._index = {}
    cat._cache = {d.name.casefold(): d for d in defs}

    def _find(name, _self=cat):
        return _self._cache.get(name.casefold())

    cat.find = _find   # type: ignore[assignment]
    return cat


_RES_DEF = SymbolDef(
    name="res",
    path=Path("/fake/res.asy"),
    prefix="R",
    pins=[Pin(name="1", x=0, y=0, spice_order=1),
          Pin(name="2", x=0, y=80, spice_order=2)],
)
_CAP_DEF = SymbolDef(
    name="cap",
    path=Path("/fake/cap.asy"),
    prefix="C",
    pins=[Pin(name="1", x=0, y=0, spice_order=1),
          Pin(name="2", x=0, y=64, spice_order=2)],
)
_VSRC_DEF = SymbolDef(
    name="voltage",
    path=Path("/fake/voltage.asy"),
    prefix="V",
    pins=[Pin(name="+", x=0, y=0, spice_order=1),
          Pin(name="-", x=0, y=96, spice_order=2)],
)


class TestFlattener:
    def _rc_lowpass(self) -> Schematic:
        """RC low-pass wired so pins coincide with wire endpoints exactly.

        Layout (coords in schematic units; y grows downward).

        V1 at (128, 96) R0: pin1 world=(128,96) ["in"], pin2=(128,192) ["0"].
        R1 at (144, 96) R270: local (0,0) R270 → (0,0); local (0,80) → (80,0).
            pin1 world=(144,96) ["in"], pin2=(224,96) ["out"].
        C1 at (224, 96) R0: pin1=(224,96) ["out"], pin2=(224,160) ["0"].
        """
        s = Schematic()
        s.wires.extend([
            Wire(128, 96, 144, 96),      # V1 top → R1 left ("in")
            Wire(128, 192, 224, 160),    # ground rail connecting V1- and C1-
        ])
        s.flags.extend([
            Flag(128, 96, "in"),
            Flag(224, 96, "out"),
            Flag(128, 192, "0"),
            Flag(224, 160, "0"),
        ])
        s.symbols.extend([
            Placement("voltage", 128, 96, Rotation.R0,
                      attrs={"InstName": "V1",
                             "Value": "PULSE(0 1 0 1u 1u 1m 2m)"}),
            Placement("res", 144, 96, Rotation.R270,
                      attrs={"InstName": "R1", "Value": "1k"}),
            Placement("cap", 224, 96, Rotation.R0,
                      attrs={"InstName": "C1", "Value": "100n"}),
        ])
        s.texts.append(
            TextDirective(0, 0, "Left", 2, ".tran 5m", TextKind.SPICE)
        )
        return s

    def test_emits_expected_elements(self):
        cat = _fake_catalog([_RES_DEF, _CAP_DEF, _VSRC_DEF])
        schem = self._rc_lowpass()
        net = schematic_to_netlist(schem, cat)

        by_name = {e.name: e for e in net.elements}
        assert set(by_name) == {"V1", "R1", "C1"}

        assert by_name["V1"].nodes == ["in", "0"]
        assert by_name["V1"].tail == "PULSE(0 1 0 1u 1u 1m 2m)"
        assert by_name["R1"].nodes == ["in", "out"]
        assert by_name["R1"].tail == "1k"
        assert by_name["C1"].nodes == ["out", "0"]
        assert by_name["C1"].tail == "100n"

    def test_spice_directive_text_promoted(self):
        cat = _fake_catalog([_RES_DEF, _CAP_DEF, _VSRC_DEF])
        net = schematic_to_netlist(self._rc_lowpass(), cat)
        assert any(d.command == ".tran" for d in net.directives)

    def test_missing_symbol_raises(self):
        s = Schematic()
        s.symbols.append(
            Placement("does_not_exist", 0, 0, Rotation.R0,
                      attrs={"InstName": "U1"})
        )
        with pytest.raises(FlattenError, match="not found in catalog"):
            schematic_to_netlist(s, _fake_catalog([]))

    def test_missing_instname_raises(self):
        s = Schematic()
        s.symbols.append(Placement("res", 0, 0, Rotation.R0, attrs={}))
        with pytest.raises(FlattenError, match="InstName"):
            schematic_to_netlist(s, _fake_catalog([_RES_DEF]))

    def test_unlabeled_nets_get_auto_names(self):
        """A floating wire between two passives should become Nxxx."""
        cat = _fake_catalog([_RES_DEF])
        s = Schematic()
        s.wires.append(Wire(0, 0, 0, 80))        # R1 top to R2 top
        s.wires.append(Wire(0, 160, 0, 240))     # R1 bottom to R2 bottom... not connected here
        # Two resistors in series with no flags at all.
        s.symbols.extend([
            Placement("res", 0, 0, Rotation.R0, attrs={"InstName": "R1"}),
            Placement("res", 0, 80, Rotation.R0, attrs={"InstName": "R2"}),
        ])
        net = schematic_to_netlist(s, cat)
        r_nodes = {e.name: e.nodes for e in net.elements}
        # R1 and R2 share the middle net; both should have two auto nets.
        assert r_nodes["R1"][1] == r_nodes["R2"][0]
        for n in r_nodes["R1"] + r_nodes["R2"]:
            assert n.startswith("N")


# ----------------------------------------------------------------------
# Rotation math
# ----------------------------------------------------------------------

class TestPinRotation:
    """Rotations are exercised via the public flattener to avoid coupling
    tests to the private `_rotate_pin` helper.
    """

    @pytest.mark.parametrize(
        "rot,expected",
        [
            (Rotation.R0, (0, 80)),
            (Rotation.R90, (-80, 0)),
            (Rotation.R180, (0, -80)),
            (Rotation.R270, (80, 0)),
            (Rotation.M0, (0, 80)),      # mirror of (0,0) is (0,0); pin (0,80) flips x → still (0,80)
            (Rotation.M90, (80, 0)),
            (Rotation.M180, (0, -80)),
            (Rotation.M270, (-80, 0)),
        ],
    )
    def test_single_pin_world_pos(self, rot, expected):
        """Confirm pin2 of a resistor (local (0,80)) lands where we expect
        when placed at origin under each rotation."""
        cat = _fake_catalog([_RES_DEF])
        s = Schematic()
        s.symbols.append(
            Placement("res", 0, 0, rot, attrs={"InstName": "R1"})
        )
        # Add a flag exactly where pin2 should land — it must resolve to
        # that label.
        s.flags.append(Flag(expected[0], expected[1], "tgt"))
        net = schematic_to_netlist(s, cat)
        (r,) = net.elements
        # Pin 2 is the second node emitted.
        assert r.nodes[1] == "tgt"
