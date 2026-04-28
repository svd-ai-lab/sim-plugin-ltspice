"""LTspice `.asy` symbol library index.

A lazy catalog of symbol definitions. Walks a set of search paths, maps
symbol name → `.asy` file, and parses on demand. Primary use case is
`schematic_to_netlist` needing pin ordering and the `Prefix` SYMATTR
for a symbol referenced in a `.asc` file.

Search paths resolve in this order:

1. Explicit `search_paths=[…]` argument
2. ``$SIM_LTSPICE_SYM_PATHS`` (os.pathsep-separated) — agent override
3. Platform defaults:
   - macOS:   `~/Library/Application Support/LTspice/lib/sym`
              `~/Documents/LTspice/lib/sym`
   - Windows: `%LOCALAPPDATA%\\LTspice\\lib\\sym`
              `%USERPROFILE%\\Documents\\LTspice\\lib\\sym`

`.asy` grammar (simplified):

    Version <n>
    SymbolType CELL|BLOCK|AUTO
    LINE / RECTANGLE / CIRCLE / ARC ...     — decorative, ignored
    WINDOW <idx> <x> <y> <align> <size>     — attribute display pos, ignored here
    SYMATTR Prefix       <letter>           — R / C / L / V / X / ...
    SYMATTR SpiceModel   <lib>              — e.g. "LTC.lib"
    SYMATTR Description  <text>
    SYMATTR Value / Value2 ...
    PIN <x> <y> <align> <order_hint>
    PINATTR PinName  <name>
    PINATTR SpiceOrder <n>
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Pin:
    """A single pin on a symbol.

    ``spice_order`` is the ordinal in the SPICE subcircuit pin list for
    the symbol (1-based), or ``None`` for symbols without a
    ``PINATTR SpiceOrder``.
    """

    name: str
    x: int
    y: int
    spice_order: int | None = None


@dataclass
class SymbolDef:
    """Parsed `.asy` symbol definition."""

    name: str                             # normalized basename, e.g. "res"
    path: Path                            # source .asy path
    symbol_type: str = "CELL"             # CELL | BLOCK | AUTO
    prefix: str | None = None             # SYMATTR Prefix (R / C / V / X …)
    spice_model: str | None = None        # SYMATTR SpiceModel (library ref)
    default_value: str | None = None      # SYMATTR Value
    description: str | None = None        # SYMATTR Description
    pins: list[Pin] = field(default_factory=list)
    category: str = ""                    # top-level sym subdir, e.g. "Opamps"

    def ordered_pins(self) -> list[Pin]:
        """Pins sorted by SpiceOrder ascending, unspecified orders last."""
        return sorted(
            self.pins,
            key=lambda p: (p.spice_order is None, p.spice_order or 0),
        )


def _default_sym_paths() -> list[Path]:
    """Platform defaults for LTspice's shipped symbol library."""
    paths: list[Path] = []
    if sys.platform == "darwin":
        paths.append(Path.home() / "Library/Application Support/LTspice/lib/sym")
        paths.append(Path.home() / "Documents/LTspice/lib/sym")
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.append(Path(local) / "LTspice/lib/sym")
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            paths.append(Path(userprofile) / "Documents/LTspice/lib/sym")
    return paths


def _env_sym_paths() -> list[Path]:
    override = os.environ.get("SIM_LTSPICE_SYM_PATHS")
    if not override:
        return []
    return [Path(p) for p in override.split(os.pathsep) if p]


