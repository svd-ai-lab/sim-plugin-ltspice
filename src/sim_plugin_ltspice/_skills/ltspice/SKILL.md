---
name: ltspice-sim
description: Use when working with LTspice circuit simulations: authoring `.net` netlists and `.asc` schematics, running LTspice by the simplest available batch path, and extracting verifiable `.meas`, `.log`, or `.raw` results. Prefer `sim run --solver ltspice` when sim-cli is available because it records structured run history, but direct LTspice batch commands and portable file parsing are first-class paths.
---

# ltspice-sim

This file is the **LTspice** index. Use the simplest reliable execution
path for the workspace you are in. When `sim` is available, prefer
`sim run --solver ltspice` because it gives you structured `.meas`
results, run history, and a consistent error surface. When a task already
provides a direct LTspice launcher (`LTspice.exe -b`, `wine-ltspice`, a
Docker wrapper, or a plain Bash script), that path is equally valid.

Use portable file parsing for completed `.log` and `.raw` artifacts. Do
not route every post-processing step back through sim-cli just because
sim exists.

Read [`../sim-cli/SKILL.md`](../sim-cli/SKILL.md) when you are using the
sim-cli path or a remote `sim serve` host. This skill covers the
LTspice-specific layer: netlist/schematic conventions, platform quirks,
batch execution, and result extraction.

---

## What LTspice is (and isn't)

