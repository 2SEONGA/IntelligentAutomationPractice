"""판단 기준 세트 저장·병합·스냅샷 검사: `python tests/test_criteria.py`."""
from pathlib import Path
import sys
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.web import criteria, runs  # noqa: E402
from bid_review.web.criteria import CriteriaError  # noqa: E402

AGGRESSIVE = {"min_amount_krw": 2000000000, "core_agencies": ["조달청"], "decision": {"go": 5, "review": 3}}


def fresh():
    runs.OUTPUTS = Path(tempfile.mkdtemp())


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return
    raise AssertionError(f"{exc.__name__} 안 남: {args}")


def test_default():
    fresh()
    got = criteria.resolve()
    assert got["name"] == "기본" and got["notice_filter"]["min_amount_krw"] == 4000000000
    assert [s["name"] for s in criteria.list_sets()] == ["기본"]


def test_save_and_merge():
    fresh()
    criteria.save_set("공격적", AGGRESSIVE)
    got = criteria.resolve("공격적")
    assert got["notice_filter"]["min_amount_krw"] == 2000000000
    assert got["notice_filter"]["ict_classes"] == ["ICT 서비스"]
    s3 = next(i for i in got["checklist"]["scoring"] if i["id"] == "S3")
    assert s3["core_agencies"] == ["조달청"] and got["checklist"]["decision"] == {"go": 5, "review": 3}
    assert criteria.resolve()["notice_filter"]["min_amount_krw"] == 4000000000  # default untouched
    assert criteria.values_of("공격적")["min_amount_krw"] == 2000000000
    assert criteria.values_of("기본")["min_amount_krw"] == 4000000000


def test_s2_keywords_from_json_keys():
    fresh()
    criteria.save_set("키워드", {"s2_keywords": {"2": ["운영"], "1": ["구축"]}})
    s2 = next(i for i in criteria.resolve("키워드")["checklist"]["scoring"] if i["id"] == "S2")
    assert s2["keywords"] == {2: ["운영"], 1: ["구축"]}


def test_names():
    fresh()
    for bad in ("기본", "../x", "a/b", "a\\b", "a:b", ".hidden", " ", "x" * 31):
        raises(CriteriaError, criteria.save_set, bad, {})
    criteria.save_set(" 공격적 ", {})
    criteria.save_set(unicodedata.normalize("NFD", "공격적"), {"deadline_warn_days": 5})
    assert [s["name"] for s in criteria.list_sets()] == ["기본", "공격적"]
    assert list((runs.OUTPUTS / "criteria" / "sets").iterdir()) == [runs.OUTPUTS / "criteria" / "sets" / "공격적.json"]


def test_values():
    fresh()
    for bad in ({"decision": {"go": 4, "review": 4}}, {"min_amount_krw": -1}, {"min_amount_krw": "1"},
                {"min_amount_krw": True}, {"deadline_warn_days": 31}, {"unknown": 1}, {"field_keywords": "AI"},
                {"decision": {"go": 11, "review": 4}}, {"s2_keywords": {"3": ["x"]}}):
        raises(CriteriaError, criteria.save_set, "x", bad)
    criteria.save_set("x", {"field_keywords": ["AI", " ", "", "AI"]})
    assert criteria.resolve("x")["notice_filter"]["field_keywords"] == ["AI"]


def test_active_and_delete():
    fresh()
    criteria.save_set("공격적", AGGRESSIVE)
    raises(KeyError, criteria.set_active, "없음")
    raises(KeyError, criteria.resolve, "없음")
    criteria.set_active("공격적")
    assert criteria.active_name() == "공격적" and criteria.resolve()["name"] == "공격적"
    raises(runs.Conflict, criteria.delete_set, "공격적")
    raises(CriteriaError, criteria.delete_set, "기본")
    (runs.OUTPUTS / "criteria" / "sets" / "공격적.json").unlink()
    assert criteria.active_name() == "기본"
    criteria.save_set("b", {})
    criteria.delete_set("b")
    raises(KeyError, criteria.delete_set, "b")


def test_lookup_stays_inside_sets_folder():
    fresh()
    (runs.OUTPUTS / "criteria" / "sets").mkdir(parents=True)
    (runs.OUTPUTS / "criteria" / "outside.json").write_text('{"values": {"min_amount_krw": 1}}', encoding="utf-8")
    raises(KeyError, criteria.resolve, "../outside")
    raises(KeyError, criteria.set_active, "../outside")


def test_snapshot():
    fresh()
    folder = Path(tempfile.mkdtemp())
    assert criteria.checklist_for(folder)[0] == "기본"
    criteria.save_set("공격적", AGGRESSIVE)
    criteria.set_active("공격적")
    criteria.snapshot(folder)
    criteria.set_active("기본")
    name, checklist = criteria.checklist_for(folder)
    assert name == "공격적" and checklist["decision"] == {"go": 5, "review": 3}
    assert any(i["id"] == "S2" and 2 in i["keywords"] for i in checklist["scoring"])  # int keys survive JSON


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("criteria OK")
