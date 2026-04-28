"""Unit tests for sim_plugin_ltspice.lib.asc (read/write) and schematic model."""
from __future__ import annotations

from pathlib import Path

from sim_plugin_ltspice.lib.asc import read_asc, write_asc
from sim_plugin_ltspice.lib.schematic import (
    Flag,
    Placement,
    Rotation,
    Schematic,
    TextDirective,
    TextKind,
    Wire,
)


def _build_rc_lowpass() -> Schematic:
    """Hand-built RC low-pass matching the tests/fixtures/ltspice_good.net."""
    s = Schematic()
    s.wires.extend([
        Wire(128, 96, 224, 96),     # in to R1 left
        Wire(336, 96, 432, 96),     # R1 right to out
        Wire(432, 96, 432, 160),    # out down to C1 top
        Wire(432, 224, 432, 272),   # C1 bottom to ground
        Wire(128, 96, 128, 272),    # in source (+) to source
        Wire(128, 272, 432, 272),   # bottom rail
    ])
    s.flags.extend([
        Flag(432, 272, "0"),        # ground
        Flag(432, 96, "out"),       # labelled net
    ])
    s.symbols.extend([
        Placement(
            "voltage", 128, 176, Rotation.R0,
            attrs={"InstName": "V1", "Value": "PULSE(0 1 0 1u 1u 1m 2m)"},
        ),
        Placement(
            "res", 224, 80, Rotation.R90,
            attrs={"InstName": "R1", "Value": "1k"},
        ),
        Placement(
            "cap", 416, 160, Rotation.R0,
            attrs={"InstName": "C1", "Value": "100n"},
        ),
    ])
    s.texts.extend([
        TextDirective(64, 320, "Left", 2, ".tran 5m", TextKind.SPICE),
        TextDirective(64, 352, "Left", 2, ".meas TRAN vout_pk MAX V(out)",
                      TextKind.SPICE),
        TextDirective(64, 384, "Left", 2, "RC low-pass", TextKind.COMMENT),
    ])
    return s


class TestWriteAsc:
    def test_emits_version_4_header(self, tmp_path):
        s = Schematic()
        path = tmp_path / "empty.asc"
        write_asc(s, path)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "Version 4"
        assert lines[1] == "SHEET 1 880 680"

    def test_emits_crlf_line_endings(self, tmp_path):
        """LTspice 26 on Windows rejects LF-only .asc with -netlist."""
        s = _build_rc_lowpass()
        path = tmp_path / "rc.asc"
        write_asc(s, path)
        raw = path.read_bytes()
        assert b"\r\n" in raw
        # No stray bare-LF lines (every \n must be preceded by \r).
        for i, b in enumerate(raw):
            if b == 0x0A:
                assert i > 0 and raw[i - 1] == 0x0D, \
                    f"bare LF at byte {i}"

    def test_rc_lowpass_structure(self, tmp_path):
        s = _build_rc_lowpass()
        path = tmp_path / "rc.asc"
        write_asc(s, path)
        body = path.read_text(encoding="utf-8")

        # Statement ordering: WIREs → FLAGs → SYMBOLs → TEXTs
        assert body.index("WIRE ") < body.index("FLAG ")
        assert body.index("FLAG ") < body.index("SYMBOL ")
        assert body.index("SYMBOL ") < body.index("TEXT ")

        # Each SYMBOL is followed by its SYMATTRs (InstName then Value).
        assert "SYMBOL voltage 128 176 R0" in body
        assert "SYMATTR InstName V1" in body
        assert "SYMATTR Value PULSE(0 1 0 1u 1u 1m 2m)" in body

        # TEXT prefixes reflect kind.
        assert "TEXT 64 320 Left 2 !.tran 5m" in body
        assert "TEXT 64 384 Left 2 ;RC low-pass" in body


