# Result extraction

Three layers of getting numbers out of an LTspice run. Pick the
**shallowest** layer that answers your question — going deeper is
slower, and `.meas` is the only layer that stays stable across LTspice
versions.

| Layer | What it returns | When to use | sim-cli path |
|---|---|---|---|
| `.meas` (in netlist) | Named scalar(s) per analysis | Acceptance criteria, spec checks | `sim logs last --field measures` |
| `RawRead` cursors | Scalar queries on traces (`max`, `min`, `mean`, `rms`, `sample_at`) | Ad-hoc values you didn't pre-declare | `sim_ltspice.RawRead(raw_path).max("V(out)")` |
| `RawRead` arrays | Full NumPy arrays per trace | Plotting, custom math, exporting | `RawRead(raw_path).trace("V(out)")` |

---

## Layer 1 — `.meas` (always try this first)

`.meas` statements live in the netlist and LTspice evaluates them at
the end of the run. Results flow into the `.log` and sim-cli's driver
surfaces them as structured JSON:

```bash
sim run rlc_ac.net --solver ltspice
sim logs last --field measures --json
# → {"fr":      {"expr": "WHEN Vdb(out)=MAX(Vdb(out))", "value": 5023.4, "from": 0, "to": 0},
#    "peakdb":  {"expr": "MAX Vdb(out)",                "value":   19.2, "from": 0, "to": 0},
#    "q_est":   {"expr": "FIND Vdb(out) AT 5.03k",      "value":   19.1, "from": 0, "to": 0}}
```

Under `.step`, each measure's `value` becomes a **list** indexed by
step number. Don't assume scalar.

### When to prefer `.meas`

- The acceptance criterion is known upfront.
- You want the result in the `.log` for a human to eyeball too.
- You need the result usable from *any* sim-cli call — no Python needed.

### When `.meas` isn't enough

- Exploratory: you don't yet know *what* to measure.
- Cross-trace math (`V(a) / V(b)` at a specific frequency) —
  expressible in `.meas` but much more readable in Python.
- You need the whole waveform, not a scalar — fall through to Layer 2 / 3.

---

## Layer 2 — `RawRead` cursor queries

When the number isn't `.meas`-able (yet) and you want a scalar, open
the `.raw` and ask it. Every method takes a trace name and returns a
Python scalar — complex on AC analyses, real elsewhere.

```python
from sim_ltspice import RawRead

rr = RawRead("sim.raw")

rr.max("V(out)")            # 3.142  (scalar)  — magnitude for complex
rr.min("I(R1)")             # -0.00198
rr.mean("V(out)")            # 1.57   — arithmetic mean
rr.rms("V(out)")             # 2.22   — sqrt(mean(|x|²))
rr.sample_at("V(out)", 2e-3) # linear-interpolated value at t=2 ms
```

### `.eval(expr)` — arithmetic over traces

For "the number at the same x across two traces" questions:

```python
rr.eval("V(out) / V(in)")            # complex array on AC; real on TRAN
rr.eval("V(out) - V(in)")
rr.eval("Vdb(out) - Vdb(in)")        # not allowed — Vdb is a function call
```

Accepted: `V(node)`, `I(device)`, numeric literals, `+ - * / ** %` and
unary `-`. Disallowed: function calls, attribute access, subscripting,
comparisons — all raise `InvalidExpression`.

For log-domain math, take the expression result and do `20*log10(abs(...))`
in NumPy:

```python
import numpy as np
H = rr.eval("V(out) / V(in)")        # complex transfer function
Hdb = 20 * np.log10(np.abs(H))       # magnitude in dB
```

### Stepped sweeps

`rr.sample_at()` raises on stepped sweeps because the axis is
non-monotonic across concatenated steps. For stepped runs, use `.meas`
(Layer 1) or walk the `.raw` in arrays (Layer 3) and slice by step.

---

## Layer 3 — Whole-trace arrays

When you need the waveform itself (plotting, FFT, custom filter,
external tool handoff):

```python
rr = RawRead("sim.raw")

t    = rr.axis                       # np.ndarray — time on .tran, freq on .ac
vout = rr.trace("V(out)")            # np.ndarray — same length as axis
```

### Export paths

```python
rr.to_csv("sim.csv")                 # one column per trace; axis first
                                     # complex → "<name>.re" + "<name>.im" pairs

df = rr.to_dataframe()               # requires sim-ltspice[dataframe]
                                     # axis is index, one column per non-axis trace
df["V(out)"].plot()                  # standard pandas from here
```

Handy for handing a simulation off to a human reviewer — CSV opens in
Excel, DataFrame plots in Jupyter.

### Trace names

```python
rr.trace_names()                     # ['time', 'V(in)', 'V(out)', 'I(R1)']
```

Prefixes: `V(...)` for node voltages, `I(...)` for device currents,
`time`/`frequency` for the axis (automatically handled by `rr.axis`).

---

## Decision cheat sheet

```
Do you already know what number you want?
├── Yes → Layer 1 (.meas)
└── No  → Open .raw
         ├── Scalar answer (peak, RMS, value at x)? → Layer 2 (cursors)
         ├── Cross-trace arithmetic at matched x?   → Layer 2 (rr.eval)
         └── Whole waveform (plot, FFT, handoff)?   → Layer 3 (arrays / to_csv / to_dataframe)
```

## Comparing two runs

For regression / A-B comparison, don't roll your own diff — use
`sim_ltspice.diff`. See `base/workflows/regression_diff.md`.
