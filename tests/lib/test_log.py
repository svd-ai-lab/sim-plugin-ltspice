"""Unit tests for sim_plugin_ltspice.lib.log."""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_plugin_ltspice.lib.log import parse_log, read_log

FIXTURES = Path(__file__).parent / "fixtures"


class TestReadLogEncoding:
    def test_utf16_le_no_bom(self, tmp_path):
        p = tmp_path / "mac.log"
        p.write_bytes("vout_pk: MAX(v(out))=0.999 FROM 0 TO 0.005\n".encode("utf-16-le"))
        assert "vout_pk" in read_log(p)

    def test_utf8_plain(self, tmp_path):
        p = tmp_path / "win.log"
        p.write_text(
            "LTspice 26.0.1 for Windows\n"
            "vout_pk: MAX(V(out))=0.999 FROM 0 TO 0.005\n",
            encoding="utf-8",
        )
        assert "vout_pk" in read_log(p)

    def test_utf8_with_bom(self, tmp_path):
        p = tmp_path / "bom.log"
        p.write_bytes("\ufeffvout_pk: MAX(v(out))=1.0\n".encode("utf-8"))
        assert read_log(p).startswith("vout_pk")

    def test_missing_file(self, tmp_path):
        assert read_log(tmp_path / "does-not-exist.log") == ""


class TestParseLog:
    def test_measure_with_from_to(self):
        text = (
            "solver = Normal\n"
            "vout_pk: MAX(v(out))=0.999955 FROM 0 TO 0.005\n"
            "Total elapsed time: 0.003 seconds.\n"
        )
        out = parse_log(text)
        m = out.measures["vout_pk"]
        assert m.value == pytest.approx(0.999955)
        assert m.window_from == 0.0
        assert m.window_to == 0.005
        assert m.expr == "MAX(v(out))"
        assert out.elapsed_s == pytest.approx(0.003)
        assert out.errors == []
        assert out.warnings == []

    def test_measure_with_unit_suffix(self):
        text = "gain: V(out)/V(in)=2.5V\n"
        assert parse_log(text).measures["gain"].value == pytest.approx(2.5)

    def test_errors_flagged(self):
        text = (
            "Error: convergence failed at step 1\n"
            "Singular matrix\n"
            "Total elapsed time: 0.001 seconds.\n"
        )
        out = parse_log(text)
        assert len(out.errors) >= 1

    def test_warnings_flagged(self):
        out = parse_log("WARNING: node N001 floating\nOK\n")
        assert len(out.warnings) == 1
        assert "floating" in out.warnings[0]

    def test_windows_drive_letter_is_not_a_measure(self):
        text = (
            "LTspice 26.0.1 for Windows\n"
            "Files loaded:\n"
            "C:\\Users\\jiwei\\tmp\\rc.net\n"
            "\n"
            "vout_pk: MAX(V(out))=0.999954938889 FROM 0 TO 0.005\n"
            "Total elapsed time: 0.061 seconds.\n"
        )
        out = parse_log(text)
        assert list(out.measures.keys()) == ["vout_pk"]
        assert out.measures["vout_pk"].value == pytest.approx(0.999955, rel=1e-4)
        assert out.measures["vout_pk"].expr == "MAX(V(out))"


