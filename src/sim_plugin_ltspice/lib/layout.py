"""Netlist → Schematic layout engine.

Takes a flat SPICE netlist and produces a Schematic that renders a
readable layout in LTspice. v0.1 targets:

* 2-terminal elements (R, L, C, V, I, D) in a single signal chain from
  a voltage source to one output, with ground-shunts at any node.
* Anything more complex (multi-terminal devices, multi-source
  topologies, feedback loops, subcircuit refs) raises
  ``UnsupportedTopology`` with a diagnostic.

Topology support widens iteratively. See ``plan/layout-topologies.md``
for the target matrix.
"""
from __future__ import annotations

from .netlist import Element, Netlist
from .schematic import (
    Flag,
    Placement,
    Rotation,
    Schematic,
    TextDirective,
    TextKind,
    Wire,
)


GND = "0"
_STAGE_DX = 192       # horizontal spacing between consecutive series stages
_SHUNT_DX = 96        # horizontal offset between parallel shunts on the same node
_GND_DROP = 96        # how far below a shunt's pin2 the ground flag sits


class UnsupportedTopology(ValueError):
    """Raised when a netlist falls outside v0.1's layout scope."""


_TWO_TERM = {"R", "C", "L", "V", "I", "D"}

_PREFIX_TO_SYMBOL = {
    "R": "res",
    "C": "cap",
    "L": "ind",
    "V": "voltage",
    "I": "current",
    "D": "diode",
}

# Canonical pin local coords, read from the stock LTspice symbol library.
# Pin 0 is SpiceOrder 1 (first node in .net), pin 1 is SpiceOrder 2.
_CANONICAL_PINS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "res":     ((16, 16), (16, 96)),
    "cap":     ((16, 0),  (16, 64)),
    "ind":     ((16, 16), (16, 96)),
    "voltage": ((0, 16),  (0, 96)),
    "current": ((0, 16),  (0, 96)),
    "diode":   ((0, 0),   (0, 64)),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def netlist_to_schematic(net: Netlist) -> Schematic:
    """Lay out *net* as a schematic that LTspice can render and simulate."""
    _reject_unsupported(net)

    src = _pick_source(net)
    if src is None:
        raise UnsupportedTopology(
            "v0.1 layout requires at least one V or I source as the signal start"
        )

    net_to_pins = _build_net_map(net.elements)
    chain, shunts = _walk_chain(src, net, net_to_pins)

    schem = Schematic()
    src_rail_net, rail_x, rail_y = _place_source(schem, src)
    _drop_shunts(schem, rail_x, rail_y, shunts.get(src_rail_net, []))
    _place_chain(schem, chain, shunts, rail_x, rail_y)
    _emit_directives(schem, net)
    _emit_title_comment(schem, net)
    return schem


# ---------------------------------------------------------------------------
# Topology gate
# ---------------------------------------------------------------------------

def _reject_unsupported(net: Netlist) -> None:
    for el in net.elements:
        prefix = el.name[:1].upper()
        if prefix == "X":
            raise UnsupportedTopology(
                f"subcircuit reference {el.name!r}: v0.1 layout cannot place "
                "X-elements without a user-provided symbol"
            )
        if prefix in {"Q", "M", "J"}:
            raise UnsupportedTopology(
                f"multi-terminal device {el.name!r}: v0.1 layout is 2-terminal only"
            )
        if prefix in {"E", "F", "G", "H", "B", "K", "T", "S", "W"}:
            raise UnsupportedTopology(
                f"device {el.name!r}: v0.1 layout is R/L/C/V/I/D only"
            )
        if prefix not in _TWO_TERM:
            raise UnsupportedTopology(
                f"unknown device prefix {prefix!r} in {el.name!r}"
            )


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

def _pick_source(net: Netlist) -> Element | None:
    """Prefer the first V source; fall back to I."""
    for el in net.elements:
        if el.name[:1].upper() == "V":
            return el
    for el in net.elements:
        if el.name[:1].upper() == "I":
            return el
    return None


def _build_net_map(
    elements: list[Element],
) -> dict[str, list[tuple[Element, int]]]:
    """Return {net_name: [(element, pin_index), ...]} for 2-terminal pins."""
    m: dict[str, list[tuple[Element, int]]] = {}
    for el in elements:
        if len(el.nodes) < 2:
            continue
        for i, n in enumerate(el.nodes[:2]):
            m.setdefault(n, []).append((el, i))
    return m


