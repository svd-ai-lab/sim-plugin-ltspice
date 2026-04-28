# Generic component model catalogue (`lib/cmp/standard.*`)

LTspice ships **eight** files under `<user-data>/lib/cmp/` —
one per primitive device kind. Together they define the closed enum
of generic-model names you can reference via `Value <model-name>` on
the corresponding primitive symbol (`Q1`, `M1`, `D1`, …). Anything
not in these files must come from a `.subckt` (in `lib/sub/` or a
custom `.lib`/`.include`).

## The eight files

| File | Element kind | Models defined | Sample names |
|---|---|---|---|
| `standard.bjt` | BJT (`Q…`) | NPN/PNP transistors | `2N2222`, `2N3904`, `2N3906`, `BC547`, `BC557` |
| `standard.mos` | MOSFET (`M…`) | N/P-channel FETs | `IRF540`, `IRF9540`, `Si4410DY`, `2N7000` |
| `standard.dio` | Diode (`D…`) | Rectifier / Schottky / Zener | `1N4148`, `1N4007`, `1N5817`, `1N4734` |
| `standard.jft` | JFET (`J…`) | N/P-channel JFETs | `2N3819`, `2N5460` |
| `standard.cap` | Capacitor (`C…`) | Non-ideal models with ESR/ESL | E.g. specific caps modelled with parasitics |
| `standard.ind` | Inductor (`L…`) | Non-ideal models | E.g. core-loss + saturation models |
| `standard.res` | Resistor (`R…`) | Non-ideal models | E.g. wirewound parasitic Ls |
| `standard.bead` | Ferrite bead | EMI-suppression beads | Vendor-specific bead models |

## Encoding gotcha — UTF-16 LE

These files are **UTF-16 little-endian** (no BOM in some, BOM in
others depending on history). Read with `encoding='utf-16'` in
Python; reading as UTF-8 yields space-padded ASCII gibberish:

```text
*   C o p y r i g h t   ?   2 0 0 0   ...
. m o d e l   2 N 2 2 2 2   N P N ( I S = 1 E - 1 4   V A F = 1 0 0
```

This is a recurring LTspice theme (the macOS `.log` is also UTF-16 LE
no-BOM). Always sniff before reading.

## Why agents should care

These are the **only** generic-model names LTspice resolves without
needing a `.subckt` import. If you write:

```spice
Q1 c b e 2N2222
```

…and `2N2222` is in `standard.bjt`, the netlist works. If you write
`Q1 c b e 2N9999` and `2N9999` is not there, LTspice errors at
solve time with `unknown subckt or model: 2N9999`.

## Offline lint

`sim_ltspice.cmp.ComponentModelCatalog` (forthcoming, see
[sim-ltspice#TBD](https://github.com/svd-ai-lab/sim-ltspice)) will
parse all eight files at install-discovery time and expose:

```python
from sim_ltspice import ComponentModelCatalog

cat = ComponentModelCatalog()
cat.find("2N2222")           # → ModelDef(name="2N2222", kind="bjt", ...)
cat.find("2N9999")           # → None
cat.models("bjt")            # → list of all bjt model names
cat.kinds()                  # → ("bjt", "mos", "dio", "jft", "cap", "ind", "res", "bead")
```

The point is to flag bad model references **before** running LTspice
— catching typos in CI / `sim lint` rather than mid-simulation.

## How it differs from `lib/sub/`

| | `lib/cmp/standard.*` | `lib/sub/*.lib` / `*.sub` |
|---|---|---|
| Scope | Generic device models (8 files total) | Vendor-specific parts (~4 891 files) |
| Format | UTF-16 LE; one `.MODEL` per model | UTF-8/UTF-16 mix; one `.SUBCKT` per part |
| Referenced via | `Value <model>` on a primitive | `.lib <file>` + `X<inst> nets <subckt>` |
| Lookup | `ComponentModelCatalog.find()` | `parse_subckt(file)` (parser TBD) |

For board-level simulation you'll mostly use `.lib` / `.include` of
specific vendor parts. The `cmp` catalogue is for textbook circuits
and quick discrete-component sketches.
