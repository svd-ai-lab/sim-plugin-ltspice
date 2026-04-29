"""Post-run state summary for LTspice runs.

Auto-feedback only. **No interpretation, no domain heuristics.**

What this returns:
- ``trace_stats``: min/max/mean/rms per voltage and current trace,
  computed over the steady-state window (last 40% of the time axis).
- ``steady_state_window``: the window used for the stats above.

What this DOES NOT return (by design):
- "Sanity flags" / "likely-reversed" / mode classification — those are
  domain inferences that belong in SKILL.md (where agent looks them
  up) or in the agent itself (where it does the diff against its
  expectations). Encoding them here violates separation of concerns:
  case-specific heuristics from the tool layer mislead on novel
  topologies and create false positives.
- Component-level derived stats (duty cycle, power, mode). Those are
  case-specific probes (some matter for switching circuits, none for
  pure RC, etc.). Probe-based queries belong in
  ``sim_plugin_ltspice.lib.raw.RawRead`` / sim CLI's ``logs --field``
  interface, not in auto output.

Solver-internal warnings (Newton convergence, divide-by-zero) are
already surfaced separately in the driver's ``errors``/``warnings``
fields — not duplicated here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _summarize_window(arr) -> dict[str, float]:
    """Return min/max/mean/rms over a 1-D numpy slice."""
    import numpy as np
    if arr.size == 0:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "rms": 0.0}
    return {
        "min":  float(np.min(arr)),
        "max":  float(np.max(arr)),
        "mean": float(np.mean(arr)),
        "rms":  float(np.sqrt(np.mean(np.square(arr.astype("float64"))))),
    }


def diagnose(raw_path: Path | str | None, log_path: Path | str | None = None) -> dict[str, Any]:
    """Compute per-trace stats over the steady-state window.

    Returns a dict with two keys:
    - ``trace_stats``: {trace_name: {min, max, mean, rms}}
    - ``steady_state_window``: {from, to}

    Failure modes are caught and returned as an ``error`` field rather
    than raised — diagnostics must never break the run record.
    """
    if raw_path is None:
        return {"error": "no raw file produced"}
    raw_path = Path(raw_path)
    if not raw_path.is_file():
        return {"error": f"raw file not found: {raw_path}"}

    try:
        import numpy as np
        from sim_plugin_ltspice.lib.raw import RawRead
    except Exception as exc:
        return {"error": f"diagnose unavailable: {exc}"}

    try:
        rr = RawRead(raw_path)
    except Exception as exc:
        return {"error": f"failed to read raw: {exc}"}

    # Steady-state window: skip the first 60% of the run. For transients
    # this lets the circuit settle; for AC/DC it's effectively a no-op
    # since the axis is frequency or sweep index. For very short runs
    # we use everything.
    axis = rr.axis
    if axis is None or len(axis) < 4:
        return {"error": "raw axis empty or too short"}
    span = float(axis[-1] - axis[0])
    if span > 0:
        ss_start_value = float(axis[0]) + 0.6 * span
        ss_idx = int(np.searchsorted(axis, ss_start_value))
        ss_idx = max(0, min(ss_idx, len(axis) - 2))
    else:
        ss_idx = 0

    trace_stats: dict[str, dict[str, float]] = {}
    for var in rr.variables:
        name = var.name if hasattr(var, "name") else str(var)
        try:
            data = rr.trace(name)
        except Exception:
            continue
        if data is None or len(data) <= ss_idx + 1:
            continue
        window = np.asarray(data[ss_idx:])
        trace_stats[name] = _summarize_window(window)

    return {
        "trace_stats": trace_stats,
        "steady_state_window": {
            "from": float(axis[ss_idx]),
            "to":   float(axis[-1]),
        },
    }
