# Workflow: two-run regression diff

A headline v0.2 use case: when you change a netlist (swap a model,
retune an R/C, refactor a subcircuit), you want to know **exactly what
the waveforms did** — not just whether `.meas` still passes.

`sim_plugin_ltspice.lib.diff(a, b)` gives you a per-trace delta report with
explicit tolerances. Reach for it whenever you'd otherwise be eyeballing
two `.raw` files side-by-side.

## When to use this instead of `.meas`

| Question | Right tool |
|---|---|
| Did the -3 dB corner stay within 5%? | `.meas` in netlist + `sim logs last` |
| Did *any* trace change more than 1e-4 after the refactor? | `diff(a, b)` |
| Is the new op-amp model waveform-equivalent to the old one? | `diff(a, b)` |
| Does CI pass the acceptance criterion? | `.meas` |
| Does CI reject unintended waveform drift in a refactor PR? | `diff(a, b)` |

Rule of thumb: `.meas` is for **specs**, `diff` is for **regressions**.

## Basic shape

```python
from sim_plugin_ltspice.lib import run_net, diff

r_old = run_net("design_v1.net")
r_new = run_net("design_v2.net")
assert r_old.ok and r_new.ok, "runs failed — fix that first"

result = diff(r_old.raw_path, r_new.raw_path, atol=1e-6, rtol=1e-4)

if not result.ok:
    for t in result.mismatched:
        print(f"  {t.name}: max_abs={t.max_abs:.3e}  max_rel={t.max_rel:.3e}")
    raise AssertionError("unintended waveform change")
```

`DiffResult` is a frozen dataclass. Key fields:

| Field | Type | Meaning |
|---|---|---|
| `ok` | `bool` | True iff every included trace is within tolerance AND no set-difference / axis-mismatch |
| `traces` | `tuple[TraceDiff, ...]` | Per-trace report |
| `mismatched` | `tuple[TraceDiff, ...]` | Shortcut for `[t for t in traces if not t.within_tol]` |
| `only_in_a` | `tuple[str, ...]` | Trace names present in `a` but missing in `b` |
| `only_in_b` | `tuple[str, ...]` | Symmetric |
| `axis_mismatch` | `str \| None` | Non-None if axes differ (length or values diverge) |

Each `TraceDiff` carries `name`, `max_abs`, `max_rel`, `within_tol`.

## Tolerance model

The gate is the classic NumPy form:

```
|a - b| <= atol + rtol · |b|
```

Defaults: `atol=0.0`, `rtol=1e-6`. **Pick both deliberately.**

- `atol` — the "noise floor" where absolute differences are irrelevant.
  For sub-μV precision on a 5 V rail, `atol=1e-9` is over-strict;
  `atol=1e-6` is usually plenty.
- `rtol` — the relative ceiling. `1e-4` = "stay within 0.01% of the
  reference value."

Complex traces (AC analyses) compare by **magnitude of the difference**:
`|a - b|`, not `|a| - |b|`. A pure imaginary shift counts.

## Scoping the comparison: `traces=...`

By default every non-axis trace is compared. Restrict to the traces
you actually care about — much faster on dense `.step` runs and makes
the failure output readable:

```python
result = diff(a, b, traces=["V(out)", "I(Rload)"], atol=1e-6)
```

If a name in `traces=` exists on neither side, it's reported on both
`only_in_a` and `only_in_b` — that way a typo fails loud instead of
silently passing.

## Set differences: trace added or removed

If one side has a trace the other lacks (e.g. you renamed a net),
`only_in_a` / `only_in_b` carry the names and `ok` is False. You can
still inspect the overlapping traces via `result.traces`.

## Axis mismatch

If the two `.raw` axes differ (different `.tran` stop times, different
`.ac` sweep ranges, or the axes diverge mid-run):

```python
if result.axis_mismatch:
    print(result.axis_mismatch)  # e.g. "axis lengths differ: 1001 vs 501"
```

Per-trace diffs in this case are `inf` placeholders — the result is
still a structured object, just not comparable trace-by-trace.

## CI pattern: pinning a golden `.raw`

Cheapest regression guard for a design repo:

```python
# tests/test_design_regression.py
from pathlib import Path
from sim_plugin_ltspice.lib import RawRead, run_net, diff

GOLDEN = Path(__file__).parent / "fixtures" / "rc_lowpass.golden.raw"

def test_rc_lowpass_unchanged():
    r = run_net(Path(__file__).parent / "fixtures" / "rc_lowpass.net")
    assert r.ok
    result = diff(GOLDEN, r.raw_path, atol=1e-9, rtol=1e-6)
    assert result.ok, f"regression: {result.mismatched}"
```

On first run, commit the produced `.raw` as the golden file. On intended
changes (new component values, model swap you *meant* to do), update
the golden in the same commit as the netlist change — the diff is part
of the PR, not hidden.

Caveats:
- LTspice `.raw` binary layout is deterministic for a given LTspice
  version + platform. Across LTspice 17 ↔ 26 you may see float32 ↔
  float64 drift in transient traces. If CI runs on a different LTspice
  build than local, use a looser `rtol` (`1e-4`) or keep CI pinned to
  one LTspice install.
- Very large `.raw` (> 100 MB) can inflate the git repo. For those,
  commit a **decimated** golden — run the reference with a coarser
  `.tran step` — rather than the full resolution.

## Handing off a failing diff

When `result.ok` is False and the human asks "what broke?":

```python
for t in sorted(result.mismatched, key=lambda x: -x.max_abs)[:5]:
    print(f"{t.name:30s}  max_abs={t.max_abs:.3e}  max_rel={t.max_rel:.3e}")
```

Top-5 by absolute delta usually enough to localize. If the bad trace is
ambiguous (e.g. both `V(mid)` and `V(out)` drifted), overlay the raw
waveforms in the LTspice GUI — see `gui_review_handoff.md`.
