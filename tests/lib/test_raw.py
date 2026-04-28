"""Tests for sim_plugin_ltspice.lib.raw.RawRead — binary `.raw` waveform parser.

Fixtures in ``tests/fixtures/raw/`` were produced on macOS LTspice 17.2.4
from the netlists in ``tmp/`` of the sim-proj workspace; they cover
the five layouts RawRead must decode:

- ``tran_rc`` — ``Flags: real forward`` (default transient)
- ``op_rdiv`` — ``Flags: real`` (``.op``: axis-less single-point)
- ``ac_rlc`` — ``Flags: complex forward log`` (complex128 everywhere)
- ``step_rc`` — ``Flags: real forward stepped`` (param sweep concatenated)
- ``noise_rc`` — ``Flags: real forward log`` (``.noise`` with gain trace)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sim_plugin_ltspice.lib import (
    InvalidExpression,
    RawRead,
    UnsupportedRawFormat,
    trace_names,
)

FIX = Path(__file__).parent / "fixtures" / "raw"


class TestTransientDefault:
    """Default LTspice layout: float64 time axis + float32 signal traces."""

    def setup_method(self):
        self.rr = RawRead(FIX / "tran_rc.raw")

    def test_metadata(self):
        assert self.rr.plotname == "Transient Analysis"
        assert self.rr.flags == {"real", "forward"}
        assert self.rr.n_points == 321
        assert self.rr.n_variables == 6

    def test_trace_names_match_header(self):
        assert self.rr.trace_names() == [
            "time", "V(in)", "V(out)", "I(C1)", "I(R1)", "I(V1)",
        ]

    def test_axis_is_monotone_non_decreasing(self):
        t = self.rr.axis
        assert t.dtype == np.float64
        assert t[0] == 0.0
        assert t[-1] == pytest.approx(0.005, rel=1e-6)
        # Transient time axis is monotonic (compressed points already
        # absolute-valued in __init__).
        assert np.all(np.diff(t) >= -1e-15)

    def test_trace_returns_float64(self):
        v_out = self.rr.trace("V(out)")
        assert v_out.dtype == np.float64
        assert v_out.shape == (321,)

    def test_pulse_ramps_high(self):
        """V(in) is a PULSE(0,1,...) — must cross 0.5 at least once."""
        v_in = self.rr.trace("V(in)")
        assert v_in.max() >= 0.99

    def test_rc_filter_attenuates_edge(self):
        """The RC filter smooths the pulse — V(out) peak <= V(in) peak."""
        assert self.rr.trace("V(out)").max() <= self.rr.trace("V(in)").max() + 1e-6

    def test_trace_missing_raises(self):
        with pytest.raises(KeyError, match="not found"):
            self.rr.trace("V(no_such_node)")

    def test_case_insensitive_fallback(self):
        """LTspice is inconsistent about case — make sure lookup copes."""
        low = self.rr.trace("v(out)")
        exact = self.rr.trace("V(out)")
        np.testing.assert_array_equal(low, exact)


class TestOperatingPoint:
    """``.op`` produces 1 point but still uses the real-default layout."""

    def setup_method(self):
        self.rr = RawRead(FIX / "op_rdiv.raw")

    def test_metadata(self):
        assert self.rr.plotname == "Operating Point"
        assert self.rr.flags == {"real"}
        assert self.rr.n_points == 1

    def test_divider_values(self):
        """V1 in 0 5 / R1 in mid 1k / R2 mid 0 2k → V(mid)=10/3 V."""
        assert self.rr.trace("V(in)")[0] == pytest.approx(5.0, rel=1e-6)
        assert self.rr.trace("V(mid)")[0] == pytest.approx(10.0 / 3.0, rel=1e-5)
        # Current through divider: 5 V / 3 kΩ = 1.6666... mA
        assert self.rr.trace("I(R1)")[0] == pytest.approx(5e-3 / 3.0, rel=1e-4)

    def test_not_complex(self):
        assert self.rr.is_complex is False


class TestACComplex:
    """``.ac`` stores complex128 for both axis and all traces."""

    def setup_method(self):
        self.rr = RawRead(FIX / "ac_rlc.raw")

    def test_metadata(self):
        assert self.rr.plotname == "AC Analysis"
        assert self.rr.flags == {"complex", "forward", "log"}
        assert self.rr.is_complex is True

    def test_axis_is_complex_but_real_valued(self):
        """Frequency is declared complex but imaginary parts must be zero."""
        f = self.rr.axis
        assert f.dtype == np.complex128
        assert np.allclose(f.imag, 0.0)
        assert f[0].real == pytest.approx(10.0)
        assert f[-1].real == pytest.approx(1e6)

    def test_source_amplitude_is_unity(self):
        """V1 in 0 AC 1 → V(in) ≡ 1+0j at every frequency."""
        v_in = self.rr.trace("V(in)")
        assert v_in.dtype == np.complex128
        assert np.allclose(v_in, 1 + 0j, atol=1e-9)

    def test_vout_has_phase_shift(self):
        """RLC band-pass must produce non-zero imaginary components."""
        v_out = self.rr.trace("V(out)")
        assert np.abs(v_out.imag).max() > 1e-3


class TestSteppedSweep:
    """``.step`` concatenates multiple sweeps into one body."""

    def setup_method(self):
        self.rr = RawRead(FIX / "step_rc.raw")

    def test_metadata(self):
        assert self.rr.plotname == "Transient Analysis"
        assert self.rr.is_stepped is True
        assert self.rr.n_points == 699

    def test_axis_resets_per_step(self):
        """Monotonicity breaks at step boundaries; at least 2 resets for 3 steps."""
        t = self.rr.axis
        # Points where the axis decreases signal a new sweep.
        decreases = np.sum(np.diff(t) < 0)
        assert decreases >= 2


class TestNoise:
    """``.noise`` is a real-forward log sweep with gain/V(onoise)/V(inoise)."""

    def setup_method(self):
        self.rr = RawRead(FIX / "noise_rc.raw")

    def test_metadata(self):
        assert self.rr.plotname.startswith("Noise Spectral Density")
        assert self.rr.flags == {"real", "forward", "log"}
        assert self.rr.output == "out"

    def test_gain_and_noise_traces(self):
        names = self.rr.trace_names()
        # frequency + gain + the noise contribution traces
        assert names[:2] == ["frequency", "gain"]
        # RC low-pass: gain near DC ≈ 1.
        assert self.rr.trace("gain")[0] == pytest.approx(1.0, abs=1e-3)


class TestUnsupported:
    def _forge_header(self, flags: str) -> bytes:
        """Build a minimal UTF-16 LE header with arbitrary Flags."""
        header = (
            f"Title: forged\n"
            f"Plotname: Operating Point\n"
            f"Flags: {flags}\n"
            f"No. Variables: 1\n"
            f"No. Points: 1\n"
            f"Offset: 0\n"
            f"Command: sim-ltspice test\n"
            f"Variables:\n"
            f"\t0\tV(x)\tvoltage\n"
            f"Binary:\n"
        )
        return header.encode("utf-16-le")

    def test_fastaccess_rejected(self, tmp_path):
        p = tmp_path / "fast.raw"
        # one float64 value so the size check doesn't fire first.
        p.write_bytes(self._forge_header("real fastaccess") + b"\x00" * 8)
        with pytest.raises(UnsupportedRawFormat, match="fastaccess"):
            RawRead(p)

    def test_missing_sentinel_rejected(self, tmp_path):
        p = tmp_path / "garbage.raw"
        p.write_bytes("Not an LTspice .raw file\n".encode("utf-16-le"))
        with pytest.raises(UnsupportedRawFormat, match="sentinel"):
            RawRead(p)

    def test_size_mismatch_rejected(self, tmp_path):
        p = tmp_path / "short.raw"
        # Header declares 1 point but body is empty.
        p.write_bytes(self._forge_header("real") + b"")
        with pytest.raises(UnsupportedRawFormat, match="body size"):
            RawRead(p)


class TestBackCompat:
    def test_trace_names_function_still_works(self):
        """The pre-v0.2 module-level ``trace_names`` helper must keep working."""
        names = trace_names(FIX / "tran_rc.raw")
        assert names == ["time", "V(in)", "V(out)", "I(C1)", "I(R1)", "I(V1)"]


class TestAsciiBody:
    """ASCII `.raw` ('Values:' sentinel) — same API as binary.

    Fixtures in ``ascii_*.raw`` were generated from the binary fixtures
    using the tab-separated / comma-separated-complex format documented
    in spicelib's parser. LTspice has no batch CLI switch to force
    ASCII output, so emitting the file from a known-good binary
    exercises the ASCII decoder against real LTspice conventions.
    """

    def test_ascii_transient_matches_binary(self):
        b = RawRead(FIX / "tran_rc.raw")
        a = RawRead(FIX / "ascii_tran_rc.raw")
        assert b.trace_names() == a.trace_names()
        assert b.n_points == a.n_points
        np.testing.assert_allclose(a.axis, b.axis)
        for name in b.trace_names()[1:]:
            np.testing.assert_allclose(
                a.trace(name), b.trace(name), rtol=1e-6, atol=1e-12
            )

    def test_ascii_ac_matches_binary(self):
        b = RawRead(FIX / "ac_rlc.raw")
        a = RawRead(FIX / "ascii_ac_rlc.raw")
        for name in b.trace_names():
            # 15-digit scientific round-trip ≈ 1e-15 relative error.
            np.testing.assert_allclose(
                a.trace(name), b.trace(name), rtol=1e-10, atol=1e-14
            )

    def test_ascii_flags_preserve_complex_dtype(self):
        a = RawRead(FIX / "ascii_ac_rlc.raw")
        assert a.is_complex is True
        assert a.trace("V(in)").dtype == np.complex128

    def test_ascii_point_index_out_of_order_raises(self, tmp_path):
        """Corrupt the index of the second point — decoder must reject."""
        raw = (FIX / "ascii_tran_rc.raw").read_bytes()
        text = raw.decode("utf-16-le")
        # Replace the first "1\t" tag (marker for point 1) with "99\t".
        marker = "1\t"
        i = text.index("Values:\n") + len("Values:\n")
        # Skip past the first point's n_variables lines to land on the
        # "1\t" of point 1 specifically.
        for _ in range(6):
            i = text.index("\n", i) + 1
        assert text[i : i + len(marker)] == marker
        corrupted = text[:i] + "99\t" + text[i + len(marker) :]
        bad = tmp_path / "corrupt.raw"
        bad.write_bytes(corrupted.encode("utf-16-le"))
        with pytest.raises(UnsupportedRawFormat, match="out of order"):
            RawRead(bad)


class TestCursors:
    def setup_method(self):
        self.tran = RawRead(FIX / "tran_rc.raw")
        self.ac = RawRead(FIX / "ac_rlc.raw")
        self.step = RawRead(FIX / "step_rc.raw")

    def test_max_matches_numpy(self):
        arr = self.tran.trace("V(out)")
        assert self.tran.max("V(out)") == pytest.approx(float(arr.max()))

    def test_min_on_real_trace(self):
        """V(in) starts at 0 and a PULSE ramps up — min is ≥ 0."""
        assert self.tran.min("V(in)") >= 0.0

    def test_mean_real(self):
        arr = self.tran.trace("V(in)")
        assert self.tran.mean("V(in)") == pytest.approx(float(arr.mean()))

    def test_rms_real(self):
        """RMS is sqrt(mean(x**2)); match a hand computation."""
        arr = self.tran.trace("V(in)")
        expect = float(np.sqrt((arr ** 2).mean()))
        assert self.tran.rms("V(in)") == pytest.approx(expect)

    def test_rms_complex_uses_magnitude(self):
        """For complex traces RMS must equal sqrt(mean(|x|**2))."""
        arr = self.ac.trace("V(out)")
        expect = float(np.sqrt((np.abs(arr) ** 2).mean()))
        assert self.ac.rms("V(out)") == pytest.approx(expect)

    def test_mean_complex_returns_complex(self):
        val = self.ac.mean("V(in)")
        assert isinstance(val, complex)
        # V(in) = 1+0j at every frequency → mean is exactly 1+0j.
        assert val == pytest.approx(1 + 0j, abs=1e-10)

    def test_sample_at_endpoint(self):
        """Sampling at axis[0] must return the first point exactly."""
        assert self.tran.sample_at("V(out)", 0.0) == pytest.approx(
            float(self.tran.trace("V(out)")[0])
        )
        assert self.tran.sample_at("V(out)", self.tran.axis[-1]) == pytest.approx(
            float(self.tran.trace("V(out)")[-1])
        )

    def test_sample_at_midpoint(self):
        """Linearly interpolate between two neighbouring points."""
        t = self.tran.axis
        v = self.tran.trace("V(out)")
        x = 0.5 * (t[100] + t[101])
        expect = 0.5 * (v[100] + v[101])
        assert self.tran.sample_at("V(out)", x) == pytest.approx(expect, rel=1e-6)

    def test_sample_at_out_of_range_raises(self):
        with pytest.raises(ValueError, match="outside range"):
            self.tran.sample_at("V(out)", 1.0)  # t_max is 5 ms

    def test_sample_at_stepped_raises(self):
        with pytest.raises(ValueError, match="stepped"):
            self.step.sample_at("V(out)", 0.001)

    def test_sample_at_complex_trace(self):
        """AC: interpolated V(in) must still be 1+0j (flat in freq)."""
        val = self.ac.sample_at("V(in)", 1e3)
        assert isinstance(val, complex)
        assert val == pytest.approx(1 + 0j, abs=1e-9)


class TestEval:
    def setup_method(self):
        self.rr = RawRead(FIX / "tran_rc.raw")
        self.ac = RawRead(FIX / "ac_rlc.raw")

    def test_difference(self):
        """V(out) - V(in) matches the hand-computed difference."""
        d = self.rr.eval("V(out) - V(in)")
        expected = self.rr.trace("V(out)") - self.rr.trace("V(in)")
        np.testing.assert_allclose(d, expected)

    def test_arithmetic_with_literal(self):
        """Numeric constants mix with trace refs."""
        d = self.rr.eval("2 * V(out) + 1")
        expected = 2 * self.rr.trace("V(out)") + 1
        np.testing.assert_allclose(d, expected)

    def test_literal_only_expression_broadcasts(self):
        """Expression without traces still aligns with axis length."""
        d = self.rr.eval("2.5 * 4")
        assert d.shape == (self.rr.n_points,)
        assert np.all(d == 10.0)

    def test_complex_preserved_when_ac_traces_used(self):
        """Any complex trace in the expression → complex128 result."""
        d = self.ac.eval("V(out) / V(in)")
        assert d.dtype == np.complex128

    def test_nested_parens_and_unary(self):
        d = self.rr.eval("-(V(out) - V(in)) * 0.5")
        expected = -(self.rr.trace("V(out)") - self.rr.trace("V(in)")) * 0.5
        np.testing.assert_allclose(d, expected)

    def test_rejects_function_call(self):
        with pytest.raises(InvalidExpression, match="Call"):
            self.rr.eval("abs(V(out))")

    def test_rejects_attribute_access(self):
        with pytest.raises(InvalidExpression, match="Attribute"):
            self.rr.eval("V(out).real")

    def test_rejects_comparison(self):
        with pytest.raises(InvalidExpression, match="Compare"):
            self.rr.eval("V(out) > 0")

    def test_rejects_boolean(self):
        with pytest.raises(InvalidExpression, match="BoolOp"):
            self.rr.eval("V(out) and 1")

    def test_rejects_string_literal(self):
        with pytest.raises(InvalidExpression, match="literal"):
            self.rr.eval("'malicious'")

    def test_rejects_subscript(self):
        with pytest.raises(InvalidExpression, match="Subscript"):
            self.rr.eval("V(out)[0]")

    def test_unknown_trace_raises_keyerror(self):
        """Missing trace names surface the normal KeyError, not InvalidExpression."""
        with pytest.raises(KeyError, match="not found"):
            self.rr.eval("V(foo) + V(bar)")

    def test_syntax_error_wrapped(self):
        with pytest.raises(InvalidExpression, match="could not be parsed"):
            self.rr.eval("V(out) +")


class TestCsvExport:
    def test_real_trace_roundtrip(self, tmp_path):
        rr = RawRead(FIX / "tran_rc.raw")
        path = rr.to_csv(tmp_path / "out.csv")
        assert path.exists()
        text = path.read_text()
        header = text.splitlines()[0]
        assert header == "time,V(in),V(out),I(C1),I(R1),I(V1)"
        # Spot-check the value at t=0 (all zeros for the pulse source).
        second_row = text.splitlines()[1].split(",")
        assert [float(x) for x in second_row] == [0.0] * 6

    def test_complex_expands_re_im(self, tmp_path):
        rr = RawRead(FIX / "ac_rlc.raw")
        path = rr.to_csv(tmp_path / "ac.csv")
        header = path.read_text().splitlines()[0].split(",")
        # First few columns must be frequency.re, frequency.im, V(in).re, V(in).im
        assert header[0] == "frequency.re"
        assert header[1] == "frequency.im"
        assert "V(in).re" in header
        assert "V(in).im" in header

    def test_creates_parent_dirs(self, tmp_path):
        rr = RawRead(FIX / "tran_rc.raw")
        path = rr.to_csv(tmp_path / "deep" / "nested" / "out.csv")
        assert path.exists()


class TestDataFrame:
    def setup_method(self):
        pytest.importorskip("pandas")
        self.rr = RawRead(FIX / "tran_rc.raw")
        self.ac = RawRead(FIX / "ac_rlc.raw")

    def test_real_dataframe_shape(self):
        df = self.rr.to_dataframe()
        assert df.shape == (self.rr.n_points, self.rr.n_variables - 1)
        assert df.index.name == "time"
        assert list(df.columns) == ["V(in)", "V(out)", "I(C1)", "I(R1)", "I(V1)"]

    def test_complex_preserved(self):
        df = self.ac.to_dataframe()
        assert df["V(in)"].dtype == np.complex128
        # Index is on the real part of the frequency column.
        assert df.index.name == "frequency"
        assert df.index.dtype == np.float64
