# SPICE directives — LTspice cheat sheet

All directives start with `.` and live anywhere in the netlist (but
convention is near the bottom, after the element list). Case-insensitive.

## Analysis (you must have at least one)

| Directive | Purpose | Typical form |
|---|---|---|
| `.tran` | Time-domain transient | `.tran 5m` — run for 5 ms with auto timestep |
| `.tran <tstep> <tstop>` | With explicit step | `.tran 1u 5m` — 1 µs step, 5 ms end |
| `.ac dec N fstart fstop` | Small-signal AC sweep | `.ac dec 20 10 100k` — 20 points/decade, 10 Hz to 100 kHz |
| `.dc Vsrc start stop step` | DC sweep | `.dc V1 0 5 0.1` |
| `.op` | Quiescent operating point | `.op` |
| `.noise V(out) Vsrc dec N fstart fstop` | Noise analysis | `.noise V(out) V1 dec 20 10 100k` |
| `.tf V(out) Vsrc` | Small-signal transfer function | `.tf V(out) V1` |
| `.four freq V(out)` | Fourier analysis on transient | `.four 1k V(out)` (must follow `.tran`) |

## Measurements (`.meas` / `.measure`)

Single value:

```
.meas TRAN vout_pk  MAX V(out)                              ; peak
.meas TRAN vout_rms RMS V(out) FROM 1m TO 5m                ; RMS over window
.meas TRAN t_settle WHEN V(out)=4.95 RISE=1                 ; time to reach 4.95 V on rising edge
.meas AC   fc       WHEN Vdb(out)=-3                        ; -3 dB corner
.meas AC   gain     MAX Vdb(out)                            ; peak gain (dB)
.meas DC   ithresh  FIND I(R1) WHEN V(out)=2.5              ; current at specific output level
```

Common operators: `MAX`, `MIN`, `AVG`, `RMS`, `PP` (peak-to-peak),
`INTEG`, `DERIV`, `WHEN`, `FIND`, `FIND...AT`, `PARAM`.

Window modifiers: `FROM <t1> TO <t2>`, `RISE=n`, `FALL=n`, `CROSS=n`.

Results appear in the `.log` and in `sim logs last --field measures`:
```json
{"vout_pk": {"expr": "MAX(V(out))", "value": 4.97, "from": 0, "to": 0.005}}
```

### AC `.meas` quirk: prefer `mag(...)` over `db(...)` / `Vdb(...)`

In AC analysis, `FIND db(V(out)) AT <freq>` can return wildly wrong
values (observed −17 dB vs. actual −0.09 dB on a flat-band probe at
5 kHz of a series-RLC band-pass centered at 5.03 kHz). The same
measurement written with `mag(V(out))` returns the expected magnitude
and `20*log10(mag)` matches the displayed `db()` trace.

Mechanism (empirical, not documented by Analog Devices): `db(...)`
inside a `.meas FIND ... AT` expression interpolates across the decade
grid on the log-scale result of `db()` instead of the linear magnitude,
which amplifies the interpolation error near a peak. `mag(...)` uses
the underlying complex sample and is stable.

**Rule of thumb.** When writing AC `.meas` directives, prefer:

```
.meas AC gain_5k  FIND mag(V(out)) AT 5.03k          ; stable (linear)
```

over:

```
.meas AC gain_5k  FIND db(V(out))  AT 5.03k          ; can be wildly wrong
.meas AC gain_5k  FIND Vdb(out)    AT 5.03k          ; same issue
```

For thresholded corner frequencies (WHEN form), both work, because
LTspice searches for the crossing on the sample grid directly:

```
.meas AC fc_lo WHEN db(V(out)) = -3                  ; OK
```

If you need dB in the result, capture linear magnitude and convert
post-hoc: `value_db = 20 * log10(abs(m.value))`.

## Parameters and sweeps

```
.param R_feedback = 10k               ; named constant
.param R_in = {R_feedback / 10}        ; expression

.step param R_feedback 1k 100k dec 5  ; logarithmic sweep, 5 points/decade
.step param VIN list 1.8 3.3 5.0 12   ; explicit list

.step dec param C 10p 10n 10           ; equivalent syntax
```

Each step writes its own section in the `.raw`; `.meas` runs once per
step; results appear as arrays in the log.

## Initial conditions

```
.ic V(vcap) = 0                        ; force node voltage at t=0
.ic I(L1) = 1m                         ; force inductor current
.nodeset V(x) = 1.2                    ; solver hint for DC op-point
```

## Models and libraries

```
.model MyDiode D(Is=1e-14 Rs=0.1 N=1.05)          ; inline model
.include standard.dio                              ; include a model file
.lib LTC.lib                                       ; library of subcircuits
```

## Save / output control

```
.save V(out) V(in) I(R1)              ; limit what's written to .raw
.save all                              ; default
.print TRAN V(out) V(in)               ; legacy text table — prefer .meas
```

## Options

```
.options abstol=1e-12 reltol=1e-4 vntol=1e-6
.options method=trap                   ; or gear / modifiedtrap
.options nolongnames                   ; PSpice compat
.options plotwinsize=0                 ; compact raw file (uncompressed)
```

## End

```
.end
```

Ending `.end` is optional for LTspice (unlike classic SPICE3), but
include it for portability across simulators.
