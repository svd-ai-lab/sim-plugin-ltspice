# Symbol & include search path resolution

When LTspice (or `sim-ltspice`) tries to resolve `<symbol>` references
in a `.asc` or `.lib` / `.include` directives in a `.net`, it walks
search paths in a deterministic order. Knowing this order is the
fastest way to debug "symbol not found" / "library not found" errors.

## Resolution order

```
1. -I<path> from CLI               # injected at invoke time, takes precedence
2. Settings → Search Paths         # persisted in LTspice.ini
3. <schematic-dir>/                # the directory containing the .asc being opened
4. <user-data>/lib/sym/  (recursive)
5. <user-data>/lib/sub/
```

`<user-data>` is:
- Windows: `%LOCALAPPDATA%\LTspice\`
- macOS: `~/Library/Application Support/LTspice/`

`<install-root>/lib/` (under `Programs\ADI\LTspice\`) is **not** on
the search path — the bundled `lib.zip` is unzipped to the user-data
dir on first launch and re-extracted by `LTspice.exe -sync`.

## sim-ltspice extensions

`sim_ltspice.symbols.SymbolCatalog` honours an additional env var on
top of the LTspice-native order:

```
$LTSPICE_SYM_PATH        # colon-separated (POSIX) or semicolon (Windows)
```

This lets you point a single environment at a project-local symbol
dir without modifying `LTspice.ini`. Catalog discovery is:

```
$LTSPICE_SYM_PATH        ─→ first
<platform default lib/sym>  ─→ fallback
```

## Common failure modes

### "Could not find symbol: XYZ" in `.asc`

Symbol resolution failed. Check, in order:

1. **Typo?** `SymbolCatalog().find("XYZ")` returns `None` if the name
   is wrong. Use `.names()` to fuzzy-match.
2. **Wrong category?** Top-level `lib/sym/*.asy` (54 primitives:
   `res`, `cap`, `ind`, `voltage`, `nmos`, `npn`, …) vs. nested
   `lib/sym/PowerProducts/LT3045.asy` (2 755 part-specific symbols).
   The lookup is recursive, so naming the symbol is enough — but if
   two categories collide on a name, the first match wins.
3. **Custom symbol not on path?** Drop the `.asy` next to your `.asc`,
   or pass `-I<dir>` at invoke time, or set `$LTSPICE_SYM_PATH`.
4. **Stale install?** `LTspice.exe -sync` re-extracts the bundled
   `lib.zip` if the user-data dir was nuked.

### "Could not open: foo.lib"

Library resolution failed. Most common cause: relative path in a
`.include` / `.lib` directive that resolves against the *netlist's*
directory, not the cwd. Two fixes:

- Use an absolute path: `.lib "C:\path\to\foo.lib"`
- Use `<schematic-dir>/`-relative: `.lib "./vendor/foo.lib"` and
  ensure the file sits next to the `.net`.

## Symbol library shape (LTspice 26)

`<user-data>/lib/sym/` holds **6 571 `.asy` symbols** organized as:

| Subtree | Count | Purpose |
|---|---:|---|
| `lib/sym/` (top-level) | 54 | Generic primitives — `res`, `cap`, `ind`, `voltage`, `nmos`, `npn`, etc. |
| `lib/sym/PowerProducts/` | 2 755 | Vendor switching regulators, LDOs, motor drivers |
| `lib/sym/Contrib/` | 2 423 | Community-contributed symbols |
| `lib/sym/OpAmps/` | 759 | Op-amp catalogue |
| `lib/sym/SpecialFunctions/` | 196 | Behavioural blocks, controllers |
| `lib/sym/Switches/` | 173 | Voltage/current-controlled switches |
| `lib/sym/References/` | 76 | Voltage references |
| `lib/sym/ADC/` | 60 | A/D converters |
| `lib/sym/Comparators/` | 51 | Comparators |
| `lib/sym/Misc/` | 41 | Miscellaneous |
| `lib/sym/DAC/` | 39 | D/A converters |
| `lib/sym/FilterProducts/` | 26 | Filter parts |
| `lib/sym/Digital/` | 17 | Digital gates |
| `lib/sym/Optos/` | 16 | Opto-isolators |
| `lib/sym/CurrentMonitors/` | 2 | Current sense |
| `lib/sym/Transceivers/` | 2 | Transceivers |

`lib/sub/` ships **2 798 `.sub` files + 2 093 `.lib` files** —
SPICE-text sub-circuit definitions for vendor parts, referenced via
`.lib` / `.include` rather than as schematic symbols.
