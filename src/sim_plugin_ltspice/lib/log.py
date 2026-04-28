"""LTspice .log reader + .MEAS parser.

Encoding varies by version. LTspice 17.x (macOS native) writes UTF-16
LE with no BOM; LTspice 26.x (Windows) writes UTF-8. The reader sniffs
via BOM first, then the "0x00 at every odd byte" pattern (ASCII under
UTF-16 LE), else falls back to UTF-8 and finally Latin-1. A naive chain
that tries utf-16-le first produces silent garbage on UTF-8 logs —
UTF-16 LE decoding never raises on arbitrary bytes.

Measure lines come in several shapes depending on analysis type:

* TRAN / DC, MAX|MIN|AVG|RMS:
      ``vout_pk: MAX(v(out))=0.999955 FROM 0 TO 0.005``
* TRAN / DC, FIND … AT … / WHEN …:
      ``fall_time: V(out)=0.5 AT 1.234e-3``
* AC, MAX|MIN|AVG|RMS — result is complex (magnitude in dB + phase):
      ``peakmag: MAX(mag(v(out)))=(-0.0123613dB,0°) FROM 100 TO 100000``
* AC, WHEN <expr>=<target>:
      ``fr: ph(v(out))=0 AT 5035.04``
* AC, WHEN <expr>=<expr-referencing-other-measure>:
      ``bw_3db_lo: mag(v(out))=peakmag*0.7071 AT 4644.92``
* AC, FIND <expr> AT <freq>:
      ``gain_5k: mag(v(out))=(-0.0893997dB,0°) at 5030``

Windows 26 additionally emits a ``Files loaded:`` block containing an
absolute path (``C:\\Users\\...\\design.net``); the per-line splitter
requires an ``=`` before committing a line as a measure so a drive
letter can't masquerade as a measure name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Measure:
    """A single `.MEAS` result.

    ``value`` is the "primary" scalar the user is most likely after:

    * ``MAX/MIN/AVG/RMS`` → the scalar result (or magnitude for AC
      complex results).
    * ``FIND <expr> AT <freq>`` in AC → the magnitude of the complex
      result at that frequency.
    * ``WHEN <condition>`` → the axis point (frequency or time) at
      which the condition held.

    For AC complex results we also populate ``phase_deg``. For AT-form
    results we populate ``at`` with the axis point.

    **Ambiguity note — TRAN FIND…AT vs TRAN WHEN.** Both forms log as
    ``<expr>=<scalar> AT <scalar>``; the scalar RHS is the measured
    value in the FIND case and the WHEN target in the WHEN case.
    LTspice doesn't echo the original directive, so we can't tell them
    apart from the log alone. ``Measure.value`` defaults to the AT
    axis point (WHEN-style, the common case); the raw RHS number is
    preserved in ``rhs_value`` so callers who know they wrote FIND…AT
    can recover the measured value via ``m.rhs_value``. AC FIND…AT is
    unambiguous because its RHS is a complex tuple, not a scalar — so
    ``value`` already holds the magnitude for that case.
    """

    expr: str
    value: float
    window_from: float | None = None
    window_to: float | None = None
    at: float | None = None
    phase_deg: float | None = None
    rhs_value: float | None = None


@dataclass
class LogResult:
    """Structured view of an LTspice `.log` file."""

    measures: dict[str, Measure] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float | None = None


# Line-level splitter: only commits to a line if it looks like
# ``<ident>: <body-containing-'='>``. The ``=`` requirement filters
# out ``Start Time:``, ``Files loaded:``, drive letters, etc.
#
# Note we deliberately do NOT anchor to ``$``: LTspice 17 on macOS
# writes the ``.meas`` block with CRLF line endings while the rest of
# the file uses LF, and ``$`` in ``re.MULTILINE`` matches only before
# ``\n`` — so an anchored pattern misses every CRLF-terminated meas
# line. The ``[^\n\r]+`` body naturally stops at the line boundary,
# and `_parse_measure_body` rejects non-numeric RHS (which filters
# spurious matches like ``Circuit: ... fr = 1/(...)``).
_MEAS_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z_][\w]*)\s*:\s*(?P<body>[^\n\r]*=[^\n\r]+)",
    re.MULTILINE,
)

# Trailing ``FROM <f> TO <t>`` (TO optional). Matched case-insensitive
# because some LTspice builds capitalize inconsistently.
_FROM_TO_RE = re.compile(
    r"\s+FROM\s+(?P<from>[-+0-9.eE]+)"
    r"(?:\s+TO\s+(?P<to>[-+0-9.eE]+))?\s*$",
    re.IGNORECASE,
)

# Trailing ``AT <axis-point>``. The axis point is always a plain
# decimal in practice — LTspice doesn't emit SI prefixes in the log.
_AT_RE = re.compile(
    r"\s+AT\s+(?P<at>[-+0-9.eE]+)\s*$",
    re.IGNORECASE,
)

# AC complex result: ``(mag[unit],phase°)``. Unit is typically ``dB``
# but LTspice is free to write plain linear magnitude too.
_COMPLEX_RE = re.compile(
    r"^\(\s*(?P<mag>[-+0-9.eE]+)[A-Za-z]*"
    r"\s*,\s*(?P<phase>[-+0-9.eE]+)\s*°?\s*\)$"
)

# Plain scalar: ``-1.23e-4`` optionally followed by a unit suffix
# (``V``, ``A``, ``dB``, …). Unit is stripped.
_SCALAR_RE = re.compile(r"^(?P<num>[-+0-9.eE]+)[A-Za-z]*$")

_ERROR_RE = re.compile(
    r"^(?:Error[:\s]|Fatal[:\s]|Convergence failed|Singular matrix|"
    r"Cannot find|Unknown (?:parameter|device))",
    re.MULTILINE | re.IGNORECASE,
)

_WARN_RE = re.compile(r"^WARNING[:\s].*$", re.MULTILINE | re.IGNORECASE)

_ELAPSED_RE = re.compile(
    r"Total elapsed time:\s*([0-9.]+)\s*seconds",
    re.IGNORECASE,
)


def read_log(path: Path | str) -> str:
    """Read an LTspice `.log` file as text, auto-detecting encoding."""
    path = Path(path)
    if not path.is_file():
        return ""
    data = path.read_bytes()
    if not data:
        return ""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if len(data) >= 4 and data[1] == 0 and data[3] == 0:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def _safe_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_measure_body(
    body: str,
) -> tuple[
    str,
    float,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
] | None:
    """Parse the ``<expr>=<rhs>[ FROM f TO t][ AT a]`` body of a measure line.

    Returns ``(expr, value, window_from, window_to, at, phase_deg,
    rhs_value)`` or ``None`` if the body doesn't resolve to a numeric
    measurement. ``rhs_value`` preserves the raw scalar on the right-
    hand side of ``=`` (or the magnitude of a complex tuple) so callers
    can disambiguate TRAN FIND…AT from TRAN WHEN even though ``value``
    itself defaults to the WHEN interpretation.
    """
    # Split on first '=' — expr may itself contain parens/operators,
    # but the RHS never starts with another top-level name token, and
    # LTspice always writes a single '=' between expr and rhs.
    eq = body.find("=")
    if eq < 0:
        return None
    expr = body[:eq].strip()
    rest = body[eq + 1:]

    # Pull optional FROM/TO first (MAX/MIN/AVG/RMS form); otherwise AT.
    window_from = window_to = at = None
    m_from = _FROM_TO_RE.search(rest)
    if m_from:
        rhs = rest[:m_from.start()].strip()
        window_from = _safe_float(m_from.group("from"))
        window_to = _safe_float(m_from.group("to"))
    else:
        m_at = _AT_RE.search(rest)
        if m_at:
            rhs = rest[:m_at.start()].strip()
            at = _safe_float(m_at.group("at"))
        else:
            rhs = rest.strip()

    if not expr or not rhs:
        return None

    value: float | None
    phase_deg: float | None = None
    rhs_value: float | None = None

    m_complex = _COMPLEX_RE.match(rhs)
    if m_complex:
        # AC complex result — the magnitude (often in dB) is what the
        # user almost always wants; preserve phase separately.
        value = _safe_float(m_complex.group("mag"))
        phase_deg = _safe_float(m_complex.group("phase"))
        rhs_value = value
    else:
        m_scalar = _SCALAR_RE.match(rhs)
        if m_scalar:
            value = _safe_float(m_scalar.group("num"))
            rhs_value = value
        else:
            # RHS is a non-numeric expression like ``peakmag*0.7071``.
            # This only happens in WHEN-style measures where the AT
            # clause carries the answer (a frequency or time).
            value = None

    # For WHEN-style measures the RHS is the *target* the user wrote
    # in the .meas directive, not the measured result — the measured
    # result is the axis point after AT. Fall back to ``at`` in that
    # case. Heuristic: if we have an AT clause AND the RHS is not a
    # complex tuple (AC FIND…AT), treat the line as WHEN and prefer
    # ``at``. Ambiguity: TRAN FIND…AT also logs as scalar + AT — we
    # can't tell from the log alone, so ``rhs_value`` is preserved
    # above for callers that know they wrote FIND rather than WHEN.
    if at is not None and not m_complex:
        value = at

    if value is None:
        return None

    return expr, value, window_from, window_to, at, phase_deg, rhs_value


def parse_log(text_or_path: str | Path) -> LogResult:
    """Parse an LTspice log body (or a path to one) into a `LogResult`."""
    if isinstance(text_or_path, Path) or (
        isinstance(text_or_path, str) and len(text_or_path) < 4096
        and Path(text_or_path).is_file()
    ):
        text = read_log(text_or_path)
    else:
        text = text_or_path  # type: ignore[assignment]

    measures: dict[str, Measure] = {}
    for line_match in _MEAS_LINE_RE.finditer(text):
        name = line_match.group("name")
        body = line_match.group("body")
        parsed = _parse_measure_body(body)
        if parsed is None:
            continue
        expr, value, window_from, window_to, at, phase_deg, rhs_value = parsed
        measures[name] = Measure(
            expr=expr,
            value=value,
            window_from=window_from,
            window_to=window_to,
            at=at,
            phase_deg=phase_deg,
            rhs_value=rhs_value,
        )

    errors = [m.group(0).strip() for m in _ERROR_RE.finditer(text)]
    warnings = [m.group(0).strip() for m in _WARN_RE.finditer(text)]

    elapsed_s: float | None = None
    em = _ELAPSED_RE.search(text)
    if em:
        try:
            elapsed_s = float(em.group(1))
        except ValueError:
            elapsed_s = None

    return LogResult(
        measures=measures,
        errors=errors,
        warnings=warnings,
        elapsed_s=elapsed_s,
    )
