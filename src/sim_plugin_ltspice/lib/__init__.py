"""Python API for LTspice (in-tree library used by sim-plugin-ltspice).

Exposes the runtime layer (installs, batch runner, .log/.raw parsers)
plus the authoring layer (Schematic model, .asc read/write, symbol
catalog, layout engine).

Originally distributed as the standalone ``sim-ltspice`` package; folded
into this plugin in v0.2.0 so a single wheel ships both the driver and
its file-format library, eliminating cross-package release skew.
"""
from __future__ import annotations

__version__ = "0.2.3"

from .asc import read_asc, write_asc
from .cmp import ComponentModelCatalog, ModelDef, parse_cmp
from .install import Install, find_ltspice
from .log import LogResult, Measure, parse_log, read_log
from .layout import UnsupportedTopology, netlist_to_schematic
from .netlist import (
    Directive,
    Element,
    FlattenError,
    Netlist,
    parse_net,
    schematic_to_netlist,
    write_net,
)
from .diff import DiffResult, TraceDiff, diff
from .raw import (
    InvalidExpression,
    RawRead,
    UnsupportedRawFormat,
    Variable,
    trace_names,
)
from .runner import (
    LtspiceError,
    LtspiceNotInstalled,
    NETLIST_SUFFIXES,
    RunResult,
    UnsupportedInput,
    run_asc,
    run_net,
)
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
from .symbols import Pin, SymbolCatalog, SymbolDef, parse_asy

__all__ = [
    "__version__",
    # Install discovery + runner
    "Install",
    "find_ltspice",
    "LogResult",
    "Measure",
    "parse_log",
    "read_log",
    "InvalidExpression",
    "RawRead",
    "UnsupportedRawFormat",
    "Variable",
    "trace_names",
    "DiffResult",
    "TraceDiff",
    "diff",
    "LtspiceError",
    "LtspiceNotInstalled",
    "NETLIST_SUFFIXES",
    "RunResult",
    "UnsupportedInput",
    "run_asc",
    "run_net",
    # Schematic authoring
    "Schematic",
    "Placement",
    "Wire",
    "Flag",
    "TextDirective",
    "TextKind",
    "Window",
    "Rotation",
    "read_asc",
    "write_asc",
    # Symbol catalog
    "SymbolCatalog",
    "SymbolDef",
    "Pin",
    "parse_asy",
    # Component-model catalog (lib/cmp/standard.*)
    "ComponentModelCatalog",
    "ModelDef",
    "parse_cmp",
    # Netlist
    "Directive",
    "Element",
    "FlattenError",
    "Netlist",
    "parse_net",
    "schematic_to_netlist",
    "write_net",
    # Layout (netlist → schematic)
    "UnsupportedTopology",
    "netlist_to_schematic",
]
