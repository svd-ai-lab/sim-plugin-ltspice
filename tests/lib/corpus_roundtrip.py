"""Corpus harness: run every `.asc` in ``~/Documents/LTspice/examples``
through ``read_asc`` → ``write_asc`` → ``read_asc`` and report:

* parse rate — how many files parse without raising on the first read.
* semantic round-trip rate — how many files re-parse to a structure
  equal to the original (wires, flags, symbols, texts, raw_tail).

Not a pytest — it's too slow to run on every invocation and too
environmental (needs the user's examples corpus). Invoke as:

    uv run --no-sync python tests/corpus_roundtrip.py [root]

Exit code 0 iff both rates clear the gate thresholds.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sim_plugin_ltspice.lib.asc import read_asc, write_asc
from sim_plugin_ltspice.lib.schematic import Schematic


PARSE_GATE = 0.95
ROUNDTRIP_GATE = 0.95


def _schem_sig(schem: Schematic) -> tuple:
    """Structural signature for semantic equality.

    Ignores ``version`` (we always emit 4) and ``sheet`` (older files
    may carry the Version 3 coordinate range).
    """
    return (
        tuple((w.x1, w.y1, w.x2, w.y2) for w in schem.wires),
        tuple((f.x, f.y, f.net) for f in schem.flags),
        tuple(
            (
                s.symbol, s.x, s.y, s.rotation.value,
                tuple(sorted(s.attrs.items())),
                tuple((w.index, w.x, w.y, w.align, w.size) for w in s.windows),
            )
            for s in schem.symbols
        ),
        tuple(
            (t.x, t.y, t.align, t.size, t.text, t.kind.value)
            for t in schem.texts
        ),
        tuple(schem.raw_tail),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "root",
        nargs="?",
        default=str(Path.home() / "Documents/LTspice/examples"),
    )
    ap.add_argument("--limit", type=int, default=0,
                    help="process at most N files (0 = all)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.exists():
        print(f"corpus root not found: {root}")
        return 2

    files = sorted(root.rglob("*.asc"))
    if args.limit:
        files = files[: args.limit]
    total = len(files)
    print(f"scanning {total} .asc files under {root}")

    parse_ok = 0
    roundtrip_ok = 0
    parse_fail: list[tuple[Path, str]] = []
    rt_fail: list[tuple[Path, str]] = []

    tmp = Path("/tmp/sim_ltspice_roundtrip.asc")

    for i, p in enumerate(files):
        if i and i % 500 == 0:
            print(f"  [{i}/{total}] parse={parse_ok} rt={roundtrip_ok}")
        try:
            schem = read_asc(p)
        except Exception as exc:  # noqa: BLE001
            parse_fail.append((p, f"{type(exc).__name__}: {exc}"))
            continue
        parse_ok += 1

        try:
            write_asc(schem, tmp)
            schem2 = read_asc(tmp)
        except Exception as exc:  # noqa: BLE001
            rt_fail.append((p, f"write/reparse: {type(exc).__name__}: {exc}"))
            continue

        if _schem_sig(schem) == _schem_sig(schem2):
            roundtrip_ok += 1
        else:
            rt_fail.append((p, "structural mismatch"))

    parse_rate = parse_ok / total if total else 0.0
    rt_rate = roundtrip_ok / total if total else 0.0

    print()
    print(f"parse:      {parse_ok}/{total} ({parse_rate:.1%}), gate {PARSE_GATE:.0%}")
    print(f"round-trip: {roundtrip_ok}/{total} ({rt_rate:.1%}), gate {ROUNDTRIP_GATE:.0%}")

    if parse_fail:
        print(f"\nparse failures ({len(parse_fail)}), first 10:")
        for p, why in parse_fail[:10]:
            print(f"  {p.relative_to(root)} — {why}")
    if rt_fail:
        print(f"\nround-trip failures ({len(rt_fail)}), first 10:")
        for p, why in rt_fail[:10]:
            print(f"  {p.relative_to(root)} — {why}")

    ok = parse_rate >= PARSE_GATE and rt_rate >= ROUNDTRIP_GATE
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
