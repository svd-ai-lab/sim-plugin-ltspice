"""Tests for `sim_plugin_ltspice.lib.diff` — two-run .raw comparison."""
from __future__ import annotations

import copy
import dataclasses
from pathlib import Path

import numpy as np
import pytest

from sim_plugin_ltspice.lib import RawRead, diff
from sim_plugin_ltspice.lib.diff import DiffResult, TraceDiff


FIXTURES = Path(__file__).parent / "fixtures" / "raw"
TRAN = FIXTURES / "tran_rc.raw"
AC = FIXTURES / "ac_rlc.raw"
OP = FIXTURES / "op_rdiv.raw"


def _loaded(path: Path) -> RawRead:
    """Fresh RawRead — tests may mutate ._data on the copy."""
    return RawRead(path)


def _mutate_trace(rr: RawRead, name: str, delta: float | complex) -> RawRead:
    """Return a shallow copy of ``rr`` with one trace shifted by ``delta``.

    Shallow copy is fine because ``_data`` is a fresh ndarray we take a
    copy of before mutating — callers never see aliasing.
    """
    copy_rr = copy.copy(rr)
    copy_rr._data = rr._data.copy()
    idx = rr._index_of(name)
    copy_rr._data[:, idx] = copy_rr._data[:, idx] + delta
    return copy_rr


class TestSelfCompare:
    """A run compared against itself is trivially ok."""

    def test_tran_self(self):
        result = diff(TRAN, TRAN)
        assert result.ok
        assert result.axis_mismatch is None
        assert result.only_in_a == ()
        assert result.only_in_b == ()
        assert result.mismatched == ()
        # Every trace reports max_abs == 0.
        assert all(t.max_abs == 0.0 for t in result.traces)

    def test_ac_self(self):
        result = diff(AC, AC)
        assert result.ok
        assert all(t.within_tol for t in result.traces)

    def test_op_self(self):
        result = diff(OP, OP)
        assert result.ok


class TestTolerance:
    """atol/rtol semantics."""

    def test_small_shift_within_atol(self):
        a = _loaded(TRAN)
        b = _mutate_trace(a, "V(out)", 1e-12)
        result = diff(a, b, atol=1e-10, rtol=0.0)
        assert result.ok
        vout = next(t for t in result.traces if t.name == "V(out)")
        assert vout.max_abs == pytest.approx(1e-12, rel=1e-6)
        assert vout.within_tol is True

    def test_large_shift_fails(self):
        a = _loaded(TRAN)
        b = _mutate_trace(a, "V(out)", 0.1)
        result = diff(a, b, atol=0.0, rtol=1e-6)
        assert not result.ok
        assert len(result.mismatched) == 1
        assert result.mismatched[0].name == "V(out)"
        assert result.mismatched[0].max_abs == pytest.approx(0.1, rel=1e-6)

    def test_rtol_gates_pass(self):
        """A 1e-6-relative shift passes at rtol=1e-5 but fails at 1e-7."""
        a = _loaded(TRAN)
        # Multiply one trace by (1 + 1e-6) — relative shift 1e-6.
        b = copy.copy(a)
        b._data = a._data.copy()
        idx = a._index_of("V(out)")
        b._data[:, idx] = a._data[:, idx] * (1.0 + 1e-6)

        loose = diff(a, b, atol=0.0, rtol=1e-5)
        assert loose.ok

        tight = diff(a, b, atol=0.0, rtol=1e-7)
        assert not tight.ok


