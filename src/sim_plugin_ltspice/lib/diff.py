"""Compare two `.raw` files trace-by-trace.

`sim_ltspice.diff` is the regression-testing counterpart to
`sim_ltspice.raw.RawRead`. Given two `.raw` files produced by different
runs of the same circuit, `diff(a, b)` returns a structured `DiffResult`
listing per-trace tolerance checks, axis compatibility, and any traces
that are present in only one run.

The tolerance model mirrors ``numpy.allclose``::

    |a - b| <= atol + rtol * |b|

For complex traces the comparison operates on the complex difference
(``|a-b|`` is the complex magnitude). ``max_rel`` is computed against
``max(|a|, |b|, tiny)`` so a trace that is identically zero in one run
still yields a finite relative error rather than +inf.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from .raw import RawRead


@dataclass(frozen=True)
class TraceDiff:
    """Per-trace comparison summary."""

    name: str
    max_abs: float   # max |a - b| across the trace
    max_rel: float   # max |a - b| / max(|a|, |b|, _TINY)
    within_tol: bool


@dataclass(frozen=True)
class DiffResult:
    """Outcome of a `diff(a, b)` call."""

    traces: tuple[TraceDiff, ...]
    only_in_a: tuple[str, ...]
    only_in_b: tuple[str, ...]
    axis_mismatch: str | None  # None ⇒ axes align within tolerance

    @property
    def ok(self) -> bool:
        """True iff every compared trace is within tolerance and no trace
        is missing on either side and the axes match."""
        return (
            self.axis_mismatch is None
            and not self.only_in_a
            and not self.only_in_b
            and all(t.within_tol for t in self.traces)
        )

    @property
    def mismatched(self) -> tuple[TraceDiff, ...]:
        """Subset of ``traces`` that failed the tolerance check."""
        return tuple(t for t in self.traces if not t.within_tol)


_TINY = np.finfo(np.float64).tiny


def _as_rawread(x: RawRead | str | Path) -> RawRead:
    return x if isinstance(x, RawRead) else RawRead(x)


def _compare_axis(
    a: np.ndarray,
    b: np.ndarray,
    atol: float,
    rtol: float,
) -> str | None:
    if a.shape != b.shape:
        return f"axis lengths differ: {a.shape[0]} vs {b.shape[0]}"
    # Axes should be real in practice (time / freq / index). Coerce
    # defensively for the ``complex`` layout where the axis is stored
    # as a complex number with zero imaginary part.
    a_r = np.asarray(a).real
    b_r = np.asarray(b).real
    delta = np.abs(a_r - b_r)
    tol = atol + rtol * np.abs(b_r)
    if np.any(delta > tol):
        bad = float(delta.max())
        return f"axis values diverge (max |Δ|={bad:.3e})"
    return None


def _compare_trace(
    name: str,
    a: np.ndarray,
    b: np.ndarray,
    atol: float,
    rtol: float,
) -> TraceDiff:
    if a.shape != b.shape:
        return TraceDiff(name=name, max_abs=float("inf"), max_rel=float("inf"), within_tol=False)
    if a.size == 0:
        return TraceDiff(name=name, max_abs=0.0, max_rel=0.0, within_tol=True)
    delta = np.abs(a - b)
    max_abs = float(delta.max())
    denom = np.maximum(np.maximum(np.abs(a), np.abs(b)), _TINY)
    max_rel = float((delta / denom).max())
    tol = atol + rtol * np.abs(b)
    within = bool(np.all(delta <= tol))
    return TraceDiff(name=name, max_abs=max_abs, max_rel=max_rel, within_tol=within)


def diff(
    a: RawRead | str | Path,
    b: RawRead | str | Path,
    *,
    traces: Iterable[str] | None = None,
    atol: float = 0.0,
    rtol: float = 1e-6,
) -> DiffResult:
    """Compare two `.raw` files trace-by-trace.

    Parameters
    ----------
    a, b
        Either paths (`str` / `Path`) or pre-loaded `RawRead` objects.
    traces
        If given, restrict the comparison to these trace names. Missing
        names are reported in ``only_in_a`` / ``only_in_b`` rather than
        raising. If omitted, all traces common to both runs are compared
        (the axis is still compared separately and reported in
        ``axis_mismatch``).
    atol, rtol
        ``numpy.allclose``-style tolerances: a sample passes when
        ``|a-b| <= atol + rtol * |b|``.

    Returns
    -------
    DiffResult
        ``.ok`` is True when every requested trace is within tolerance
        and the axis aligns and no trace is missing.

    Notes
    -----
    Stepped sweeps are compared as flat arrays — the caller is
    responsible for ensuring step alignment between the two runs.
    """
    ra = _as_rawread(a)
    rb = _as_rawread(b)

    # Axis comparison (skipped implicitly when lengths differ because
    # traces won't compare either).
    axis_msg = _compare_axis(ra.axis, rb.axis, atol, rtol)

    # Non-axis trace names on each side.
    a_names = [v.name for v in ra.variables if v.index != 0]
    b_names = [v.name for v in rb.variables if v.index != 0]
    a_set = set(a_names)
    b_set = set(b_names)

    if traces is None:
        # All common names, in ra's order.
        requested = [n for n in a_names if n in b_set]
        only_a = tuple(n for n in a_names if n not in b_set)
        only_b = tuple(n for n in b_names if n not in a_set)
    else:
        requested_list = list(traces)
        requested: list[str] = []
        only_a_list: list[str] = []
        only_b_list: list[str] = []
        for name in requested_list:
            in_a = name in a_set
            in_b = name in b_set
            if in_a and in_b:
                requested.append(name)
            elif in_a and not in_b:
                # Present in a, missing from b ⇒ this name is only_in_a.
                only_a_list.append(name)
            elif in_b and not in_a:
                only_b_list.append(name)
            else:
                # Missing from both — report in both only_* lists so
                # neither side's summary swallows it.
                only_a_list.append(name)
                only_b_list.append(name)
        only_a = tuple(only_a_list)
        only_b = tuple(only_b_list)

    trace_diffs: list[TraceDiff] = []
    for name in requested:
        if ra.axis.shape != rb.axis.shape:
            # Lengths differ — emit an infinite-delta placeholder so
            # callers see which traces were skipped and why without a
            # silent drop.
            trace_diffs.append(
                TraceDiff(
                    name=name,
                    max_abs=float("inf"),
                    max_rel=float("inf"),
                    within_tol=False,
                )
            )
            continue
        trace_diffs.append(
            _compare_trace(name, ra.trace(name), rb.trace(name), atol, rtol)
        )

    return DiffResult(
        traces=tuple(trace_diffs),
        only_in_a=only_a,
        only_in_b=only_b,
        axis_mismatch=axis_msg,
    )
