"""`.asc` text reader and writer.

Supports Version 3, 4, and 1003 (beta) on read. Always emits Version 4
on write — that's what every modern LTspice build (17.x / 24.x / 26.x)
ingests cleanly.

Encoding:

- Read: sniff UTF-16 LE / UTF-8 BOM first, then try UTF-8 and fall
  back to Latin-1 so files from any LTspice generation parse.
- Write: UTF-8 with CRLF line endings. LTspice 26.x on Windows
  silently refuses to netlist LF-only .asc files (``-netlist`` exits
  without writing `.net`), so we emit the native format.

Unknown statements (decorative `LINE` / `RECTANGLE` / `CIRCLE`,
`IOPIN`, `BUSTAP`, and friends) are preserved verbatim as `raw_tail`
so semantic round-trip holds even when we don't model every statement
kind.
"""
from __future__ import annotations

from pathlib import Path

from .schematic import (
    Flag,
    Placement,
    Rotation,
    Schematic,
    TextDirective,
    TextKind,
    Window,
    Wire,
)


_SYMBOL_BOUND_STATEMENTS = ("WINDOW", "SYMATTR")


def _decode(data: bytes) -> str:
    """Tolerant text decode. Matches the LTspice generation matrix."""
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


def read_asc(path: Path | str) -> Schematic:
    """Parse a `.asc` file into a `Schematic`.

    Unknown statements are captured in ``schem.raw_tail`` verbatim,
    preserving round-trip fidelity for content we don't model.
    """
    path = Path(path)
    text = _decode(path.read_bytes())
    lines = text.splitlines()

    schem = Schematic()
    current_symbol: Placement | None = None

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        # Any statement that is not a WINDOW or SYMATTR terminates the
        # current SYMBOL block. That guarantees WINDOWs only attach to
        # the immediately preceding SYMBOL.
        first = line.split(None, 1)[0] if line.strip() else ""
        if current_symbol is not None and first not in _SYMBOL_BOUND_STATEMENTS:
            schem.symbols.append(current_symbol)
            current_symbol = None

        if line.startswith("Version "):
            try:
                schem.version = int(line.split()[1])
            except (IndexError, ValueError):
                pass
            continue
        if line.startswith("SHEET "):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    schem.sheet = (int(parts[1]), int(parts[2]), int(parts[3]))
                except ValueError:
                    pass
            continue
        if line.startswith("WIRE "):
            parts = line.split()
            if len(parts) == 5:
                try:
                    schem.wires.append(
                        Wire(int(parts[1]), int(parts[2]),
                             int(parts[3]), int(parts[4]))
                    )
                    continue
                except ValueError:
                    pass
            schem.raw_tail.append(line)
            continue
        if line.startswith("FLAG "):
            parts = line.split(None, 3)
            if len(parts) >= 4:
                try:
                    schem.flags.append(Flag(int(parts[1]), int(parts[2]), parts[3]))
                    continue
                except ValueError:
                    pass
            schem.raw_tail.append(line)
            continue
        if line.startswith("SYMBOL "):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    rotation = Rotation(parts[4])
                except ValueError:
                    rotation = Rotation.R0
                try:
                    current_symbol = Placement(
                        symbol=parts[1],
                        x=int(parts[2]),
                        y=int(parts[3]),
                        rotation=rotation,
                    )
                    continue
                except ValueError:
                    pass
            schem.raw_tail.append(line)
            continue
        if line.startswith("WINDOW ") and current_symbol is not None:
            parts = line.split()
            # WINDOW <idx> <x> <y> <align> <size>
            if len(parts) >= 6:
                try:
                    current_symbol.windows.append(
                        Window(
                            index=int(parts[1]),
                            x=int(parts[2]),
                            y=int(parts[3]),
                            align=parts[4],
                            size=int(parts[5]),
                        )
                    )
                    continue
                except ValueError:
                    pass
            schem.raw_tail.append(line)
            continue
        if line.startswith("SYMATTR ") and current_symbol is not None:
            parts = line.split(None, 2)
            if len(parts) == 3:
                current_symbol.attrs[parts[1]] = parts[2]
                continue
            schem.raw_tail.append(line)
            continue
        if line.startswith("TEXT "):
            parts = line.split(None, 5)
            # TEXT <x> <y> <align> <size> <body>
            if len(parts) == 6:
                try:
                    x = int(parts[1])
                    y = int(parts[2])
                    align = parts[3]
                    size = int(parts[4])
                    body = parts[5]
                    if body.startswith("!"):
                        kind = TextKind.SPICE
                        body = body[1:]
                    elif body.startswith(";"):
                        kind = TextKind.COMMENT
                        body = body[1:]
                    else:
                        kind = TextKind.LABEL
                    schem.texts.append(
                        TextDirective(x=x, y=y, align=align, size=size,
                                      text=body, kind=kind)
                    )
                    continue
                except ValueError:
                    pass
            schem.raw_tail.append(line)
            continue

        # Unknown statement — keep verbatim.
        schem.raw_tail.append(line)

    if current_symbol is not None:
        schem.symbols.append(current_symbol)

    return schem


def write_asc(schem: Schematic, path: Path | str) -> None:
    """Write `schem` to `path` as Version 4 `.asc`, UTF-8, CRLF-terminated."""
    path = Path(path)
    out: list[str] = []
    out.append(f"Version {schem.version}")
    out.append(f"SHEET {schem.sheet[0]} {schem.sheet[1]} {schem.sheet[2]}")

    for w in schem.wires:
        out.append(f"WIRE {w.x1} {w.y1} {w.x2} {w.y2}")

    for f in schem.flags:
        out.append(f"FLAG {f.x} {f.y} {f.net}")

    for sym in schem.symbols:
        out.append(f"SYMBOL {sym.symbol} {sym.x} {sym.y} {sym.rotation.value}")
        for win in sym.windows:
            out.append(
                f"WINDOW {win.index} {win.x} {win.y} {win.align} {win.size}"
            )
        # LTspice emits SYMATTRs in InstName-first order when it writes
        # a fresh .asc, but any order parses. Preserve insertion order
        # so round-trips are structural-equal (dict is insertion-ordered
        # in Python 3.7+).
        for k, v in sym.attrs.items():
            out.append(f"SYMATTR {k} {v}")

    for t in schem.texts:
        prefix = {"spice": "!", "comment": ";", "label": ""}[t.kind.value]
        out.append(f"TEXT {t.x} {t.y} {t.align} {t.size} {prefix}{t.text}")

    if schem.raw_tail:
        out.extend(schem.raw_tail)

    # CRLF required: LTspice 26 on Windows silently rejects LF-terminated
    # .asc files when invoked with `-netlist`. Emit bytes directly so
    # Python's universal-newlines write doesn't re-translate.
    path.write_bytes(("\r\n".join(out) + "\r\n").encode("utf-8"))