def parse_asy(path: Path | str) -> SymbolDef:
    """Parse a single `.asy` file into a `SymbolDef`."""
    path = Path(path)
    data = path.read_bytes()
    # `.asy` files historically use various encodings; tolerant decode.
    if data.startswith(b"\xff\xfe"):
        text = data[2:].decode("utf-16-le", errors="replace")
    elif len(data) >= 4 and data[1] == 0 and data[3] == 0:
        text = data.decode("utf-16-le", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")

    sym = SymbolDef(name=path.stem, path=path)
    current_pin: Pin | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head = line.split(None, 1)[0]

        # Any statement other than PINATTR terminates the current pin block.
        if current_pin is not None and head != "PINATTR":
            sym.pins.append(current_pin)
            current_pin = None

        if line.startswith("SymbolType "):
            parts = line.split()
            if len(parts) >= 2:
                sym.symbol_type = parts[1].upper()
            continue
        if line.startswith("SYMATTR "):
            parts = line.split(None, 2)
            if len(parts) == 3:
                key, value = parts[1], parts[2]
                if key == "Prefix":
                    sym.prefix = value
                elif key == "SpiceModel":
                    sym.spice_model = value
                elif key == "Description":
                    sym.description = value
                elif key == "Value":
                    sym.default_value = value
            continue
        if line.startswith("PIN "):
            parts = line.split()
            # PIN <x> <y> <align> <order_hint>
            if len(parts) >= 3:
                try:
                    current_pin = Pin(name="", x=int(parts[1]), y=int(parts[2]))
                    continue
                except ValueError:
                    pass
            continue
        if line.startswith("PINATTR ") and current_pin is not None:
            parts = line.split(None, 2)
            if len(parts) == 3:
                key, value = parts[1], parts[2]
                if key == "PinName":
                    current_pin.name = value
                elif key == "SpiceOrder":
                    try:
                        current_pin.spice_order = int(value)
                    except ValueError:
                        pass
            continue
        # Every other statement (LINE / RECTANGLE / CIRCLE / ARC / WINDOW / TEXT
        # / Version) is irrelevant to the netlist-side catalog use case.

    if current_pin is not None:
        sym.pins.append(current_pin)

    return sym


class SymbolCatalog:
    """Lazy, install-rooted catalog of LTspice `.asy` symbols.

    Construction is cheap — it only resolves search paths and walks the
    tree to build a ``name → path`` index. Parsing happens on first
    `find()` for each symbol and is cached per-instance.
    """

    def __init__(self, search_paths: list[Path] | None = None):
        if search_paths is not None:
            self._search_paths = [Path(p) for p in search_paths]
        else:
            self._search_paths = _env_sym_paths() or _default_sym_paths()
        self._search_paths = [p for p in self._search_paths if p.is_dir()]

        self._index: dict[str, tuple[Path, str]] = {}   # name -> (path, category)
        self._cache: dict[str, SymbolDef] = {}
        self._build_index()

    def _build_index(self) -> None:
        for root in self._search_paths:
            for asy in root.rglob("*.asy"):
                rel = asy.relative_to(root)
                # Category = immediate subdir under root (e.g. "Opamps"),
                # empty string if .asy sits directly at root.
                category = rel.parts[0] if len(rel.parts) > 1 else ""
                name = asy.stem
                # Case-fold key so .find("res") matches "Res.asy" if any.
                # Keep the first occurrence — user-local dirs listed first
                # win over system defaults.
                key = name.casefold()
                self._index.setdefault(key, (asy, category))

    # -- Public surface ------------------------------------------------------

    def search_paths(self) -> list[Path]:
        """The resolved search-path list, in priority order."""
        return list(self._search_paths)

    def names(self) -> list[str]:
        """All symbol names known to the catalog. Basenames only."""
        return sorted({path.stem for path, _ in self._index.values()})

    def categories(self) -> dict[str, list[str]]:
        """Map category (e.g. "Opamps") → sorted list of symbol names."""
        out: dict[str, list[str]] = {}
        for path, category in self._index.values():
            out.setdefault(category, []).append(path.stem)
        return {k: sorted(v) for k, v in out.items()}

    def find(self, name: str) -> SymbolDef | None:
        """Return the parsed `SymbolDef` for `name`, or `None` if unknown.

        Name lookup is case-insensitive (LTspice itself is tolerant;
        `RES`, `Res`, and `res` all refer to the same symbol).
        """
        key = name.casefold()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        entry = self._index.get(key)
        if entry is None:
            return None
        path, category = entry
        sym = parse_asy(path)
        sym.category = category
        self._cache[key] = sym
        return sym

    def __contains__(self, name: str) -> bool:
        return name.casefold() in self._index
