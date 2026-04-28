# Workflow: GUI review & human-in-the-loop handoff

LTspice is a one-shot batch solver, so unlike Fluent/COMSOL there's no
live simulation session to drive. But the GUI still matters — for three
real engineer workflows:

1. **Review handoff** — Python builds an `.asc`; human opens it in LTspice
   to eyeball the schematic before signing off.
2. **Waveform view** — after a batch run, spawn the LTspice waveform
   viewer on the `.raw` so the human can scrub / overlay traces.
3. **Round-trip edit** — Python proposes an initial design, human tweaks
   in the GUI and saves, Python reads the modified `.asc` back.

All three share one primitive: **spawn LTspice with a file argument,
wait for the window to close, continue.** That's it.

## Platform notes

| Capability | macOS 17.x | Windows 26.x |
|---|---|---|
| Spawn GUI with `.asc` | `open -a LTspice <file>` or direct `subprocess.Popen([exe, file])` | `subprocess.Popen([exe, file])` |
| Spawn GUI with `.raw` (waveform viewer) | same | same |
| `sim.gui.GuiController` / pywinauto introspection | ❌ (`.app`, not pywinauto-driveable) | ✅ |
| Dismiss dialogs / UIA drilling | not available | `sim.gui` — see the very end of this doc |

If your workflow only needs "open the file + wait for close", the cross-
platform `subprocess.Popen` + `proc.wait()` path works everywhere and
that's what this doc uses. Reach for `sim.gui` only when the design
actually needs window introspection.

> **Note (2026-04-24):** The LTspice driver in sim-cli does not yet
> inject a `gui` object into the session namespace (unlike Fluent /
> COMSOL / Flotherm). Until it does, drive the GUI with `sim_ltspice`'s
> install discovery + standard `subprocess`, as shown below.

## Scenario 1 — review an authored `.asc`

You've programmatically built a schematic. Before passing it downstream,
you want a human to look at it.

```python
import subprocess
from sim_ltspice import (
    find_ltspice, Netlist, Element, Directive, netlist_to_schematic, write_asc,
)

# 1. Build the design. `Element` takes (name, nodes, tail);
#    `Directive` splits into (command, args).
net = Netlist(
    title="RC low-pass — review candidate",
    elements=[
        Element("V1", ["in", "0"],  "AC 1 SINE(0 1 1k)"),
        Element("R1", ["in", "out"], "1.6k"),
        Element("C1", ["out", "0"],  "100n"),
    ],
    directives=[Directive(".ac", "dec 20 10 100k")],
)
write_asc(netlist_to_schematic(net), "review.asc")

# 2. Hand off to a human. find_ltspice() returns a *list* — empty if
#    none installed. Use the first hit (normally the only one).
installs = find_ltspice()
if not installs:
    raise RuntimeError("LTspice not installed on this host")
inst = installs[0]

proc = subprocess.Popen([str(inst.exe), "review.asc"])
print("LTspice is open — close the window when you're done reviewing.")
proc.wait()             # blocks until the human closes LTspice

# 3. Re-read — captures any in-GUI save the human made.
from sim_ltspice import read_asc
schem = read_asc("review.asc")
```

On macOS you can also use `subprocess.Popen(["open", "-a", "LTspice",
"review.asc"])` — the `open` wrapper launches LTspice detached, so
`proc.wait()` returns immediately. If you need the wait-until-closed
behavior on macOS, invoke the binary directly (`inst.exe`) so the
subprocess tracks LTspice.app's lifetime.

## Scenario 2 — show waveforms after a run

```python
import subprocess
from sim_ltspice import find_ltspice, run_net

result = run_net("design.net")
assert result.ok, result.stderr

inst = find_ltspice()[0]
subprocess.Popen([str(inst.exe), str(result.raw_path)])
# Don't .wait() — let the waveform viewer stay open while the script
# continues with the next design.
```

LTspice opens `.raw` files directly into the waveform viewer; no GUI
automation needed.

## Scenario 3 — round-trip edit (agent proposes, human finalizes)

```python
import subprocess
from pathlib import Path
from sim_ltspice import (
    find_ltspice, read_asc, write_asc, schematic_to_netlist,
    write_net, run_net, diff,
)

asc = Path("candidate.asc")
write_asc(initial_schematic(), asc)   # agent-supplied builder

# Compute a hash so we can detect whether the human actually edited.
before_mtime = asc.stat().st_mtime

proc = subprocess.Popen([str(find_ltspice()[0].exe), str(asc)])
proc.wait()

after = read_asc(asc)
edited = asc.stat().st_mtime > before_mtime

if not edited:
    print("Human didn't touch it — accepting agent's version.")
else:
    print("Human edited; re-simulating and diffing vs baseline.")
    net = schematic_to_netlist(after)
    write_net(net, "candidate.net")
    new_run = run_net("candidate.net")

    # Was there a baseline? diff to show the cost of the human's edit.
    if Path("baseline.raw").exists():
        result = diff("baseline.raw", new_run.raw_path)
        if not result.ok:
            for t in result.mismatched[:5]:
                print(f"  {t.name}: max_abs={t.max_abs:.3e}")
```

## Scenario 4 — programmatic build → GUI review → acceptance loop

Combine with `.meas`-based acceptance:

```python
import subprocess
from sim_ltspice import find_ltspice, run_net

# Agent proposes, human reviews, agent runs, verifies acceptance.
subprocess.Popen([str(find_ltspice()[0].exe), "design.asc"]).wait()
r = run_net("design.net")
fc = r.log.measures["fc"].value           # Measure dataclass → .value / .expr
assert 950 <= fc <= 1050, f"fc drifted: {fc}"
```

The human's job is *review*, not verification. Acceptance still comes
from `.meas` — see `meas_based_acceptance.md`.

## When you do need `sim.gui` (Windows, future)

Edge cases that need pywinauto-level control:

- **Dismiss a blocking "Simulation errors" dialog** that LTspice
  sometimes pops even under `-b` on Windows.
- **Check for an open waveform plot** of a specific `.raw` before
  spawning a second one.
- **Activate / screenshot** the LTspice window for a CI artifact.

Once the sim-cli LTspice driver wires `GuiController` into its session
namespace, the pattern will mirror the other drivers:

```python
# Future — not yet available on LTspice driver as of 2026-04-24.
# gui = sim-cli-injected session handle
dlg = gui.find(title_contains="Simulation errors", timeout_s=2)
if dlg:
    dlg.click("OK")
    gui.wait_until_window_gone("Simulation errors")
```

Track this in the sim-cli LTspice driver — it's a one-line
`GuiController(process_name_substrings=("ltspice","LTspice","XVIIx64"))`
change. Until then, the `subprocess.Popen` pattern above is the canonical
way to drive the LTspice GUI from an agent, and it covers Scenarios 1–4
fully.

## Anti-patterns

- **Don't `time.sleep(10)` after `Popen`.** Use `proc.wait()` — it
  returns the moment the window closes, not on a fixed timer.
- **Don't parse the `.asc` while LTspice has it open.** LTspice
  rewrites the file on save; reading mid-session may hit a partial
  write. Wait for `proc.wait()` first.
- **Don't open the same `.asc` twice.** LTspice is single-instance per
  file on Windows; the second spawn focuses the existing window silently.
- **Don't assume the human edited.** Check `mtime` — the "save &
  close" and "just close" paths look the same from Python otherwise.