def _walk_chain(
    src: Element,
    net: Netlist,
    net_to_pins: dict[str, list[tuple[Element, int]]],
) -> tuple[list[tuple[Element, int]], dict[str, list[Element]]]:
    """Walk series elements starting at src's signal rail.

    Returns ``(chain, shunts)`` where
      * ``chain`` is ``[(element, incoming_pin_idx), ...]`` in traversal
        order. ``incoming_pin_idx`` identifies which of the element's
        two pins faces the previous stage.
      * ``shunts`` maps *rail_net* → list of shunt elements, one pin
        on that rail and the other on ground.
    """
    # V1 in 0 … → nodes[0]=in (rail), nodes[1]=0 (ground).
    rail_net = src.nodes[0]
    other = src.nodes[1] if len(src.nodes) > 1 else GND
    if rail_net == GND and other != GND:
        # source drawn "upside down" — flip so + is the rail.
        rail_net, other = other, rail_net
    if other != GND:
        raise UnsupportedTopology(
            f"source {src.name!r} is floating between non-ground nets "
            f"({src.nodes!r}); v0.1 needs one pin at ground (0)"
        )

    visited: set[str] = {src.name}
    chain: list[tuple[Element, int]] = []
    shunts: dict[str, list[Element]] = {}

    current_net = rail_net
    while True:
        candidates = [
            (el, pi) for (el, pi) in net_to_pins.get(current_net, [])
            if el.name not in visited
        ]
        if not candidates:
            break

        series: list[tuple[Element, int, str]] = []  # (el, in_idx, far_net)
        for (el, pi) in candidates:
            other_net = el.nodes[1 - pi]
            if other_net == GND:
                shunts.setdefault(current_net, []).append(el)
                visited.add(el.name)
            else:
                series.append((el, pi, other_net))

        if not series:
            break
        if len(series) > 1:
            raise UnsupportedTopology(
                f"net {current_net!r} has {len(series)} series branches; "
                "v0.1 handles a single linear chain"
            )

        el, in_idx, far_net = series[0]
        chain.append((el, in_idx))
        visited.add(el.name)
        current_net = far_net

    # Fallback: elements still unplaced must be shunts on some rail node.
    for el in net.elements:
        if el.name in visited:
            continue
        a, b = el.nodes[0], el.nodes[1] if len(el.nodes) > 1 else GND
        rail_anchor = None
        if a == GND and b != GND:
            rail_anchor = b
        elif b == GND and a != GND:
            rail_anchor = a
        if rail_anchor is None:
            raise UnsupportedTopology(
                f"orphan element {el.name!r} between {a!r} and {b!r}; "
                "v0.1 cannot place it"
            )
        shunts.setdefault(rail_anchor, []).append(el)
        visited.add(el.name)

    return chain, shunts


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def _place_source(
    schem: Schematic, src: Element
) -> tuple[str, int, int]:
    """Anchor the source at (0,0) R0 and return (rail_net, rail_x, rail_y)."""
    prefix = src.name[:1].upper()
    symbol = _PREFIX_TO_SYMBOL[prefix]
    p1, p2 = _CANONICAL_PINS[symbol]

    # Canonical orientation: + pin up (rail), - pin down (ground).
    # Source nodes may be in either order; figure out which to call + and -.
    rail_net = src.nodes[0]
    gnd_net = src.nodes[1] if len(src.nodes) > 1 else GND
    if rail_net == GND and gnd_net != GND:
        rail_net, gnd_net = gnd_net, rail_net
        # Pin1 is still wired to src.nodes[0]. If user wrote the source
        # with rail on pin2, the emitted netlist would swap the node
        # order back, but for layout we prefer the canonical + up.
        # For v0.1 assume the netlist uses the "+ on rail" convention.

    schem.symbols.append(
        Placement(
            symbol=symbol, x=0, y=0, rotation=Rotation.R0,
            attrs={"InstName": src.name, "Value": src.tail},
        )
    )
    rail_world = (p1[0], p1[1])
    gnd_world = (p2[0], p2[1])
    if rail_net != GND:
        schem.flags.append(Flag(rail_world[0], rail_world[1], rail_net))
    schem.flags.append(Flag(gnd_world[0], gnd_world[1], GND))
    return rail_net, rail_world[0], rail_world[1]


