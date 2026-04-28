"""SPICE netlist reader, writer, and `Schematic → Netlist` flattener.

The netlist layer underpins `.asc` execution: LTspice 26 on Windows has a
broken `-netlist` pass (hangs without producing `.net`), and the macOS
build has never supported that mode, so we flatten schematics to netlists
in Python and invoke `LTspice -b <.net>` directly.

Grammar we handle (LTspice dialect of SPICE):

- First non-blank line is the **title** (bare comment, per SPICE convention).
- ``* …`` lines are comments.
- Lines starting with ``+`` continue the previous element/directive.
- ``<prefix><InstName> <node>+ <tail...>`` element lines, where ``prefix``
  is the single-letter SPICE device class (R, C, L, V, I, D, Q, M, J, X, …).
- ``.<command> <args...>`` directives (``.tran``, ``.meas``, ``.model``,
  ``.include``, ``.lib``, ``.end``, …).

The parser is deliberately permissive: it treats anything that is neither
a comment, a directive, nor a recognisable element prefix as an opaque
``raw_tail`` entry so round-trip over unfamiliar LTspice extensions stays
lossless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .schematic import Rotation, Schematic
from .symbols import Pin, SymbolCatalog, SymbolDef


# SPICE element-prefix letters; a line starting with one of these letters
# followed by an alphanumeric identifier is treated as a circuit element.
# `K` (mutual inductance) has node-less syntax but we still capture it here.
_ELEMENT_PREFIXES = set("RCLVIDQMJEFGHBSTWXK")

# Element classes whose trailing token is a model / subckt name, not a
# value. Used by `schematic_to_netlist` to know when to emit ``Value`` vs.
# the subcircuit reference as the tail.
_MODEL_CLASSES = set("DQMJX")


@dataclass
class Element:
    """One circuit element line.

    The tail is everything after the node list: a plain value (``1k``),
    a source descriptor (``PULSE(0 1 0 1u 1u 1m 2m)``), or a
    model-name+params string (``NMOS L=1u W=10u``). We don't sub-parse
    it — preserving the original spelling matters for round-trip.
    """

    name: str                       # "R1", "X_opamp", "V1"
    nodes: list[str]                # ["in", "out"] or longer
    tail: str                       # value / source expr / model + params


@dataclass
class Directive:
    """A dot-prefixed directive (`.tran`, `.meas`, `.model`, ...)."""

    command: str                    # ".tran"
    args: str                       # rest of the line


@dataclass
class Netlist:
    """In-memory SPICE netlist.

    ``title`` is the first non-blank line (SPICE convention). We model it
    separately from `comments` so the writer can emit it first without
    string-prefix surgery.
    """

    title: str = ""
    elements: list[Element] = field(default_factory=list)
    directives: list[Directive] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)   # leading `*` comments after title


# ----------------------------------------------------------------------
# .net I/O
# ----------------------------------------------------------------------

def _read_net_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if len(data) >= 4 and data[1] == 0 and data[3] == 0:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _join_continuations(lines: list[str]) -> list[str]:
    """Collapse ``+``-prefixed continuation lines onto the previous line."""
    out: list[str] = []
    for raw in lines:
        stripped = raw.lstrip()
        if stripped.startswith("+") and out:
            out[-1] = out[-1].rstrip() + " " + stripped[1:].strip()
        else:
            out.append(raw)
    return out


def parse_net(path: Path | str) -> Netlist:
    """Parse a SPICE `.net` file into a `Netlist`."""
    path = Path(path)
    text = _read_net_text(path)
    raw_lines = _join_continuations(text.splitlines())

    net = Netlist()
    seen_title = False

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue

        if not seen_title:
            # The SPICE title is the first non-blank line, conventionally
            # a comment. We strip any leading `*` so the title reads cleanly.
            net.title = line.lstrip("* ").rstrip()
            seen_title = True
            continue

        if line.startswith("*"):
            net.comments.append(line[1:].lstrip())
            continue

        if line.startswith("."):
            parts = line.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            if cmd == ".end":
                # `.end` terminates the netlist; nothing after it matters.
                break
            net.directives.append(Directive(command=cmd, args=args))
            continue

        head = line[:1].upper()
        if head in _ELEMENT_PREFIXES:
            elem = _parse_element_line(line, head)
            if elem is not None:
                net.elements.append(elem)
                continue
        # Unrecognised — drop into comments so a round-trip stays lossless.
        net.comments.append(line)

    return net


def _parse_element_line(line: str, prefix: str) -> Element | None:
    """Split a SPICE element line into (name, nodes, tail).

    Node count per device class is fixed by SPICE; we use it to pivot
    between nodes and the trailing value/model+params string without
    needing to know the semantics of the tail.
    """
    tokens = line.split()
    if len(tokens) < 2:
        return None
    name = tokens[0]

    node_count = _node_count_for(prefix, tokens)
    if node_count is None or len(tokens) < 1 + node_count:
        return None

    nodes = tokens[1 : 1 + node_count]
    tail = " ".join(tokens[1 + node_count :])
    return Element(name=name, nodes=nodes, tail=tail)


def _node_count_for(prefix: str, tokens: list[str]) -> int | None:
    """SPICE node counts per device class, covering what LTspice emits."""
    if prefix in {"R", "C", "L", "V", "I", "D", "B"}:
        return 2
    if prefix in {"Q", "J"}:
        # BJT: C B E [S], JFET: D G S. LTspice always emits the 3-node form
        # for J; Q is 3-node unless a 4th token exists *and* is not the model.
        return 3
    if prefix == "M":
        return 4
    if prefix in {"E", "G"}:
        # Linear VCVS / VCCS: OUT+ OUT- IN+ IN-
        return 4
    if prefix in {"F", "H"}:
        # CCCS / CCVS: OUT+ OUT- <ctrl_elem>. Parsed as 2 nodes; the
        # controlling source name stays in the tail.
        return 2
    if prefix == "K":
        # Mutual inductance: K <L1> <L2> <k> — no nodes.
        return 0
    if prefix == "T":
        return 4
    if prefix in {"S", "W"}:
        return 4
    if prefix == "X":
        # Subcircuit: everything between the name and the LAST non-param
        # token is a node. Heuristic: tokens until we see a '=' are nodes
        # or the subckt name; we treat all tokens except the last as nodes
        # and peel the last one off as the subckt name. This matches the
        # common case; `.param name=value` tails are absorbed into tail.
        last_node_idx = len(tokens) - 1
        for i in range(1, len(tokens)):
            if "=" in tokens[i]:
                last_node_idx = i - 1
                break
        # `X` always needs ≥1 node and a subckt name.
        if last_node_idx < 2:
            return None
        return last_node_idx - 1
    return None


def write_net(net: Netlist, path: Path | str) -> None:
    """Write `net` to `path` as a SPICE netlist, UTF-8 CRLF-terminated."""
    path = Path(path)
    lines: list[str] = []
    lines.append(f"* {net.title}" if net.title else "*")

    for c in net.comments:
        lines.append(f"* {c}")

    for e in net.elements:
        node_str = " ".join(e.nodes)
        line = f"{e.name} {node_str}".rstrip()
        if e.tail:
            line = f"{line} {e.tail}"
        lines.append(line)

    for d in net.directives:
        line = d.command
        if d.args:
            line = f"{line} {d.args}"
        lines.append(line)

    lines.append(".end")
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


# ----------------------------------------------------------------------
# Schematic → Netlist flattener
# ----------------------------------------------------------------------

class FlattenError(Exception):
    """Raised when a schematic can't be flattened to a netlist.

    Subclasses in `errors.py` (Stage 2g) may further classify this.
    """


def _rotate_pin(px: int, py: int, rot: Rotation) -> tuple[int, int]:
    """Map a pin from symbol-local coords to the placement-local frame.

    LTspice convention (verified empirically against shipped examples):
    `M*` applies rotation first, then mirrors across x (x → -x).
    So M0 = mirror; M90/M180/M270 = rotate by 90/180/270, then mirror.
    """
    if rot is Rotation.R0:
        return (px, py)
    if rot is Rotation.R90:
        return (-py, px)
    if rot is Rotation.R180:
        return (-px, -py)
    if rot is Rotation.R270:
        return (py, -px)
    if rot is Rotation.M0:
        return (-px, py)
    if rot is Rotation.M90:
        return (py, px)
    if rot is Rotation.M180:
        return (px, -py)
    if rot is Rotation.M270:
        return (-py, -px)
    raise AssertionError(f"unreachable rotation {rot!r}")


def _pin_world_xy(
    sym_x: int, sym_y: int, rot: Rotation, pin: Pin
) -> tuple[int, int]:
    dx, dy = _rotate_pin(pin.x, pin.y, rot)
    return (sym_x + dx, sym_y + dy)


class _UnionFind:
    """Minimal union-find over hashable keys, used to compute net equivalence."""

    def __init__(self) -> None:
        self._parent: dict = {}

    def add(self, k) -> None:
        self._parent.setdefault(k, k)

    def find(self, k):
        self.add(k)
        root = k
        while self._parent[root] != root:
            root = self._parent[root]
        # Path-compress.
        cur = k
        while self._parent[cur] != root:
            self._parent[cur], cur = root, self._parent[cur]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def _on_wire_body(wire, x: int, y: int) -> bool:
    """True when point (x,y) lies on the axis-aligned wire segment."""
    if wire.x1 == wire.x2 == x:
        return min(wire.y1, wire.y2) <= y <= max(wire.y1, wire.y2)
    if wire.y1 == wire.y2 == y:
        return min(wire.x1, wire.x2) <= x <= max(wire.x1, wire.x2)
    return False


def _assign_nets(
    schem: Schematic,
    placements_with_pins: list[tuple[int, list[tuple[Pin, tuple[int, int]]]]],
) -> dict[tuple[int, int], str]:
    """Build a coord → net-name map for every pin in `placements_with_pins`.

    Nets are the connected components of wires + pins + flags, joined by:

    1. Wire endpoints merge with each other (the wire itself).
    2. Any pin / flag / wire-endpoint that coincides with another wire's
       body (T-junctions and pins-on-wire-segment) joins that wire's net.

    Each component takes the name of any flag sitting on one of its grid
    points (with ``"0"`` winning if present, matching LTspice's ground
    convention), else an auto-generated ``N001``/``N002``/... identifier.
    """
    uf = _UnionFind()
    for w in schem.wires:
        uf.add((w.x1, w.y1))
        uf.add((w.x2, w.y2))
        uf.union((w.x1, w.y1), (w.x2, w.y2))

    # Collect every "interesting" coordinate so we can join incidentally-
    # coincident points to wire bodies in one pass.
    interesting: set[tuple[int, int]] = set()
    for w in schem.wires:
        interesting.add((w.x1, w.y1))
        interesting.add((w.x2, w.y2))
    for _idx, pins in placements_with_pins:
        for _pin, xy in pins:
            interesting.add(xy)
            uf.add(xy)

    flags_by_xy: dict[tuple[int, int], str] = {}
    for f in schem.flags:
        flags_by_xy[(f.x, f.y)] = f.net
        uf.add((f.x, f.y))
        interesting.add((f.x, f.y))

    # T-junctions + pin-on-wire-body: each coord that lies on any wire's
    # segment joins that wire's net.
    for xy in interesting:
        for w in schem.wires:
            if _on_wire_body(w, xy[0], xy[1]):
                uf.union(xy, (w.x1, w.y1))

    # Component → label. Walk flags first; "0" always wins as ground name.
    comp_label: dict = {}
    for xy, label in flags_by_xy.items():
        root = uf.find(xy)
        existing = comp_label.get(root)
        if existing is None or label == "0":
            comp_label[root] = label

    auto_idx = 0
    out: dict[tuple[int, int], str] = {}
    for _idx, pins in placements_with_pins:
        for _pin, xy in pins:
            root = uf.find(xy)
            label = comp_label.get(root)
            if label is None:
                auto_idx += 1
                label = f"N{auto_idx:03d}"
                comp_label[root] = label
            out[xy] = label
    return out


def schematic_to_netlist(
    schem: Schematic,
    catalog: SymbolCatalog | None = None,
) -> Netlist:
    """Flatten a `Schematic` into a SPICE `Netlist`.

    This is the "native" `-netlist` replacement: for every `SYMBOL`
    placement we look up its `.asy` definition, map each pin from
    symbol-local coords to the world-grid via the placement's rotation,
    resolve each pin's net via wires + flags, then emit a SPICE element
    line using the pin order given by `PINATTR SpiceOrder`.

    TEXT directives marked as SPICE are appended to the netlist as
    directives; comments are dropped (netlist comments are a separate
    convention from schematic annotations).
    """
    catalog = catalog or SymbolCatalog()

    # Build (placement_idx, [(pin, world_xy), ...]) for every symbol first,
    # so the net assigner sees every pin up-front.
    resolved: list[tuple[int, list[tuple[Pin, tuple[int, int]]]]] = []
    sym_defs: list[SymbolDef] = []
    for idx, pl in enumerate(schem.symbols):
        sdef = catalog.find(pl.symbol)
        if sdef is None:
            raise FlattenError(
                f"symbol {pl.symbol!r} not found in catalog "
                f"(searched {[str(p) for p in catalog.search_paths()]})"
            )
        sym_defs.append(sdef)
        pairs = [
            (pin, _pin_world_xy(pl.x, pl.y, pl.rotation, pin))
            for pin in sdef.ordered_pins()
        ]
        resolved.append((idx, pairs))

    xy_to_net = _assign_nets(schem, resolved)

    net = Netlist(title="Generated by sim_ltspice")

    for (idx, pairs), sdef in zip(resolved, sym_defs):
        pl = schem.symbols[idx]
        inst = pl.attrs.get("InstName")
        if not inst:
            raise FlattenError(
                f"symbol at ({pl.x}, {pl.y}) kind {pl.symbol!r} "
                f"has no InstName SYMATTR"
            )
        nodes = [xy_to_net[xy] for _pin, xy in pairs]
        tail = _element_tail(pl, sdef)
        net.elements.append(Element(name=inst, nodes=nodes, tail=tail))

    # SPICE directive text from the schematic.
    from .schematic import TextKind  # local import to dodge cycle
    for t in schem.texts:
        if t.kind is not TextKind.SPICE:
            continue
        body = t.text.strip()
        if not body:
            continue
        if body.startswith("."):
            parts = body.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            net.directives.append(Directive(command=cmd, args=args))
        else:
            # Free-form SPICE (rare; e.g. "K1 L1 L2 1" written as a TEXT).
            tokens = body.split(maxsplit=1)
            head = tokens[0][:1].upper()
            if head in _ELEMENT_PREFIXES:
                elem = _parse_element_line(body, head)
                if elem is not None:
                    net.elements.append(elem)
                    continue
            net.comments.append(body)

    return net


def _element_tail(pl, sdef: SymbolDef) -> str:
    """Emit the trailing token(s) for a placed symbol.

    For subckts / models (X / D / Q / M / J) the tail is the model name
    (Value SYMATTR, which LTspice uses to hold the .subckt name too),
    followed by any param SYMATTRs we preserve verbatim. For passives
    / sources the tail is just Value.
    """
    prefix = (sdef.prefix or pl.attrs.get("InstName", "?")[:1]).upper()
    value = pl.attrs.get("Value", "")

    if prefix in _MODEL_CLASSES:
        extras = []
        for k, v in pl.attrs.items():
            if k in {"InstName", "Value", "Prefix", "SpiceLine", "Value2"}:
                continue
            extras.append(f"{k}={v}")
        spice_line = pl.attrs.get("SpiceLine", "").strip()
        value2 = pl.attrs.get("Value2", "").strip()
        parts = [value] + ([value2] if value2 else []) + ([spice_line] if spice_line else []) + extras
        return " ".join(p for p in parts if p).strip()
    return value