class TestReadAsc:
    def test_roundtrip_rc_lowpass(self, tmp_path):
        original = _build_rc_lowpass()
        path = tmp_path / "rc.asc"
        write_asc(original, path)
        parsed = read_asc(path)

        assert parsed.version == original.version
        assert parsed.sheet == original.sheet
        assert len(parsed.wires) == len(original.wires)
        assert len(parsed.flags) == len(original.flags)
        assert len(parsed.symbols) == len(original.symbols)
        assert len(parsed.texts) == len(original.texts)

        # Deep equality on representative structures.
        assert parsed.wires[0] == original.wires[0]
        assert parsed.flags[0] == original.flags[0]
        assert parsed.symbols[0].symbol == original.symbols[0].symbol
        assert parsed.symbols[0].attrs == original.symbols[0].attrs
        assert parsed.symbols[0].rotation == original.symbols[0].rotation
        assert parsed.texts[0].kind == TextKind.SPICE
        assert parsed.texts[0].text == ".tran 5m"
        assert parsed.texts[2].kind == TextKind.COMMENT

    def test_unknown_statement_preserved(self, tmp_path):
        """Decorative LINE / RECTANGLE / IOPIN etc. must round-trip verbatim."""
        body = (
            "Version 4\n"
            "SHEET 1 880 680\n"
            "WIRE 0 0 16 0\n"
            "LINE Normal 16 0 32 0\n"                 # decorative
            "RECTANGLE Normal 0 0 32 32\n"            # decorative
            "IOPIN 0 0 BiDir\n"                       # hierarchical port
        )
        path = tmp_path / "x.asc"
        path.write_bytes(body.encode("utf-8"))
        parsed = read_asc(path)
        assert any("LINE Normal" in line for line in parsed.raw_tail)
        assert any("RECTANGLE" in line for line in parsed.raw_tail)
        assert any("IOPIN" in line for line in parsed.raw_tail)

        # Round-trip preserves the unknown lines.
        out = tmp_path / "x_out.asc"
        write_asc(parsed, out)
        round_tripped = out.read_text(encoding="utf-8")
        assert "LINE Normal 16 0 32 0" in round_tripped
        assert "RECTANGLE Normal 0 0 32 32" in round_tripped
        assert "IOPIN 0 0 BiDir" in round_tripped

    def test_version_3_accepted(self, tmp_path):
        body = "Version 3\nSHEET 1 440 320\n"
        p = tmp_path / "old.asc"
        p.write_bytes(body.encode("utf-8"))
        schem = read_asc(p)
        assert schem.version == 3
        assert schem.sheet == (1, 440, 320)

    def test_utf16_le_asc_reads(self, tmp_path):
        """macOS-native LTspice writes some files as UTF-16 LE."""
        body = "Version 4\nSHEET 1 880 680\nWIRE 0 0 16 0\n"
        p = tmp_path / "macutf16.asc"
        p.write_bytes(body.encode("utf-16-le"))
        schem = read_asc(p)
        assert schem.version == 4
        assert len(schem.wires) == 1

    def test_roundtrip_real_ltspice_file(self, tmp_path):
        """Byte-identical round-trip of a shipped LTspice example.

        Guards against writer regressions that corrupt the native format
        (LF instead of CRLF, reordered SYMATTRs, lost decorative graphics).
        The fixture is a verbatim copy of
        ``LTspice/examples/Educational/MonteCarlo.asc``.
        """
        fixture = Path(__file__).parent / "fixtures" / "montecarlo.asc"
        original_bytes = fixture.read_bytes()

        schem = read_asc(fixture)
        out = tmp_path / "montecarlo_rewritten.asc"
        write_asc(schem, out)

        assert out.read_bytes() == original_bytes


class TestRotation:
    def test_round_trip_all_rotations(self, tmp_path):
        s = Schematic()
        for i, rot in enumerate(Rotation):
            s.symbols.append(
                Placement("res", 0, i * 80, rot, attrs={"InstName": f"R{i}"})
            )
        p = tmp_path / "rot.asc"
        write_asc(s, p)
        s2 = read_asc(p)
        assert len(s2.symbols) == len(list(Rotation))
        assert [sym.rotation for sym in s2.symbols] == list(Rotation)
