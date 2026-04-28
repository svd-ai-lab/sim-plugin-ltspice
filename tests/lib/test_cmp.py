"""Tests for `sim_plugin_ltspice.lib.cmp` — `lib/cmp/standard.*` parsing + catalog."""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_plugin_ltspice.lib import ComponentModelCatalog, ModelDef, parse_cmp
from sim_plugin_ltspice.lib.cmp import KINDS, _read_utf16


FIXTURES = Path(__file__).parent / "fixtures" / "cmp_lib"
CMP_DIR = FIXTURES / "cmp"


# -- UTF-16 reader ----------------------------------------------------------


def test_utf16_no_bom_decodes(tmp_path: Path):
    raw = "* hello\n.MODEL X NPN()\n".encode("utf-16-le")
    f = tmp_path / "standard.bjt"
    f.write_bytes(raw)
    text = _read_utf16(f)
    assert ".MODEL X NPN()" in text


def test_utf16_le_bom_decodes(tmp_path: Path):
    raw = b"\xff\xfe" + "* hello\n.MODEL X D()\n".encode("utf-16-le")
    f = tmp_path / "standard.dio"
    f.write_bytes(raw)
    text = _read_utf16(f)
    assert ".MODEL X D()" in text


def test_utf16_be_bom_decodes(tmp_path: Path):
    raw = b"\xfe\xff" + "* hello\n.MODEL X D()\n".encode("utf-16-be")
    f = tmp_path / "standard.dio"
    f.write_bytes(raw)
    text = _read_utf16(f)
    assert ".MODEL X D()" in text


# -- parse_cmp --------------------------------------------------------------


def test_parse_cmp_extracts_three_bjt_models():
    models = parse_cmp(CMP_DIR / "standard.bjt")
    names = [m.name for m in models]
    assert names == ["2N2222", "2N3904", "2N3906"]


def test_parse_cmp_attaches_kind_from_extension():
    models = parse_cmp(CMP_DIR / "standard.bjt")
    assert all(m.kind == "bjt" for m in models)


def test_parse_cmp_extracts_type_token():
    models = parse_cmp(CMP_DIR / "standard.bjt")
    by_name = {m.name: m for m in models}
    assert by_name["2N2222"].type == "NPN"
    assert by_name["2N3906"].type == "PNP"


def test_parse_cmp_handles_multiline_continuation():
    """The 2N2222 model spans two physical lines (`+` continuation).
    Parser still extracts it correctly."""
    models = parse_cmp(CMP_DIR / "standard.bjt")
    assert "2N2222" in {m.name for m in models}


def test_parse_cmp_handles_bom():
    """standard.dio fixture has a UTF-16 LE BOM."""
    models = parse_cmp(CMP_DIR / "standard.dio")
    names = [m.name for m in models]
    assert names == ["1N4148", "1N4007"]
    assert all(m.kind == "dio" for m in models)


def test_parse_cmp_explicit_kind_override(tmp_path: Path):
    f = tmp_path / "weird_extension.bjt_data"
    f.write_bytes("* hi\n.MODEL Q1 NPN()\n".encode("utf-16-le"))
    models = parse_cmp(f, kind="bjt")
    assert models[0].kind == "bjt"


def test_parse_cmp_skips_comments(tmp_path: Path):
    src = "* not a model\n.MODEL Q1 NPN()\n* also not a model\n"
    f = tmp_path / "standard.bjt"
    f.write_bytes(src.encode("utf-16-le"))
    models = parse_cmp(f)
    assert len(models) == 1
    assert models[0].name == "Q1"


def test_parse_cmp_returns_modeldef_dataclass():
    models = parse_cmp(CMP_DIR / "standard.bjt")
    assert isinstance(models[0], ModelDef)
    assert models[0].source == (CMP_DIR / "standard.bjt").resolve()


# -- ComponentModelCatalog --------------------------------------------------


@pytest.fixture
def cat() -> ComponentModelCatalog:
    return ComponentModelCatalog(search_paths=[CMP_DIR])


def test_catalog_loads_both_files(cat):
    # bjt: 3 models, dio: 2 models, others: 0
    assert len(cat) == 5


def test_catalog_kinds_are_canonical(cat):
    assert cat.kinds() == KINDS
    assert KINDS == ("bjt", "mos", "dio", "jft", "cap", "ind", "res", "bead")


def test_catalog_find_known(cat):
    m = cat.find("2N2222")
    assert m is not None
    assert m.kind == "bjt"
    assert m.type == "NPN"


def test_catalog_find_unknown_returns_none(cat):
    assert cat.find("2N9999") is None


def test_catalog_models_by_kind(cat):
    bjts = cat.models("bjt")
    assert [m.name for m in bjts] == ["2N2222", "2N3904", "2N3906"]
    dios = cat.models("dio")
    assert [m.name for m in dios] == ["1N4148", "1N4007"]
    # Empty kinds
    assert cat.models("mos") == []


def test_catalog_models_unknown_kind_raises(cat):
    with pytest.raises(KeyError):
        cat.models("transistor")


def test_catalog_names_sorted(cat):
    names = cat.names()
    assert names == sorted(names)
    assert "2N2222" in names
    assert "1N4148" in names


def test_catalog_contains(cat):
    assert "2N2222" in cat
    assert "2N9999" not in cat
    assert 42 not in cat  # type-narrowing


def test_catalog_iter_in_kind_order(cat):
    """__iter__ walks in (kind, source-order) — bjt before dio."""
    seen = [m.name for m in cat]
    bjt_idx = seen.index("2N2222")
    dio_idx = seen.index("1N4148")
    assert bjt_idx < dio_idx


def test_catalog_search_paths_exposes_inputs(cat):
    assert cat.search_paths == [CMP_DIR]


def test_catalog_empty_when_no_files(tmp_path: Path):
    """A directory with no `standard.*` files yields an empty catalogue."""
    cat = ComponentModelCatalog(search_paths=[tmp_path])
    assert len(cat) == 0
    assert cat.find("2N2222") is None


def test_catalog_first_path_wins_on_duplicate(tmp_path: Path):
    """Same model name in two paths → first path's definition wins
    (matches SymbolCatalog convention)."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "standard.bjt").write_bytes(
        "* a\n.MODEL DUPE NPN()\n".encode("utf-16-le")
    )
    b = tmp_path / "b"
    b.mkdir()
    (b / "standard.bjt").write_bytes(
        "* b\n.MODEL DUPE PNP()\n".encode("utf-16-le")
    )

    cat = ComponentModelCatalog(search_paths=[a, b])
    assert cat.find("DUPE").type == "NPN"
    # Both are visible in `.models("bjt")` (catalogue is for lookup;
    # ordering reflects scan order).
    assert len(cat.models("bjt")) == 2


def test_catalog_env_path(monkeypatch, tmp_path: Path):
    """LTSPICE_CMP_PATH env var overrides default discovery."""
    (tmp_path / "standard.bjt").write_bytes(
        "* test\n.MODEL Q_FROM_ENV NPN()\n".encode("utf-16-le")
    )
    monkeypatch.setenv("LTSPICE_CMP_PATH", str(tmp_path))
    cat = ComponentModelCatalog()  # no explicit search_paths
    assert cat.find("Q_FROM_ENV") is not None
