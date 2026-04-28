"""Parse LTspice's bundled generic-model files (`lib/cmp/standard.*`).

LTspice ships exactly **eight** model-card files under
`<user-data>/lib/cmp/`, one per primitive device kind:

    standard.bjt   — NPN/PNP transistors
    standard.mos   — N/P-channel MOSFETs
    standard.dio   — diodes
    standard.jft   — JFETs
    standard.cap   — non-ideal capacitors
    standard.ind   — non-ideal inductors
    standard.res   — non-ideal resistors
    standard.bead  — ferrite beads

These are the closed enum of generic-model names that LTspice resolves
without a `.SUBCKT` import. Knowing them lets us lint references like
``Q1 c b e 2N2222`` against the catalogue *before* invoking the
solver — catching typos in CI rather than mid-simulation.

All eight files are **UTF-16** (some with BOM, some without). Reading
with the wrong encoding silently yields space-padded ASCII gibberish,
which is a recurring LTspice gotcha — see
`dev-docs/playbook.md` in sim-proj.

The model-card grammar is the standard SPICE form::

    .MODEL <name> <type>(<param>=<value> <param>=<value> ...
    +     <continuation>)

Continuation lines start with ``+``. Comments start with ``*``.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence


# The eight standard.<kind> files, in canonical order.
KINDS: tuple[str, ...] = (
    "bjt", "mos", "dio", "jft", "cap", "ind", "res", "bead",
)


@dataclass(frozen=True)
class ModelDef:
    """One `.MODEL` entry parsed out of a `lib/cmp/standard.*` file."""

    name: str
    """Model name as written on the `.MODEL` line. Case-preserved."""

    kind: str
    """Source file kind — one of `KINDS`."""

    type: str | None
    """SPICE model type token (e.g. ``NPN``, ``PNP``, ``D``, ``NMOS``).
    May be ``None`` for files where the type is implicit (rare)."""

    source: Path
    """Absolute path to the `standard.<kind>` file this came from."""


# ``.MODEL <name> <type>`` — case-insensitive on the directive name,
# permissive on whitespace. The model body (parens, params) is left to
# the solver — we only care about the (name, type) pair for lint.
_MODEL_RE = re.compile(
    r"^\s*\.model\s+(\S+)\s+([A-Za-z][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)


def _read_utf16(path: Path) -> str:
    """Read a `lib/cmp/` file as text. Handles UTF-16 with or without BOM.

    LTspice writes these files inconsistently — some have a BOM, some
    don't. The plain ``utf-16`` codec auto-detects when a BOM is
    present; without one it assumes platform-native, which is wrong on
    macOS. We sniff the first two bytes ourselves.
    """
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe"):
        # UTF-16 LE with BOM
        return data[2:].decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        # UTF-16 BE with BOM
        return data[2:].decode("utf-16-be", errors="replace")
    # No BOM — empirically all bundled files are UTF-16 LE. Try that
    # first; fall back to UTF-8 on decode failure (defensive).
    try:
        return data.decode("utf-16-le")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _join_continuations(text: str) -> Iterator[str]:
    """Fold ``+``-continuation lines into the preceding logical line."""
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if current:
                yield " ".join(current)
                current = []
            continue
        if line.lstrip().startswith("*"):
            # SPICE comment — skip
            continue
        if line.lstrip().startswith("+"):
            current.append(line.lstrip()[1:].lstrip())
        else:
            if current:
                yield " ".join(current)
            current = [line]
    if current:
        yield " ".join(current)


def parse_cmp(path: Path | str, *, kind: str | None = None) -> list[ModelDef]:
    """Parse one `lib/cmp/standard.<kind>` file.

    Parameters
    ----------
    path
        Path to the file. Decoded as UTF-16 (with BOM auto-detect).
    kind
        Kind tag attached to every emitted ``ModelDef``. If omitted,
        derived from the file extension (``standard.bjt`` → ``"bjt"``).
        Pass an explicit value when the filename doesn't follow the
        ``standard.<kind>`` convention.

    Returns
    -------
    list[ModelDef]
        One per ``.MODEL`` directive found, in source order.
    """
    p = Path(path).resolve()
    if kind is None:
        # standard.bjt → "bjt", or fall back to the suffix without dot.
        suffix = p.suffix.lstrip(".").lower()
        kind = suffix if suffix else "unknown"

    text = _read_utf16(p)
    out: list[ModelDef] = []
    for joined in _join_continuations(text):
        m = _MODEL_RE.match(joined)
        if m:
            out.append(ModelDef(
                name=m.group(1),
                kind=kind,
                type=m.group(2),
                source=p,
            ))
    return out


def _env_cmp_paths() -> list[Path]:
    """Honour ``$LTSPICE_CMP_PATH`` (colon- or semicolon-separated)."""
    raw = os.environ.get("LTSPICE_CMP_PATH")
    if not raw:
        return []
    sep = ";" if sys.platform == "win32" else ":"
    return [Path(p).expanduser() for p in raw.split(sep) if p]


def _default_cmp_paths() -> list[Path]:
    """Platform-default location of `lib/cmp/`. Returns existing dirs only."""
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.append(
            Path.home() / "Library/Application Support/LTspice/lib/cmp"
        )
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "LTspice" / "lib" / "cmp")
    else:
        # Linux + wine: same path inside the wine prefix; we don't
        # try to autodetect it. Callers pass `search_paths=[...]`.
        pass
    return [p for p in candidates if p.is_dir()]


class ComponentModelCatalog:
    """Closed-enum lookup of LTspice's bundled generic models.

    Auto-discovers `lib/cmp/` from ``$LTSPICE_CMP_PATH`` or the
    platform's user-data dir. Pass ``search_paths=`` to override
    discovery (e.g. in tests, or to point at a custom install).

    Parsing happens eagerly at construction time. The 8 files together
    define ~1 500 models in a stock LTspice 26 install — well under
    a millisecond to load — so we don't bother with lazy resolution.

    Attributes
    ----------
    KINDS
        The eight canonical kinds, in source order.
    """

    KINDS = KINDS

    def __init__(self, search_paths: Sequence[Path | str] | None = None):
        if search_paths is None:
            paths = _env_cmp_paths() or _default_cmp_paths()
        else:
            paths = [Path(p).expanduser() for p in search_paths]

        self._search_paths: list[Path] = paths
        self._index: dict[str, ModelDef] = {}
        self._by_kind: dict[str, list[ModelDef]] = {k: [] for k in KINDS}

        for d in paths:
            for kind in KINDS:
                f = d / f"standard.{kind}"
                if not f.is_file():
                    continue
                for model in parse_cmp(f, kind=kind):
                    # Earliest path wins on duplicate names — same
                    # rule as `SymbolCatalog`.
                    self._index.setdefault(model.name, model)
                    self._by_kind[kind].append(model)

    @classmethod
    def from_install(cls, install) -> "ComponentModelCatalog":
        """Discover `lib/cmp/` relative to a known LTspice install.

        Most useful when ``find_ltspice()`` returned multiple installs
        and you want to bind the catalogue to a specific one. Falls
        back to platform-default discovery if no `lib/cmp/` is found
        next to the install.
        """
        # Most installs put lib/cmp under the install dir on Windows
        # before LTspice 26 unpacked it to LOCALAPPDATA. Try both.
        candidates = [
            Path(install.path) / "lib" / "cmp",
            *_default_cmp_paths(),
        ]
        existing = [p for p in candidates if p.is_dir()]
        if existing:
            return cls(search_paths=existing)
        return cls()

    @property
    def search_paths(self) -> list[Path]:
        """The directories that were scanned. Useful for diagnostics."""
        return list(self._search_paths)

    def kinds(self) -> tuple[str, ...]:
        """The eight canonical kinds, in source order."""
        return KINDS

    def names(self) -> list[str]:
        """All model names known to the catalogue, sorted."""
        return sorted(self._index)

    def find(self, name: str) -> ModelDef | None:
        """Look up a model by name. Returns ``None`` if not found.

        Matches LTspice's case-sensitive resolution — ``2N2222`` and
        ``2n2222`` are different names. (LTspice itself is actually
        case-insensitive on model names, but we preserve case here
        and let callers normalise if they need to.)
        """
        return self._index.get(name)

    def models(self, kind: str) -> list[ModelDef]:
        """All models of a given kind, in source order.

        Raises ``KeyError`` for unknown kinds — the eight in
        ``KINDS`` are the only valid values.
        """
        if kind not in self._by_kind:
            raise KeyError(
                f"Unknown kind {kind!r}. Valid kinds: {', '.join(KINDS)}"
            )
        return list(self._by_kind[kind])

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._index

    def __iter__(self) -> Iterator[ModelDef]:
        """Iterate models in (kind, source-order) order."""
        for k in KINDS:
            yield from self._by_kind[k]
