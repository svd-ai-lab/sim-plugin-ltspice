# What `.log` does and doesn't capture

LTspice has **one** observable file channel for run-time errors: the
per-deck `<deck>.log` written next to the `.net` after a `-b` batch
run. There is no GUI session journal, no event log, no AppData
activity record. This is fundamentally different from solvers like
Flotherm that write a continuous `LogFiles\logFile<timestamp>.xml`
capturing every GUI action and popup.

## What `.log` captures (i.e. what GUI's "SPICE Error Log" window shows)

These are the errors that the solver itself emits during simulation,
all of which appear in the `.log`:

- **Solver convergence**: `Time step too small`, `Singular matrix`,
  `Iteration limit reached`, gmin/source-stepping failures.
- **Netlist syntax**: `Unknown subckt: XYZ`, `Could not find: V(out)`,
  `Bad expression`, duplicate node names.
- **Model loading**: `Could not open: foo.lib`, missing `.MODEL` cards,
  encrypted-model decryption failures.
- **Measurement**: `.MEAS` results (the structured outputs we parse),
  `.MEAS` failures (`measurement failed`, `peakmag did not evaluate`).
- **Initial conditions**: `.IC` / `.NODESET` warnings.
- **Run summary**: total elapsed time, solver method, matrix stats.

`sim_plugin_ltspice.lib.log.parse_log` surfaces these as
`LogResult.measures` / `.errors` / `.warnings`.

## What `.log` does NOT capture

These are GUI-side events that never touch the file system:

- **Schematic-load failures**: `Symbol not found in library` when
  opening an `.asc` interactively. The popup is shown; nothing is
  logged.
- **File-version warnings**: "This schematic was saved by a newer
  version of LTspice…" — modal dialog, no log entry.
- **Editing dialogs**: "Are you sure you want to discard…", "Place
  Component" errors, ParseErrors during symbol editing.
- **Updater popups**: from `updater.exe`, totally separate from
  `LTspice.exe`.
- **`-netlist` failure** (LTspice 26.0.1 regression): when
  `LTspice.exe -netlist <file.asc>` hangs, **no log is written, no
  exit code, no signal**. The process just sits at 0% CPU.

## Practical implication

The `sim_plugin_ltspice.lib.runner` health-check has to use **two strategies**:

| Failure mode | Detection |
|---|---|
| Solver error during a batch run | Parse `.log` → `LogResult.errors` |
| LTspice never produces output (GUI hang, `-netlist` regression, session-0 hang) | Subprocess timeout (default 300 s in `sim_plugin_ltspice.lib.runner`) → exit code 124 |
| Schematic editing / interactive popup | Out of scope for batch driver; use UIA if needed |

`run_net` already handles the first two automatically. There is no
file-channel equivalent of "tail this log to monitor everything LTspice
is doing."

## If you want a popup-equivalent observability channel

The closest thing LTspice has: the GUI's `View → SPICE Error Log` is a
non-modal child window. Its content is written incrementally to
`<deck>.log` as the solver runs, so tailing the file *during* a long
batch run gives you live progress. There's no equivalent for events
*before* the batch starts (schematic-parse stage) or for any GUI-only
dialog.

## Why this matters for agents

When debugging "LTspice didn't produce output," the diagnostic order is:

1. Did `<deck>.log` get written? If no → process never reached the
   simulator (likely `-netlist` hang on 26.0.1, or session-0 hang).
2. If yes, does `LogResult.errors` contain anything? If yes → solver
   error; read the message.
3. If `.log` has content but no errors and no `.raw` exists → the
   netlist was missing an analysis directive (`.tran`, `.ac`, etc.).
   `sim lint` catches this.

`sim_plugin_ltspice.lib` already implements this triage. Don't roll your own.
