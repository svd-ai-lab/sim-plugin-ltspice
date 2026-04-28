# LTspice command-line switches

Reproduced verbatim from the help bundle shipped with LTspice 26
(`LTspiceHelp/commandlineswitches.htm`). This is the **complete**
documented surface — anything not listed below is not a real flag.

| Flag | Description |
|---|---|
| `-alt` | Set solver to **Alternate**. Overridable by the netlist. |
| `-ascii` | Use ASCII `.raw` files. *"Seriously degrades program performance."* (vendor warning verbatim) |
| `-b` | Run in **batch mode**. `LTspice.exe -b deck.cir` leaves the data in `deck.raw`. |
| `-big` / `-max` | Start as a maximized window. (GUI mode.) |
| `-encrypt` | Encrypt a model library (for 3rd-party model vendors). |
| `-FastAccess` | Convert a binary `.raw` to **Fast Access** format. |
| `-FixUpSchematicFonts` / `-FixUpSymbolFonts` | Migrate very-old user files to modern font defaults. |
| `-ini <path>` | Override the default settings file (`%APPDATA%\LTspice.ini` on Windows, `~/Library/Preferences/LTspice.ini` on macOS). |
| `-I<path>` | Insert `<path>` into the symbol + file search paths. **Must be the last argument; no space between `-I` and `<path>`.** |
| `-netlist` | Batch-convert a `.asc` schematic to a `.net` netlist. |
| `-norm` | Set solver to **Normal**. Overridable by the netlist. |
| `-PCBnetlist` | Batch-convert `.asc` to PCB-format netlist. |
| `-Run` | Open the schematic on the cmdline and immediately simulate (no need to press Run). |
| `-sync` | Update component libraries (re-extracts the bundled `lib.zip` / `examples.zip` to the user-data dir). |
| `-version` | Print the LTspice version. |

Two environment variables are also documented:

- `PASTE_OMEGA` — when set, paste the UNICODE Ω symbol on the
  clipboard instead of `"Ohm"`.
- `CAPITAL_KILO` — when set, use upper-case `K` for *all* metric
  multipliers, not just those ≥ 1000.

## Three modes the sim-plugin-ltspice driver actually uses

```bash
# Batch-solve a netlist  →  emits sibling .log + .raw
LTspice.exe -b deck.net                    # Windows / wine
/Applications/LTspice.app/Contents/MacOS/LTspice -b deck.net  # macOS

# Schematic → netlist (one-shot conversion)
LTspice.exe -netlist deck.asc              # Windows
# ⚠️ BROKEN on LTspice 26.0.1 — see "Known regressions" below.

# Reproducible CI run with a fresh ini (no user state leakage)
LTspice.exe -ini fixtures/clean.ini -b deck.net
```

## Known regressions and gotchas

### `-netlist` is broken on LTspice 26.0.1 (Windows)

`LTspice.exe -netlist <file.asc>` spawns a windowless process that
sits at ~0% CPU indefinitely and never writes a `.net`. Reproduced
against LTspice's own shipped `examples/Educational/MonteCarlo.asc`.
Workarounds:

1. **Preferred:** use `sim_plugin_ltspice.lib.schematic_to_netlist` (the
   in-process Python flattener). No LTspice binary involved.
2. Fall back to LTspice XVII (17.2.4) for `.asc` → `.net` if you have
   it side-by-side.

### `-Run -b` from SSH session 0 hangs indefinitely

LTspice GUI cannot render under `WinSta0\Service`, the session SSH
processes land in. A `-b` invocation from SSH on Windows hangs (no
output, never terminates). Workarounds: run `sim serve` from an
interactive desktop (RDP) session, or invoke from inside an RDP
shell. The `sim-plugin-ltspice` runner has a 300 s default timeout that
makes this fail-fast rather than hang forever.

### `-Run` is a no-op for `-b`

`-b` already starts simulating immediately. `-Run -b` is the form
sim-plugin-ltspice emits on Windows because some older docs paired them; it
behaves identically to `-b` alone.

### `-I<path>` ordering trap

`-I` *must be the last argument*, and there is *no space* between
`-I` and the path. `-I C:\my\syms` is wrong. `-IC:\my\syms` is right.
Quoting rules apply for paths with spaces: `"-IC:\Program Files\my syms"`.

### `-ini <path>` tradeoff

The `.ini` is INI-with-binary-tail (4 KB-ish). The header is plain
text (`[Options]`, `UUID=…`, `CaptureAnalytics=…`); the rest is a
binary blob holding window positions, recent-files, plot defaults,
search paths. For CI, an empty file works — LTspice repopulates
defaults on first write.

## Recipes

### Reproducible CI run

```bash
mkdir -p tests/fixtures/ltspice
echo "[Options]" > tests/fixtures/ltspice/clean.ini
echo "CaptureAnalytics=false" >> tests/fixtures/ltspice/clean.ini

LTspice.exe -ini "$(pwd)/tests/fixtures/ltspice/clean.ini" -b deck.net
```

### Inject a custom symbol path at run time

```bash
# Last-arg discipline; no space after -I
LTspice.exe -b deck.net -I"$(pwd)/vendor-symbols"
```

### Reformat a `.raw` to ASCII (post-hoc)

```bash
LTspice.exe -b -ascii deck.net   # at simulate time
# or, if you already have a binary .raw, reformat with -FastAccess (different format)
```

## What's *not* a flag

Common myths and look-alikes that are not actually CLI flags:

- `-o <path>` / `--output` — does not exist; output filenames derive from the input.
- `-q` / `-quiet` — does not exist; redirect stdout/stderr to suppress.
- `-h` / `-help` / `-?` — does not exist (GUI-only app, no stdout help).
- `-batch` — does not exist; the flag is just `-b`.
- `--version` (double dash) — does not exist; LTspice's flag is `-version`.

Source of truth lives in the shipped help bundle:
`%LOCALAPPDATA%\Programs\ADI\LTspice\LTspiceHelp\commandlineswitches.htm`
on Windows, or its [community mirror at ltwiki.org](https://ltwiki.org/LTspiceHelp/LTspiceHelp/Command_Line_Switches.htm).
