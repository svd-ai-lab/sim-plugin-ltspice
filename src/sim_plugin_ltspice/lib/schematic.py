"""In-memory model of an LTspice schematic (`.asc`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Rotation(Enum):
    """LTspice symbol rotations.

    `R*` rotate counter-clockwise in LTspice's y-down screen frame.
    `M*` applies the same rotation, then mirrors across x. All eight
    orientations are round-trip stable. See ``netlist._rotate_pin`` for
    the empirically verified pin-coord transformations.
    """

    R0 = "R0"
    R90 = "R90"
    R180 = "R180"
    R270 = "R270"
    M0 = "M0"
    M90 = "M90"
    M180 = "M180"
    M270 = "M270"


class TextKind(Enum):
    """Distinguishes `TEXT` usage.

    LTspice tags SPICE directives by a leading ``!`` and comments by a
    leading ``;``. Anything else is treated as a free-form label.
    """

    SPICE = "spice"      # leading '!'  — .tran / .meas / .include / ...
    COMMENT = "comment"  # leading ';'  — human-readable notes
    LABEL = "label"      # everything else


@dataclass
class Placement:
    """A placed symbol instance (one `SYMBOL` block in the .asc)."""

    symbol: str                       # e.g. "res", "cap", "voltage", "LT1001"
    x: int
    y: int
    rotation: Rotation = Rotation.R0
    attrs: dict[str, str] = field(default_factory=dict)     # SYMATTR ...
    windows: list[Window] = field(default_factory=list)     # WINDOW ...


@dataclass
class Window:
    """Position override for a displayed symbol attribute.

    LTspice emits ``WINDOW <n> <x> <y> <align> <size>`` inside a
    ``SYMBOL`` block to move the displayed `SYMATTR` text relative to
    the symbol's origin. We preserve it for round-trip fidelity.
    """

    index: int
    x: int
    y: int
    align: str = "Left"
    size: int = 2


@dataclass
class Wire:
    """A straight line segment connecting two grid points."""

    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class Flag:
    """A `FLAG` — net label or ground marker.

    ``net == "0"`` is LTspice's ground. Any other string is a user
    named net.
    """

    x: int
    y: int
    net: str


@dataclass
class TextDirective:
    """A `TEXT` block — SPICE directive, comment, or label."""

    x: int
    y: int
    align: str                        # "Left", "Right", "Center", ...
    size: int                         # text size, 0..7
    text: str                         # without the leading '!' or ';'
    kind: TextKind = TextKind.LABEL


@dataclass
class Schematic:
    """An editable LTspice schematic.

    Attribute order mirrors how LTspice emits them: sheet first, then
    wires/flags, then symbols (each with their WINDOWs + SYMATTRs),
    then text directives. Unknown lines encountered on read are kept
    in `raw_tail` so a round-trip can emit them back verbatim.
    """

    version: int = 4
    sheet: tuple[int, int, int] = (1, 880, 680)   # (sheet_no, w, h)
    wires: list[Wire] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    symbols: list[Placement] = field(default_factory=list)
    texts: list[TextDirective] = field(default_factory=list)
    raw_tail: list[str] = field(default_factory=list)        # preserved verbatim
