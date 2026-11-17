"""판단 기준 세트: "기본"(config YAML) + 사용자가 이름 붙여 저장한 덮어쓰기 값.

세트 파일은 편집 가능한 값만 담고(outputs/criteria/sets/<이름>.json), 판정 때 기본 YAML 위에 덮어쓴다.
검토 건은 시작할 때 체크리스트 설정을 건 폴더 criteria.json에 스냅샷해, 적용 세트가 바뀌어도 판정이 고정된다.
"""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import unicodedata

from ..settings import load_config
from . import runs

DEFAULT = "기본"
SNAPSHOT = "criteria.json"
NOTICE_INTS = {"min_amount_krw": (0, None), "deadline_warn_days": (0, 30)}
NOTICE_LISTS = ("field_keywords", "exclude_contract_terms", "confirm_contract_terms")
_lock = threading.Lock()


class CriteriaError(ValueError):
    pass


def _dir() -> Path:
    return runs.OUTPUTS / "criteria"


def _set_path(name: str) -> Path:
    return _dir() / "sets" / f"{name}.json"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(handle.name, path)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name or "").strip()
    if not 1 <= len(name) <= 30 or any(c in name for c in "/\\:\0") or name.startswith("."):
        raise CriteriaError("기준 이름은 1~30자이고 / \\ : 를 쓸 수 없으며 .으로 시작할 수 없어요.")
    if name == DEFAULT:
        raise CriteriaError("기본 기준은 고칠 수 없어요. 새 이름으로 저장하세요.")
    return name


def _int(key: str, value, low: int, high: int | None) -> int:
    if type(value) is not int or value < low or (high is not None and value > high):
        raise CriteriaError(f"{key} 값은 {low}{'~' + str(high) if high is not None else ' 이상'} 정수여야 해요.")
    return value


def _list(key: str, value) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise CriteriaError(f"{key} 값은 문자열 목록이어야 해요.")
    return list(dict.fromkeys(v.strip() for v in value if v.strip()))


def validate(values: dict) -> dict:
    if not isinstance(values, dict):
        raise CriteriaError("기준 값이 올바르지 않아요.")
    out: dict = {}
    for key, value in values.items():
        if key in NOTICE_INTS:
            out[key] = _int(key, value, *NOTICE_INTS[key])
        elif key in NOTICE_LISTS or key == "core_agencies":
            out[key] = _list(key, value)
        elif key == "s2_keywords":
            if not isinstance(value, dict) or set(map(str, value)) - {"2", "1"}:
                raise CriteriaError("s2_keywords는 2점·1점 키워드 목록이어야 해요.")
            out[key] = {str(k): _list(f"s2_keywords.{k}", v) for k, v in value.items()}
        elif key == "decision":
            if not isinstance(value, dict) or set(value) != {"go", "review"}:
                raise CriteriaError("decision에는 go와 review가 모두 있어야 해요.")
            go, review = _int("go", value["go"], 0, 10), _int("review", value["review"], 0, 10)
            if review >= go:
                raise CriteriaError("검토 점수 기준은 Go 점수 기준보다 낮아야 해요.")
            out[key] = {"go": go, "review": review}
        else:
            raise CriteriaError(f"고칠 수 없는 항목이에요: {key}")
    return out


def _scoring(checklist: dict, item_id: str) -> dict:
    return next(i for i in checklist["scoring"] if i["id"] == item_id)


def _merge(values: dict) -> tuple[dict, dict]:
    notice, checklist = copy.deepcopy(load_config("notice_filter")), copy.deepcopy(load_config("checklist"))
    for key in (*NOTICE_INTS, *NOTICE_LISTS):
        if key in values:
            notice[key] = values[key]
    if "core_agencies" in values:
        _scoring(checklist, "S3")["core_agencies"] = values["core_agencies"]
    if "s2_keywords" in values:
        _scoring(checklist, "S2")["keywords"].update({int(k): v for k, v in values["s2_keywords"].items()})
    if "decision" in values:
        checklist["decision"] = {**checklist["decision"], **values["decision"]}
    return notice, checklist


def _stored(name: str) -> dict:
    if name == DEFAULT:
        return {"values": {}}
    try:
        data = _read(_set_path(clean_name(name)))  # clean_name keeps lookups inside sets/
    except CriteriaError:
        raise KeyError(name) from None
    if data is None:
        raise KeyError(name)
    return data


def active_name() -> str:
    name = (_read(_dir() / "active.json") or {}).get("name", DEFAULT)
    return name if name == DEFAULT or _set_path(name).is_file() else DEFAULT


def list_sets() -> list[dict]:
    active = active_name()
    sets = [{"name": DEFAULT, "default": True, "active": active == DEFAULT, "updated_at": None}]
    for path in sorted((_dir() / "sets").glob("*.json")):
        data = _read(path) or {}
        sets.append({"name": path.stem, "default": False, "active": path.stem == active, "updated_at": data.get("updated_at")})
    return sets


def resolve(name: str | None = None) -> dict:
    name = active_name() if name is None else unicodedata.normalize("NFC", name)
    notice, checklist = _merge(_stored(name)["values"])
    return {"name": name, "notice_filter": notice, "checklist": checklist}


def values_of(name: str) -> dict:
    got = resolve(name)
    notice, checklist = got["notice_filter"], got["checklist"]
    return {**{k: notice[k] for k in (*NOTICE_INTS, *NOTICE_LISTS)},
            "core_agencies": _scoring(checklist, "S3")["core_agencies"],
            "s2_keywords": {str(k): v for k, v in _scoring(checklist, "S2")["keywords"].items()},
            "decision": {k: checklist["decision"][k] for k in ("go", "review")}}


def save_set(name: str, values: dict) -> dict:
    name, values = clean_name(name), validate(values)
    now = datetime.now(runs.KST).isoformat(timespec="seconds")
    with _lock:
        previous = _read(_set_path(name)) or {}
        record = {"name": name, "created_at": previous.get("created_at", now), "updated_at": now, "values": values}
        _write(_set_path(name), record)
    return record


def delete_set(name: str) -> None:
    name = unicodedata.normalize("NFC", name)
    if name == DEFAULT:
        raise CriteriaError("기본 기준은 지울 수 없어요.")
    with _lock:
        if not _set_path(clean_name(name)).is_file():
            raise KeyError(name)
        if active_name() == name:
            raise runs.Conflict("적용 중인 기준은 지울 수 없어요. 다른 기준을 먼저 적용하세요.", "적용 중")
        _set_path(name).unlink()


def set_active(name: str) -> None:
    name = unicodedata.normalize("NFC", name)
    _stored(name)  # KeyError when missing
    with _lock:
        _write(_dir() / "active.json", {"name": name})


def snapshot(folder: Path) -> None:
    got = resolve()
    _write(Path(folder) / SNAPSHOT, {"name": got["name"], "checklist": got["checklist"]})


def _int_keys(checklist: dict) -> dict:
    # JSON turns the YAML int keys (levels 2/1/0, S2 keywords 2/1) into strings; evaluate() expects ints
    for item in checklist.get("scoring", []):
        for key in ("levels", "keywords"):
            if isinstance(item.get(key), dict):
                item[key] = {int(k) if str(k).isdigit() else k: v for k, v in item[key].items()}
    return checklist


def checklist_for(folder: Path) -> tuple[str, dict]:
    data = _read(Path(folder) / SNAPSHOT)
    if data and "checklist" in data:
        return data["name"], _int_keys(data["checklist"])
    return DEFAULT, load_config("checklist")