class TestParseAcMeasures:
    """AC analysis produces complex-valued results and `AT <freq>`
    suffixes that the original scalar-only parser silently dropped.
    Regression coverage for
    https://github.com/svd-ai-lab/sim-ltspice/issues/<TBD>.
    """

    def test_macos_ac_fixture(self):
        """The macOS LTspice 17.2.4 log for rlc_ac.net — real bytes
        off disk, UTF-16 LE no BOM, CRLF-terminated .meas block."""
        out = parse_log(FIXTURES / "rlc_ac_macos.log")
        names = set(out.measures)
        assert names == {"peakmag", "fr", "bw_3db_lo", "bw_3db_hi", "gain_5k"}

        # AC MAX → magnitude in dB (from the complex tuple)
        peakmag = out.measures["peakmag"]
        assert peakmag.value == pytest.approx(-0.0123613)
        assert peakmag.phase_deg == pytest.approx(0.0)
        assert peakmag.window_from == pytest.approx(100.0)
        assert peakmag.window_to == pytest.approx(100000.0)
        assert peakmag.at is None

        # AC WHEN: RHS is the condition the user wrote; the answer
        # (frequency where condition holds) is after AT. The target
        # itself is preserved as ``rhs_value``.
        fr = out.measures["fr"]
        assert fr.value == pytest.approx(5035.04)
        assert fr.at == pytest.approx(5035.04)
        assert fr.rhs_value == pytest.approx(0.0)
        assert fr.phase_deg is None
        assert fr.expr == "ph(v(out))"

        # AC WHEN with an expression RHS referencing another measure.
        assert out.measures["bw_3db_lo"].value == pytest.approx(4644.92)
        assert out.measures["bw_3db_hi"].value == pytest.approx(5451.9)

        # AC FIND…AT: RHS is a complex magnitude (dB); value comes
        # from RHS, not from AT (AT is the requested frequency).
        gain_5k = out.measures["gain_5k"]
        assert gain_5k.value == pytest.approx(-0.0893997)
        assert gain_5k.phase_deg == pytest.approx(0.0)
        assert gain_5k.at == pytest.approx(5030.0)

        assert out.elapsed_s == pytest.approx(0.010)
        assert out.errors == []

    def test_ac_complex_inline(self):
        """Synthesized AC MAX with complex dB result, no file I/O."""
        text = "peakmag: MAX(mag(v(out)))=(-0.5dB,12.3°) FROM 100 TO 1000\n"
        m = parse_log(text).measures["peakmag"]
        assert m.value == pytest.approx(-0.5)
        assert m.phase_deg == pytest.approx(12.3)

    def test_ac_when_scalar_target(self):
        """``fr: ph(V(out))=0 AT 5035`` — WHEN form, value is AT freq."""
        m = parse_log("fr: ph(V(out))=0 AT 5035\n").measures["fr"]
        assert m.value == pytest.approx(5035.0)
        assert m.at == pytest.approx(5035.0)

    def test_ac_when_expression_target(self):
        """RHS is ``peakmag*0.7071`` — non-numeric expression. The
        scalar-only regex rejected these outright. Value comes from AT.
        """
        m = parse_log(
            "bw_lo: mag(V(out))=peakmag*0.7071 AT 4644.92\n"
        ).measures["bw_lo"]
        assert m.value == pytest.approx(4644.92)
        assert m.at == pytest.approx(4644.92)

    def test_ac_find_at_lowercase_at(self):
        """LTspice sometimes writes ``at`` lowercase."""
        m = parse_log(
            "gain: mag(V(out))=(-0.089dB,0°) at 5030\n"
        ).measures["gain"]
        assert m.value == pytest.approx(-0.089)
        assert m.at == pytest.approx(5030.0)

    def test_crlf_line_endings(self):
        """The macOS LTspice `.meas` block is CRLF even though the
        preamble is LF. A pattern anchored to ``$`` misses CRLF lines
        under ``re.MULTILINE`` — regression for that bug."""
        text = (
            "solver = Normal\n"
            "peakmag: MAX(v(out))=0.5 FROM 100 TO 1000\r\n"
            "fr: ph(v(out))=0 AT 1000\r\n"
            "Total elapsed time: 0.01 seconds.\n"
        )
        out = parse_log(text)
        assert set(out.measures) == {"peakmag", "fr"}
        assert out.measures["peakmag"].value == pytest.approx(0.5)
        assert out.measures["fr"].value == pytest.approx(1000.0)

    def test_tran_find_at_ambiguity_preserves_rhs(self):
        """TRAN ``FIND V(out) AT 1ms`` logs as ``V(out)=0.5 AT 1e-3``,
        identical to TRAN ``WHEN V(out)=0.5``. Without the original
        directive we can't tell them apart, so ``value`` defaults to
        the WHEN interpretation (axis point) and ``rhs_value``
        preserves the measured scalar for callers who wrote FIND.
        This test pins that contract — if you change the heuristic,
        update it here and in ``Measure``'s docstring.
        """
        m = parse_log("vpk: V(out)=0.5 AT 1e-3\n").measures["vpk"]
        assert m.value == pytest.approx(1e-3)  # WHEN interpretation
        assert m.at == pytest.approx(1e-3)
        assert m.rhs_value == pytest.approx(0.5)  # recoverable

    def test_circuit_line_with_equals_is_not_a_measure(self):
        """LTspice's ``Circuit:`` echo can contain ``=`` in a
        netlist comment (e.g. ``fr = 1/(2 pi sqrt(LC))``); the body
        parser must reject non-numeric RHS."""
        text = (
            "Circuit: * RLC, resonant at fr = 1/(2 pi sqrt(LC)) ≈ 5.03 kHz\n"
            "vout_pk: MAX(V(out))=0.999 FROM 0 TO 0.005\n"
        )
        out = parse_log(text)
        assert list(out.measures) == ["vout_pk"]
