# Platform dispatch — when to run remotely on Windows

LTspice has two install flavors with non-trivial capability gaps. Pick
the right one for what you're doing.

The placeholder `<windows-host>` below stands for whatever Windows
machine is running `sim serve`. Configure it once via
`~/.sim/config.toml`:

```toml
[server]
host = "<windows-host>"   # an IP, a hostname, or a Tailscale name
```

…then `sim run …` routes there automatically with no `--host` flag.
Or pass `--host <windows-host>` per invocation, or set `SIM_HOST`.

## macOS native (LTspice 17.x)

- ✅ `.net` / `.cir` / `.sp` batch runs — fully supported.
- ✅ `.meas` result extraction — fully supported.
- 🟡 `.asc` input — only when the schematic is flat + uses
  shipped-library symbols (`sim-ltspice`'s native `asc2net` flattener
  handles these without invoking LTspice).
- ❌ Hierarchical `.asc`, custom `.subckt` symbols, `-ascii` raw
  output, `-netlist` schematic→netlist conversion.

If `sim run my.asc --solver ltspice` raises `MacOSCannotFlatten`,
your schematic is hitting one of the ❌ cases above. Route via a
Windows host.

## macOS native (LTspice 26.x)

LTspice 26.0.0 was the first parallel macOS+Windows release (Dec 2025).
Capability parity with Windows is greater than 17.x, but the same
flat-asc-only constraint **applies in `sim-ltspice`'s flattener**
because the library deliberately doesn't shell out to LTspice for
authoring (the Python flattener handles flat + library-local
schematics without touching LTspice). For hierarchical or
custom-symbol `.asc` the answer is still: route via a Windows host.

## Windows (LTspice 26.x)

- ✅ Everything. `.asc` input with any topology, `-ascii` raw output,
  full batch surface.
- ⚠️ `-netlist` is **broken on 26.0.1** — see
  [`command_line_switches.md`](command_line_switches.md) "Known
  regressions". Use the in-process flattener instead.
- Reach it via `sim --host <windows-host>` (configurable; see
  `../sim-cli/SKILL.md` for the HTTP dispatch model).

## Decision tree

```
.net input?  ────────────────→ Run local. macOS and Windows both fine.

.asc input + flat + library-only? ──→ Run local on Mac (native asc2net).
                                      Or on a Windows host.

.asc input + hierarchy / custom lib? ──→ Route via sim --host <windows-host>.

Need .raw in ASCII format? ──→ Windows only (`-ascii` flag).

Need schematic→netlist conversion WITHOUT simulating?
                               ──→ Use sim_ltspice.schematic_to_netlist
                                   (the broken `-netlist` flag is no
                                   longer the recommended path).
```

## One command covers both

```bash
# Auto-detect: local if possible, remote Windows host otherwise.
# sim-cli handles the routing based on the input's requirements.
sim run design.asc --solver ltspice

# Force-remote (always go to Windows even when local would work)
sim --host <windows-host> run design.asc --solver ltspice
```

## Why the difference?

LTspice's macOS 17 native build was a direct port with a minimal
command surface (`-b` only). The full CLI — `-b` / `-Run` / `-netlist`
/ `-ascii` / `-FastAccess` / `-encrypt` / `-sync` — only existed on
Windows and in wine. LTspice 26 narrowed the gap considerably (macOS
26 supports more flags), but `sim-ltspice`'s default routing rules
still favor the flatten-locally / solve-on-Windows split for
hierarchical `.asc` because the Python flattener is the most reliable
authoring path on either platform.
