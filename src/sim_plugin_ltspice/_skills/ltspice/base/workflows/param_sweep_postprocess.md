# Workflow: parameter sweep → post-processing

`.step param` gives you *n* sweep points in one batch run. Three
post-processing options, from cheapest to richest:

1. `.meas` aggregator — scalar per step, surfaced as a list via
   `sim logs last --field measures`.
2. `RawRead.to_dataframe()` — full traces for every step, as a
   `pandas.DataFrame` indexed by the sweep axis.
3. `RawRead` arrays + NumPy — custom per-step math.

Reach for #1 whenever the answer is "one number per step"; #2/#3 when
you want the whole waveform or custom aggregations the `.meas` syntax
can't express.

## The sweep netlist

Example — sweep a load resistor across five decade points:

```spice
* Parameter sweep — vary R_load, measure peak V(out) per step
.param R_load = 1k

V1  in 0 PULSE(0 5 0 1u 1u 1m 2m)
R_source in out 100
R_load   out 0  {R_load}
C_load   out 0  10n

.tran 5m
.step param R_load list 100 330 1k 3.3k 10k
.meas TRAN vout_peak MAX V(out)
.end
```

See `base/snippets/param_sweep.net` for a copy-paste version.

## Pattern 1 — `.meas` per step (scalar summary)

```bash
sim run param_sweep.net --solver ltspice
sim logs last --field measures --json
# → {"vout_peak": {"expr": "MAX V(out)",
#                  "value": [4.91, 4.94, 4.96, 4.98, 4.99],  # list — one per step
#                  "from": 0, "to": 0.005,
#                  "step_values": [100, 330, 1000, 3300, 10000]}}
```

Acceptance in Python:

```python
import json, subprocess
m = json.loads(subprocess.check_output(
    ["sim", "logs", "last", "--field", "measures", "--json"], text=True
))
peaks = m["vout_peak"]["value"]
assert all(p >= 4.9 for p in peaks), f"vout_peak dipped: {peaks}"
```

When this is enough: acceptance is a scalar bound per step. Maximum,
minimum, first-crossing, delay, period — all expressible in `.meas`.

## Pattern 2 — DataFrame across steps

When you need the full waveform per step (plotting, FFT, custom
integration), open the `.raw`:

```python
from sim_plugin_ltspice.lib import RawRead

rr = RawRead("param_sweep.raw")
df = rr.to_dataframe()       # requires sim-plugin-ltspice[dataframe]
```

`rr.to_dataframe()` returns every concatenated step in one frame, with
the axis as index and one column per trace. **For stepped runs, split
by the axis-restart boundary** — LTspice concatenates step i+1 right
after step i, and the axis resets, so `diff(axis) < 0` marks each seam:

```python
import numpy as np

# Boundaries = indices where the next step starts.
seams = np.where(np.diff(rr.axis) < 0)[0] + 1
bounds = [0, *seams.tolist(), len(rr.axis)]

per_step = [df.iloc[a:b] for a, b in zip(bounds[:-1], bounds[1:])]

# Step 2 → load = 1 k (third entry in .step list).
rload_1k = per_step[2]
print(rload_1k["V(out)"].max())
```

`rr.is_stepped` tells you whether a split is even needed. For AC
sweeps the axis is log-monotonic (not reset), so the seam heuristic
above doesn't apply — for `.step` + `.ac`, LTspice stores step index
as a separate axis; inspect `rr.axis` to see which shape you got.

## Pattern 3 — NumPy per-step math

When the post-processing is neither a scalar `.meas` nor "plot it":

```python
import numpy as np
from sim_plugin_ltspice.lib import RawRead

rr = RawRead("param_sweep.raw")
t = rr.axis
v = rr.trace("V(out)")
i = rr.trace("I(R_load)")

seams = np.where(np.diff(t) < 0)[0] + 1
bounds = [0, *seams.tolist(), len(t)]

energy_per_step = []
for start, end in zip(bounds[:-1], bounds[1:]):
    p = v[start:end] * i[start:end]
    dt = np.diff(t[start:end])
    energy_per_step.append(float(np.sum(p[:-1] * dt)))

print(energy_per_step)
```

If that integral is a recurring check, wrap it in a helper — don't ship
raw loops in the agent.

## CSV export for handoff

When the consumer is a human or a non-Python tool:

```python
rr.to_csv("param_sweep.csv")
# → axis column first, then one column per trace; complex traces
#   expand into "<name>.re" / "<name>.im" pairs.
```

Drops straight into Excel, `pandas.read_csv`, or `awk`. Faithful to the
raw numbers — no interpolation, no resampling.

## Choosing the sweep form

LTspice `.step` has three forms:

```spice
.step param R 1k 10k lin 5       ; 5 points linearly 1k..10k  (= 1k, 3.25k, ...)
.step param R 1k 10k dec 10      ; 10 points per decade (log)
.step param R list 100 330 1k 3.3k 10k   ; explicit list
```

Use `list` when the acceptance asks for specific points (e.g. "our BOM
has R = {100, 330, 1k, 3.3k, 10k}"). Use `dec` for frequency-like
decade sweeps. Use `lin` only when the design is actually linear in the
parameter — otherwise the endpoints dominate and mid-range info is
wasted.

## Anti-patterns

- **Don't spawn one `sim run` per step from Python.** `.step` is what
  LTspice is built for — one run, one `.raw`, N-point analysis. Python
  loops only when the sweep needs control flow LTspice can't express
  (e.g. early-stop on acceptance fail).
- **Don't rely on trace ordering by step-index alone.** The `.raw`
  concatenates steps contiguously; split by axis-monotonicity seams
  (see above) to recover per-step slices.
- **Don't `.to_dataframe()` on multi-GB `.raw`.** Pandas copies into
  memory; on a laptop, `lin 1000` with dense traces will OOM. Stick to
  NumPy arrays or decimate.
