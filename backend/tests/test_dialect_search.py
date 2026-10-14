import json
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.dialect import DialectDict
from app.search import SearchEngine, hit_at_k, tokenize
from tests.conftest import FIXTURES


@pytest.fixture
def dialect():
    return DialectDict.load(FIXTURES / "dialect_dict.json")


@pytest.fixture
def engine():
    docs = json.loads((FIXTURES / "search_docs.json").read_text(encoding="utf-8"))
    return SearchEngine(docs)


def test_dialect_regions_include_none_first(dialect):
    regions = dialect.regions()
    assert regions[0] == {"id": "none", "name": "모름/표준어", "count": 0}
    assert {"id": "gyeongsang", "name": "경상도", "count": 7} in regions


def test_dialect_entries_and_none(dialect):
    assert dialect.entries("gyeongsang")["뭇나"] == "먹었니"
    assert dialect.entries("none") == {}
    assert dialect.stt_hint("none") == ""
    assert dialect.dict_text("none") == ""
    assert dialect.has_region("none") and not dialect.has_region("seoul")


def test_dialect_hint_is_subset_and_bounded(dialect):
    hint = dialect.stt_hint("gyeongsang", max_words=3)
    assert hint.startswith("경상도 사투리")
    assert "뭇나" in hint and "찌짐" not in hint
    assert len(dialect.stt_hint("gyeongsang", max_chars=10)) < 40


def test_dialect_dict_text_has_all_entries(dialect):
    text = dialect.dict_text("gyeongsang")
    assert "- 정구지 → 부추" in text
    assert len(text.splitlines()) == 7


def test_dialect_rejects_bad_schema(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"gyeongsang": {"name": "경상도"}}', encoding="utf-8")
    with pytest.raises(ValueError):
        DialectDict.load(bad)


def test_real_data_files_match_contract():
    data = PROJECT_ROOT / "data"
    d = DialectDict.load(data / "dialect_dict.json")
    ids = {r["id"] for r in d.regions()}
    assert {"none", "gyeongsang", "jeolla", "chungcheong", "jeju"} <= ids
    assert all(r["count"] >= 30 for r in d.regions() if r["id"] != "none")
    scenarios = json.loads((data / "scenarios.json").read_text(encoding="utf-8"))
    assert {s["id"] for s in scenarios} == {"hospital", "civil", "parcel"}
    docs = json.loads((data / "search_docs.json").read_text(encoding="utf-8"))
    assert len(docs) == 40
    SearchEngine(docs)  # 인덱스 생성 가능


def test_tokenize_syllable_bigrams():
    assert tokenize("기초연금 신청!") == ["기초", "초연", "연금", "신청"]
    assert tokenize("밥 줘") == ["밥", "줘"]
    assert tokenize("") == []


def test_search_top3_and_hit(engine):
    results = engine.search("기초연금 어떻게 신청해요")
    assert 1 <= len(results) <= 3
    assert results[0]["id"] == "d01"
    assert set(results[0]) == {"id", "title", "snippet", "score"}
    assert hit_at_k(results, "d01") is True
    assert hit_at_k(results, "d04") is False
    assert hit_at_k(results, None) is None


def test_search_standard_word_beats_dialect(engine):
    # 방언(정구지)으로는 못 찾고 표준어(부추)로는 찾는다 — B 경로의 표준어 변환이 검색에 주는 효과
    assert hit_at_k(engine.search("정구지 언제 심어요"), "d04") is False
    assert hit_at_k(engine.search("부추 언제 심어요"), "d04") is True


def test_search_empty_query(engine):
    assert engine.search("") == []
    assert engine.search("?!") == []
