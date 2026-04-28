# Element syntax cheat sheet

LTspice element lines follow SPICE3: `<name> <nodes...> <value-or-model>`.
The **first letter of the name determines the element kind** — you
cannot rename a resistor to `C_foo` and have it behave as a capacitor.

| Kind | Prefix | Nodes | Tail form(s) |
|---|---|---|---|
| Resistor | `R` | 2 | `1k` · `1k tc=100u` · `{R_load}` |
| Capacitor | `C` | 2 | `100n` · `100n ic=0 Rser=10m` |
| Inductor | `L` | 2 | `10m` · `10m Rser=5m Rpar=1Meg` |
| Voltage source | `V` | 2 | `5` · `AC 1` · `SINE(0 1 1k)` · `PULSE(0 5 0 1u 1u 1m 2m)` · `PWL(0 0 1m 5)` |
| Current source | `I` | 2 | same source forms as `V` |
| Diode | `D` | 2 | `<model-name>` e.g. `1N4148` |
| BJT | `Q` | 3 or 4 | `<model>` — NPN/PNP |
| MOSFET | `M` | 4 | `<model> L=1u W=10u` |
| JFET | `J` | 3 | `<model>` |
| Switch | `S` (voltage-ctrl) / `W` (current-ctrl) | 4 | `<model>` |
| Subcircuit | `X` | ≥1 | `<subckt-name> <params>` — nodes before name |

**Ground is net `0`.** Not `GND`, not `0v`. Treat it as a reserved
identifier — LTspice references it everywhere.

## Source descriptors (on `V` / `I`)

Static DC: `V1 in 0 5`  → 5 V DC at `in`.

AC analysis: `V1 in 0 AC 1` — magnitude 1, phase 0 for `.ac` sweeps.
Can coexist with a static DC bias: `V1 in 0 2.5 AC 0.1`.

Transient waveforms — the tail names a waveform generator:

| Generator | Signature | Notes |
|---|---|---|
| `SINE(voff vampl f Td Df ϕ Ncycles)` | `SINE(0 1 1k)` — minimum form | Only first three are required |
| `PULSE(v1 v2 Td Tr Tf Pw Per Ncycles)` | `PULSE(0 5 0 1u 1u 1m 2m)` | Square / pulse train |
| `PWL(t1 v1 t2 v2 …)` | `PWL(0 0 1m 5 2m 0)` | Piecewise-linear |
| `EXP(v1 v2 Td1 τ1 Td2 τ2)` | `EXP(0 1 0 10u 1m 100u)` | Exponential rise/fall |
| `SFFM(voff vampl fc MDI fs)` | — | Single-frequency FM |
| `PWL FILE=<path>` | `PWL FILE=stimulus.csv` | Table-driven |

## Common tail options (quick reference)

- Tolerance: `{R_val*(1+0.05*mc(0,0.05))}` — 5 % Gaussian Monte Carlo on
  a param. Only evaluated under `.step` with seed.
- `tc=<temperature-coefficient>` on R / C / L — linear TC vs. `.temp`.
- `Rser=<Ω>` / `Rpar=<Ω>` — parasitic resistances on L / C.
- `ic=<V>` on C / L — initial condition, honored by `.tran uic`.

## Subcircuit usage (`X`)

```spice
* Using a subckt model (.lib / .subckt shipped with LTspice or your own)
.include LTC.lib

XU1 in+ in- vcc vee out LT1001   ; nodes first, subckt name last
V1  vcc 0 15
V2  vee 0 -15
```

Pin order for `X` is **whatever the `.subckt` header declares** — get
it wrong and LTspice either errors ("Unknown parameter") or silently
produces nonsense. When using `sim_plugin_ltspice.lib.symbols.parse_asy`, the
`.asy`'s `SpiceOrder` attribute encodes this — read it first.

## Behavioural sources (`B`)

Analog function of other nodes:

```spice
B1 out 0 V=V(in)*V(in)         ; output = V(in)^2
B2 out 0 I=I(R1)*10            ; current = 10 × I(R1)
```

Useful for idealized op-amps, custom sensors, control laws.

## Syntax rules that trip agents

1. **First line is the title — always ignored.** Your first element
   declaration must start on line 2. `V1 in 0 1` on line 1 is treated
   as a comment.
2. **Continuation with `+`.** A `+` at column 0 means the line
   continues the previous element. Our parser handles this; your hand-
   written netlists usually don't need it.
3. **Case-insensitive identifiers.** `R1`, `r1`, `R_load`, `r_LOAD`
   all refer to the same instance. LTspice preserves the original
   spelling in output.
4. **Numeric suffixes.** `1k` = 1 000, `1meg` = 1 × 10⁶ (note: `meg`,
   not `M` — `M` = milli). `1u` = 1e-6. `1f` = 1e-15.
5. **No quoting.** SPICE has no string-literal syntax; paths and model
   names are written bare. Spaces in a path are a hard error.

## See also

- `spice_directives.md` — for `.tran`, `.ac`, `.meas`, `.param`, etc.
- LTspice Help → "Circuit Element Quick Reference" for the complete
  authoritative list (on Windows: `%LOCALAPPDATA%\Programs\ADI\LTspice\LTspiceHelp\cirquick.html`).