def _place_chain(
    schem: Schematic,
    chain: list[tuple[Element, int]],
    shunts: dict[str, list[Element]],
    rail_x0: int,
    rail_y: int,
) -> None:
    """Walk *chain* placing each series element horizontally to the right."""
    prev_x = rail_x0
    for el, in_idx in chain:
        prefix = el.name[:1].upper()
        symbol = _PREFIX_TO_SYMBOL[prefix]
        p1, p2 = _CANONICAL_PINS[symbol]

        # Horizontal orientation with SpiceOrder-1 pin on the LEFT:
        # Rotation.R270 maps (px, py) → (py, -px).
        # For res (16,16) & (16,96): pin1 world_dx = 16, pin2 world_dx = 96.
        # So pin1 ends up to the LEFT of pin2.
        rot = Rotation.R270
        d1 = _rotate_pin(p1[0], p1[1], rot)   # (py, -px)
        d2 = _rotate_pin(p2[0], p2[1], rot)

        # Anchor the incoming pin on the rail. If incoming_idx == 0,
        # pin1 is incoming → goes left; otherwise pin2 is incoming.
        if in_idx == 0:
            in_disp, out_disp = d1, d2
        else:
            in_disp, out_disp = d2, d1

        target_in_x = prev_x + _STAGE_DX
        place_x = target_in_x - in_disp[0]
        place_y = rail_y - in_disp[1]
        in_world = (place_x + in_disp[0], place_y + in_disp[1])
        out_world = (place_x + out_disp[0], place_y + out_disp[1])

        schem.symbols.append(
            Placement(
                symbol=symbol, x=place_x, y=place_y, rotation=rot,
                attrs={"InstName": el.name, "Value": el.tail},
            )
        )
        # Wire the rail gap into the element.
        schem.wires.append(Wire(prev_x, rail_y, in_world[0], in_world[1]))

        # Label the incoming net if user-named.
        in_net = el.nodes[in_idx]
        if _is_user_net(in_net):
            schem.flags.append(Flag(in_world[0], in_world[1], in_net))

        # Drop shunts attached to the OUTGOING node (far side of the stage).
        out_net = el.nodes[1 - in_idx]
        if out_net in shunts:
            _drop_shunts(schem, out_world[0], out_world[1], shunts[out_net])

        prev_x = out_world[0]

    # Label the final rail point if the terminal net is user-named.
    if chain:
        last_el, last_in = chain[-1]
        final_net = last_el.nodes[1 - last_in]
        if _is_user_net(final_net):
            # Flag was already placed at out_world above? no — we only
            # flag the *incoming* node of each stage. Add the final
            # terminal flag here.
            schem.flags.append(Flag(prev_x, rail_y, final_net))


def _drop_shunts(
    schem: Schematic, anchor_x: int, anchor_y: int, shunts: list[Element]
) -> None:
    """Stack shunts below the rail, each one dropping to its own ground flag."""
    if not shunts:
        return
    for j, el in enumerate(shunts):
        prefix = el.name[:1].upper()
        symbol = _PREFIX_TO_SYMBOL[prefix]
        p1, p2 = _CANONICAL_PINS[symbol]
        # R0 (vertical): pin1 on top, pin2 on bottom.
        # Anchor pin1 at (anchor_x + j*SHUNT_DX, anchor_y).
        pin_col = anchor_x + j * _SHUNT_DX
        place_x = pin_col - p1[0]
        place_y = anchor_y - p1[1]
        schem.symbols.append(
            Placement(
                symbol=symbol, x=place_x, y=place_y, rotation=Rotation.R0,
                attrs={"InstName": el.name, "Value": el.tail},
            )
        )
        pin1_world = (place_x + p1[0], place_y + p1[1])
        pin2_world = (place_x + p2[0], place_y + p2[1])

        # Wire along the rail to the shunt's pin1 (if it's not exactly on the anchor).
        if pin1_world[0] != anchor_x:
            schem.wires.append(
                Wire(anchor_x, anchor_y, pin1_world[0], pin1_world[1])
            )
        schem.flags.append(Flag(pin2_world[0], pin2_world[1], GND))


# ---------------------------------------------------------------------------
# Directives & title
# ---------------------------------------------------------------------------

def _emit_directives(schem: Schematic, net: Netlist) -> None:
    """Copy SPICE directives as TextKind.SPICE entries under the layout."""
    y = 192
    for d in net.directives:
        text = f"{d.command} {d.args}".strip()
        schem.texts.append(
            TextDirective(x=0, y=y, align="Left", size=2, text=text,
                          kind=TextKind.SPICE)
        )
        y += 32


def _emit_title_comment(schem: Schematic, net: Netlist) -> None:
    if net.title:
        schem.texts.append(
            TextDirective(
                x=0, y=-96, align="Left", size=2,
                text=net.title.lstrip("* ").strip(),
                kind=TextKind.COMMENT,
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_user_net(name: str) -> bool:
    """True iff *name* is a human-named rail, not ground or auto-generated."""
    if name == GND:
        return False
    # auto-names from the flattener look like N001/N002/...
    if len(name) > 1 and name[0].upper() == "N" and name[1:].isdigit():
        return False
    return True


def _rotate_pin(px: int, py: int, rot: Rotation) -> tuple[int, int]:
    """Same pin-coord transform as ``netlist._rotate_pin`` (kept local)."""
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