class TestTraceSetDifferences:
    """Traces present on only one side."""

    def test_missing_in_b_lands_in_only_in_a(self):
        a = _loaded(TRAN)
        b = _loaded(TRAN)
        # Drop the last variable in b by trimming its list + data matrix.
        drop = b.variables[-1]
        b.variables = b.variables[:-1]
        b._data = b._data[:, :-1]
        b.n_variables -= 1

        result = diff(a, b)
        assert drop.name in result.only_in_a
        assert result.only_in_b == ()
        assert not result.ok

    def test_traces_filter_restricts_comparison(self):
        a = _loaded(TRAN)
        b = _mutate_trace(a, "V(out)", 0.5)  # big shift on V(out)
        # Filter to a trace we know is unchanged ⇒ result is ok.
        names = [v.name for v in a.variables if v.index != 0]
        unchanged = next(n for n in names if n != "V(out)")
        result = diff(a, b, traces=[unchanged], atol=0.0, rtol=1e-6)
        assert result.ok
        assert [t.name for t in result.traces] == [unchanged]

    def test_requested_missing_trace_reported(self):
        result = diff(TRAN, TRAN, traces=["V(nonexistent)"])
        # Present in neither ⇒ reported on both sides so it's visible.
        assert "V(nonexistent)" in result.only_in_a
        assert "V(nonexistent)" in result.only_in_b
        assert not result.ok

    def test_filter_routes_names_to_correct_buckets(self):
        """Discriminates a swap of ``only_in_a`` vs ``only_in_b`` when
        ``traces=`` is given: drop one trace from each side, filter to
        both names, assert each name lands in the side it's missing
        from."""
        a = _loaded(TRAN)
        b = _loaded(TRAN)
        names = [v.name for v in a.variables if v.index != 0]
        # Need two non-axis traces to drop.
        assert len(names) >= 2
        drop_from_b = names[0]       # present in a, absent in b → only_in_a
        drop_from_a = names[-1]      # present in b, absent in a → only_in_b

        # Drop `drop_from_b` from b.
        b.variables = [v for v in b.variables if v.name != drop_from_b]
        keep_b_idx = [v.index for v in _loaded(TRAN).variables if v.name != drop_from_b]
        b._data = b._data[:, keep_b_idx]
        b.n_variables = len(b.variables)
        # Re-number indices on b to match new column positions.
        b.variables = [
            dataclasses.replace(v, index=i) for i, v in enumerate(b.variables)
        ]

        # Drop `drop_from_a` from a (symmetric).
        a.variables = [v for v in a.variables if v.name != drop_from_a]
        keep_a_idx = [v.index for v in _loaded(TRAN).variables if v.name != drop_from_a]
        a._data = a._data[:, keep_a_idx]
        a.n_variables = len(a.variables)
        a.variables = [
            dataclasses.replace(v, index=i) for i, v in enumerate(a.variables)
        ]

        result = diff(a, b, traces=[drop_from_b, drop_from_a])
        assert drop_from_b in result.only_in_a
        assert drop_from_b not in result.only_in_b
        assert drop_from_a in result.only_in_b
        assert drop_from_a not in result.only_in_a


class TestAxisMismatch:
    """Different axis lengths must be reported."""

    def test_different_lengths_flagged(self):
        a = _loaded(TRAN)
        b = _loaded(TRAN)
        # Truncate b's data in time.
        b._data = b._data[:10, :]
        b.n_points = 10

        result = diff(a, b)
        assert result.axis_mismatch is not None
        assert "length" in result.axis_mismatch.lower()
        assert not result.ok
        # Trace diffs should be infinite-delta placeholders.
        for t in result.traces:
            assert t.max_abs == float("inf")
            assert not t.within_tol

    def test_shifted_axis_flagged(self):
        a = _loaded(TRAN)
        b = copy.copy(a)
        b._data = a._data.copy()
        b._data[:, 0] = a._data[:, 0] + 1e-3  # shift time axis by 1 ms
        result = diff(a, b, atol=0.0, rtol=1e-6)
        assert result.axis_mismatch is not None
        assert "diverge" in result.axis_mismatch
        assert not result.ok


class TestComplexTraces:
    """AC analysis — complex128 traces."""

    def test_self_compare(self):
        assert diff(AC, AC).ok

    def test_imag_shift_fails(self):
        a = _loaded(AC)
        # Pick a complex trace, shift it by +0.1j.
        complex_name = next(
            v.name for v in a.variables if v.index != 0 and a.trace(v.name).dtype == np.complex128
        )
        b = _mutate_trace(a, complex_name, 0.1j)
        result = diff(a, b, atol=0.0, rtol=1e-6)
        assert not result.ok
        bad = next(t for t in result.mismatched if t.name == complex_name)
        assert bad.max_abs == pytest.approx(0.1, rel=1e-6)


class TestInputs:
    """Accept both Path and RawRead."""

    def test_pathlike_strings_accepted(self):
        result = diff(str(TRAN), str(TRAN))
        assert result.ok

    def test_prebuilt_rawread_accepted(self):
        a = RawRead(TRAN)
        b = RawRead(TRAN)
        result = diff(a, b)
        assert result.ok

    def test_mixed_path_and_rawread(self):
        a = RawRead(TRAN)
        result = diff(a, TRAN)
        assert result.ok


class TestDataclassShape:
    """Public dataclass surface contract."""

    def test_traces_is_tuple_of_tracediff(self):
        result = diff(TRAN, TRAN)
        assert isinstance(result, DiffResult)
        assert isinstance(result.traces, tuple)
        assert all(isinstance(t, TraceDiff) for t in result.traces)

    def test_mismatched_is_tuple(self):
        a = _loaded(TRAN)
        b = _mutate_trace(a, "V(out)", 1.0)
        result = diff(a, b, atol=0.0, rtol=1e-6)
        assert isinstance(result.mismatched, tuple)
        assert len(result.mismatched) >= 1

    def test_frozen(self):
        """Dataclasses are frozen — attributes can't be reassigned."""
        result = diff(TRAN, TRAN)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.axis_mismatch = "mutated"  # type: ignore[misc]