LTspice is the free SPICE3 simulator from Analog Devices. It has **no
vendor Python API** — unlike Fluent (pyfluent), COMSOL (mph), or MATLAB
(matlabengine). The
[**sim-plugin-ltspice**](https://github.com/svd-ai-lab/sim-plugin-ltspice)
plugin ships its own Python API as `sim_plugin_ltspice.lib`: pure-Python
parsers for `.asc`/`.net`/`.log`/`.raw` plus a subprocess runner around
the LTspice CLI. That bundled lib IS the Python API for LTspice.

Implication: the LTspice-specific advice in this skill stays useful whether
you call `sim run foo.net --solver ltspice`, invoke LTspice directly in batch
mode, or import `sim_plugin_ltspice.lib` in Python. The file format
understanding and platform quirks are the same.

## Execution paths

Pick the path with the least moving parts that still gives verifiable
artifacts:

| Path | Use when | Typical command |
|---|---|---|
| `sim run` | sim-cli is installed, or you need run history / structured JSON / remote dispatch | `sim run design.net --solver ltspice` |
| Direct LTspice batch | LTspice is on the host and the task already has a stable launcher | `LTspice.exe -b design.net` |
| Wine/headless wrapper | Running inside Linux containers with LTspice under Wine | `wine-ltspice design.net` or the task's provided wrapper |
| Python library | You already have `.log` / `.raw`, or need schematic/netlist/raw parsing | `python -c "from sim_plugin_ltspice.lib import RawRead"` |

For benchmark and agent tasks, the important requirement is not which
launcher you used. It is that reported values are grounded in produced
artifacts and can be re-extracted.

## Input classification

| Input | Accepted by sim-cli? | Notes |
|---|---|---|
| `.net` / `.cir` / `.sp` netlist | ✅ today | SPICE3 syntax; first line is title (ignored by solver); must contain at least one analysis directive |
| `.asc` schematic (flat, library-local) | 🟡 sim-plugin-ltspice v0.1+ — on macOS goes through our native asc2net; on Windows/wine goes through LTspice's own `-netlist` | Schematic opens in LTspice GUI for human review |
| `.asc` schematic (hierarchical or custom lib) | 🟡 Windows / wine only | Routed through `sim_plugin_ltspice.lib.schematic_to_netlist` (the in-process Python flattener, since LTspice 26.0.1's `-netlist` flag is broken). On macOS raises `MacOSCannotFlatten` with guidance to route via a Windows host. |
| `.raw` / `.log` inputs | ❌ outputs only | Parse them directly with shell/Python or `sim_plugin_ltspice.lib` |

When you produce a netlist for an agent workflow, prefer `.net`. It is
the most portable input format and has the fewest platform edge cases.

## Platform capabilities

| Capability | macOS 17.x native | macOS 26.x native | Windows 26.x | Linux + wine |
|---|---|---|---|---|
| `-b <netlist>` batch run | ✅ | ✅ | ✅ | ✅ |
| `-Run -b` | ❌ (ignored) | ✅ | ✅ | ✅ |
| `-ascii` raw output | ❌ | ✅ | ✅ | ✅ |
| `-netlist <asc>` schematic→netlist | ❌ | ✅ † | ⚠️ broken on 26.0.1 | ✅ |
| `-ini <path>` reproducible-state run | ✅ | ✅ | ✅ | ✅ |
| `-I<path>` symbol path injection | ✅ | ✅ | ✅ | ✅ |
| `-FastAccess` `.raw` reformat | ❌ | ✅ | ✅ | ✅ |
| `-sync` re-extract bundled libs | ❌ | ✅ | ✅ | ✅ |
| `-version` print version (stderr) | ✅ | ✅ | ✅ | ✅ |
| `.asc` direct input to sim run | native asc2net only (flat + library-local) | native asc2net | full | full |
| `.log` encoding | UTF-16 LE (no BOM) | UTF-8 | UTF-8 | UTF-16 LE |
| `.raw` header encoding | UTF-16 LE | UTF-16 LE | UTF-16 LE | UTF-16 LE |

† On macOS 26 the `-netlist` flag works but `sim-plugin-ltspice`'s preferred
path is the in-process `schematic_to_netlist` flattener — no LTspice
binary touched. Use `-netlist` only when the flattener can't handle a
hierarchy or custom-symbol case.

If you need a feature macOS lacks (or to dodge the 26.0.1 `-netlist`
regression), route through `sim --host <windows-host>`. See
`../sim-cli/SKILL.md` for the HTTP dispatch model. The full
flag-by-flag table lives in
[`base/reference/command_line_switches.md`](base/reference/command_line_switches.md).

## Hard constraints (LTspice-specific)

1. **Every netlist must have an analysis directive.** At least one of
   `.tran`, `.ac`, `.dc`, `.op`, `.noise`, `.tf`, `.four`. Without one,
   LTspice returns exit code 0 but produces no useful output. If using
   sim-cli, `sim lint` can catch this before the run.
2. **Put `.meas` statements in the netlist, not in a config file.**
   That is how stable scalar values appear in the `.log`; sim-cli also
   surfaces them as structured `measures` when you use `sim run`.
   Free-form `.print` output is harder to parse.
3. **Never rely on hidden workspace / process state across batch runs.**
   Each invocation is a cold LTspice batch whether launched directly or
   through sim-cli. Chain steps by writing out intermediate `.net`
   variations in Python, not by stateful execution.
4. **First line of a netlist is the title, always ignored.** Component
   declarations start at line 2. A common mistake is putting `V1 in 0
   1` on line 1 — LTspice silently treats it as comment text.
5. **Use single-letter element prefixes.** `R` / `C` / `L` / `V` / `I`
   / `D` / `Q` / `M` / `X`. Two-letter names like `R1a` are fine as
   *instance labels*, but the first letter must match the element
   kind.
6. **Ground is net `0` (numeric zero).** Not `GND`, not `0v`. Other
   names are arbitrary user-defined nets.

## Required protocol (one paragraph)

Check that LTspice is available by the intended route (`sim check ltspice`
for sim-cli, or the task's direct launcher/version command otherwise).
Validate that the `.net` has a title line, at least one analysis directive,
and `.meas` statements for every scalar acceptance metric. Run the deck by
the simplest available batch path. If using sim-cli, read structured
results with `sim logs last --field measures`; if running directly, parse
the produced `.log` or `.raw` with shell/Python. Evaluate against the task's
acceptance criteria using values re-extracted from those artifacts. For
parameter sweeps, prefer `.step param` inside the netlist so one batch run
covers the sweep; the resulting `.raw` has one dataset per step.

## LTspice-specific layered content

Read the relevant `base/reference/` pages, snippets, and workflows for the
task at hand.

### `base/` — always relevant

| Path | What's there |
|---|---|
| `base/reference/spice_directives.md` | Cheat sheet: `.tran`, `.ac`, `.dc`, `.op`, `.noise`, `.meas`, `.step`, `.param`, `.ic`, `.nodeset`, `.save` |
| `base/reference/element_syntax.md` | R / C / L / V / I / D / Q / M / X instance syntax + common model options |
| `base/reference/result_extraction.md` | Three layers (`.meas` → `RawRead` cursors → arrays) + `eval` / `to_csv` / `to_dataframe`. Read before reaching for `.raw` |
| `base/reference/platform_dispatch.md` | When to route to a Windows host; macOS flat-asc-only constraint |
| `base/reference/command_line_switches.md` | Complete LTspice CLI flag table (16 flags + 2 env vars) — verbatim from the shipped help bundle. Read before constructing any non-default `LTspice.exe` invocation |
| `base/reference/search_path_resolution.md` | `-I<path>` → ini → schematic dir → `lib/sym/` → `lib/sub/`. The order LTspice walks when resolving symbols and `.lib` includes |
| `base/reference/log_channel_limits.md` | What `<deck>.log` does and doesn't capture. No GUI session journal — agents must triage hangs vs. solver errors differently |
| `base/reference/component_models.md` | The 8 generic-model files (`lib/cmp/standard.{bjt,mos,dio,jft,cap,ind,res,bead}`). UTF-16 closed enum used by `Value <model>` references on primitives |
| `base/snippets/rc_lowpass.net` | Minimal RC transient with one `.meas` |
| `base/snippets/rlc_ac.net` | Series-RLC band-pass AC sweep — complex `.raw` traces, resonance `.meas` |
| `base/snippets/inverting_amp.net` | Inverting op-amp with `.include LTC.lib` and gain `.meas` |
| `base/snippets/param_sweep.net` | `.step param R 1k 100k dec 5` + acceptance via `.meas` max/min |
| `base/workflows/meas_based_acceptance.md` | End-to-end: define acceptance → write `.meas` → `sim run` → read JSON → verify |
| `base/workflows/regression_diff.md` | Two-run `.raw` comparison with `sim_plugin_ltspice.lib.diff(a, b)`. Pin a golden `.raw`, gate refactor PRs on waveform equivalence |
| `base/workflows/gui_review_handoff.md` | Python builds `.asc` → spawn LTspice GUI → human reviews / edits → re-read. Waveform viewer handoff. `sim.gui` pywinauto notes for Windows dialogs |
| `base/workflows/param_sweep_postprocess.md` | `.step param` sweep → extract per-step scalars (`.meas`) or slice full traces (`RawRead.to_dataframe()` + axis-seam split) for plotting / custom math |
| `base/workflows/monte_carlo.md` *(planned — not yet written)* | Monte-Carlo via `.step` + `mc()` + Python loop with `sim run` per seed |

### Documentation lookup

LTspice ships an extensive offline help set on Windows at
`%LOCALAPPDATA%\Programs\ADI\LTspice\LTspiceHelp\` (~738 HTML files
in 26.x — comprehensive SPICE + analysis reference). macOS 17 ships
no HTML help (in-GUI help only); macOS 26 has parity with Windows.

The community mirror at [ltwiki.org](https://ltwiki.org/) indexes the
same content and is searchable from any platform without LTspice
installed locally.

For authoritative syntax questions on a Windows host:

```bash
sim --host <windows-host> exec 'cat "%LOCALAPPDATA%\Programs\ADI\LTspice\LTspiceHelp\<topic>.htm"'
```

For anyone else, consult the LTspice Users' Guide PDF (search
"LTspice Getting Started Guide" — Analog Devices publishes it openly).

### `tests/` (top-level, QA-only)

Not loaded during a normal session. Mirrors the sibling skills'
convention.

---

## Common pitfalls (save yourself a cycle)

1. **Missing ground reference.** Every net that isn't declared somewhere
   must be connected to something — LTspice flags singular matrices
   cryptically. Always add ground (`FLAG 0` via the netlist is
   implicit when you reference net `0`).

2. **`.meas` misspelled as `.measure`.** Both work, but `.meas` is the
   shorter form used in every example and our parser is tuned for it.

3. **Windows `.log` encoding trap.** LTspice 26 writes UTF-8 logs;
   LTspice 17 (macOS) writes UTF-16 LE. `sim-plugin-ltspice` handles both
   transparently, but if you're reading the `.log` yourself with
   `open()`, sniff the encoding.

4. **Drive-letter paths in logs.** On Windows, the `.log` has a
   `Files loaded:\nC:\Users\...\design.net` block. A naive regex
   parser would see `C:` as a measure name. If you roll your own
   log parser, exclude newlines from the expression capture. (Ours
   does — see the sim-cli driver's regex.)

5. **macOS `.asc` refusal.** If `sim run my.asc --solver ltspice`
   errors with `MacOSCannotFlatten`, either (a) ensure the schematic
   uses only shipped-library symbols and no hierarchy, or (b) route
   via `sim --host <windows-host>`.

6. **`-netlist` is broken on LTspice 26.0.1 (Windows).** The flag
   silently hangs — no `.net` written, no exit code, no signal.
   Don't shell out to `LTspice.exe -netlist`; use
   `sim_plugin_ltspice.lib.schematic_to_netlist` instead. See
   [`base/reference/command_line_switches.md`](base/reference/command_line_switches.md)
   for the full regression note.

7. **No GUI session log.** Unlike Flotherm, LTspice writes nothing
   for GUI events (popups, schematic-load failures, updater
   dialogs). The only file channel is the per-deck `<deck>.log`,
   which only covers solver-time errors. For hangs and GUI-only
   failures, the `sim_plugin_ltspice.lib.runner` 300 s timeout is the
   triage primitive — see
   [`base/reference/log_channel_limits.md`](base/reference/log_channel_limits.md).

8. **Generic-model lookup is closed.** `Q1 c b e 2N9999` will fail
   at solve time unless `2N9999` is in `lib/cmp/standard.bjt` or
   pulled in via `.lib`/`.include`. The 8 `lib/cmp/standard.*`
   files are the closed enum — see
   [`base/reference/component_models.md`](base/reference/component_models.md)
   for offline lint via `ComponentModelCatalog`.
