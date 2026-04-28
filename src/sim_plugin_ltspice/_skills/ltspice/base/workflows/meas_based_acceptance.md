# Workflow: `.meas`-based acceptance

Pattern for any LTspice design with a numeric acceptance criterion.
Works today (netlist only); extends to `.asc` once `sim-plugin-ltspice` v0.1
ships.

## 1. State the acceptance in words first

Example: *"The RC low-pass must have its -3 dB corner at 1 kHz ± 5%."*

Derived measurables:
- `fc` — the frequency where `Vdb(out) = -3`. Must be in [950, 1050].
- `gain_dc` — low-frequency gain. Must be in [-0.1, 0]  dB (passive filter, no gain).

## 2. Translate to `.meas` in the netlist

```spice
* RC low-pass — 1 kHz -3 dB corner
V1 in 0 AC 1
R1 in out {R}
C1 out 0 {C}

.param R = 1.6k
.param C = 100n

.ac dec 20 10 100k
.meas AC fc       WHEN Vdb(out)=-3
.meas AC gain_dc  FIND Vdb(out) AT 10
.end
```

One `.meas` per acceptance measurable. Named using snake_case matching
the acceptance statement.

## 3. Run

```bash
sim run design.net --solver ltspice
sim logs last --field measures
# → {"fc": {..., "value": 995.2}, "gain_dc": {..., "value": -0.008}}
```

## 4. Verify in Python

```python
import json, subprocess

out = subprocess.check_output(
    ["sim", "logs", "last", "--field", "measures", "--json"],
    text=True,
)
m = json.loads(out)

assert 950 <= m["fc"]["value"] <= 1050, m["fc"]
assert -0.1 <= m["gain_dc"]["value"] <= 0, m["gain_dc"]
```

## 5. On failure, mutate one knob at a time

If `fc` is wrong, don't change `R` and `C` together. Drop `C` to a
reference value (100n), sweep `R` with `.step param R 1k 2k lin 10`,
`sim run` once, and read the array-valued `fc` back from
`sim logs last --field measures`. Then pick the `R` whose `fc` is
closest to 1 kHz and lock that in.

## 6. If acceptance is ambiguous

Ask the human for a tolerance before writing `.meas`. Don't guess:
"within 5%" differs from "within 2%" and from "close enough to look
right in a plot." The whole point of `.meas` + acceptance is to make
this pass/fail deterministic.
