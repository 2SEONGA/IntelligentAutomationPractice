"""T2·T3 결과의 출처와 주장 범위를 T4 전에 검사한다.

검색 유사도나 회사 자료의 문구만으로 입찰 자격·실적 인정·동일 사업을 확정하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from ..models import CapabilityMatchingResult, CompanyProfile
from ..policy import TARGET_LEGAL_ENTITY
from ..settings import PROJECT_ROOT


def _source_profile(path: Path, title: str, buyer: str) -> str:
    from .past_projects import project_profile
    return project_profile(path, title, buyer)


def evaluate_agent23(state: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(check_id: str, status: str, detail: str) -> None:
        checks.append({"check_id": check_id, "status": status, "detail": detail})

    if state.get("decision") not in {"company_matching_complete", "human_approved_company_matching_complete"}:
        add("upstream", "not_run", "T2가 완료되지 않아 T2·T3 평가를 실행하지 않았습니다.")
        return {"agent23_evaluation": {"status": "not_run", "checks": checks}}

    try:
        capability = CapabilityMatchingResult.model_validate(state.get("capability") or {})
    except (ValidationError, ValueError) as exc:
        add("t2_schema", "needs_review", f"회사 분석 결과를 읽을 수 없습니다: {exc}")
        capability = None

    company_path = state.get("company_path")
    profile = None
    raw_records: dict[str, dict[str, Any]] = {}
    if company_path:
        try:
            raw_profile = json.loads(Path(company_path).read_text(encoding="utf-8"))
            profile = CompanyProfile.model_validate(raw_profile)
            raw_records = {item["id"]: item for item in raw_profile.get("records", [])
                           if isinstance(item, dict) and item.get("id")}
        except (OSError, ValueError, ValidationError) as exc:
            add("t2_source", "needs_review", f"회사 원자료를 확인할 수 없습니다: {exc}")
    else:
        add("t2_source", "needs_review", "회사 원자료가 없어 실적·법인 귀속을 대조할 수 없습니다.")

    if capability is not None:
        names_match = bool(profile and capability.company_name == profile.company_name
                           and TARGET_LEGAL_ENTITY in profile.company_name)
        add("t2_legal_entity", "passed" if names_match else "needs_review",
            "회사 분석 법인명과 원자료의 분석 대상 법인명이 일치합니다. 법적 실체의 최종 확인은 별도입니다."
            if names_match else "회사 분석 법인과 원자료/분석 대상 법인의 일치 여부를 확인해야 합니다.")
        records = {r.id: r for r in profile.records} if profile else {}
        matches = capability.requirement_matches + capability.eligibility_matches + capability.staffing_matches
        if not matches:
            add("t2_matches", "needs_review", "요건별 분석 결과가 비어 있습니다.")
        for match in matches:
            if match.status.value != "충족":
                add(f"t2_match:{match.requirement_id}", "needs_review",
                    f"{match.requirement_id}: {match.status.value}. 원본 증빙 및 요구 조건을 확인해야 합니다.")
                continue
            supported = bool(match.company_evidence)
            for evidence in match.company_evidence:
                record = records.get(evidence.record_id) if evidence.record_id else None
                supported = supported and bool(record and record.document == evidence.document
                    and record.section == evidence.section and record.excerpt == evidence.excerpt
                    and not record.verification_required and not evidence.verification_required)
                if record and record.type == "수행실적":
                    supported = supported and bool(record.buyer and record.period
                        and record.contract_amount_krw is not None and record.scope)
                    owner = str(raw_records.get(record.id, {}).get("performing_legal_entity") or "").strip()
                    supported = supported and bool(owner and owner in {profile.company_name, TARGET_LEGAL_ENTITY})
            add(f"t2_match:{match.requirement_id}", "passed" if supported else "needs_review",
                f"{match.requirement_id}: 충족 판단의 원본 record_id·문구·증빙 조건을 대조했습니다."
                if supported else f"{match.requirement_id}: 충족 판단의 원본 증빙 또는 개별 수행실적의 법인 귀속 정보가 부족하거나 불일치합니다.")
        if capability.human_review_items or capability.evidence_limitations:
            add("t2_limitations", "needs_review", "T2가 남긴 담당자 확인사항과 증빙 한계가 있습니다.")

    search_status = state.get("similar_projects_status") or "not_run"
    projects = state.get("similar_projects")
    if search_status == "none_above_threshold" and isinstance(projects, list) and not projects:
        # 검색은 정상 수행됐고 임계값 이상 후보가 없을 뿐이다 → 보고서에 참고로 남기되 보류 사유로 세지 않는다.
        add("t3_search", "info", "임계값 이상 유사사업 후보가 없습니다. 후보 없음은 유사사업 부재의 증거가 아닙니다.")
        projects = []
    elif not isinstance(projects, list) or not search_status.startswith("found "):
        add("t3_search", "needs_review", f"유사사업 검색 상태: {search_status}. 후보 없음은 유사사업 부재의 증거가 아닙니다.")
        projects = []
    elif not projects:
        add("t3_search", "needs_review", "검색 상태와 후보 목록이 일치하지 않습니다.")
    for index, item in enumerate(projects, start=1):
        prefix = f"t3_candidate:{index}"
        if not isinstance(item, dict):
            add(prefix, "needs_review", "후보 데이터 형식이 올바르지 않습니다.")
            continue
        notice_id = str(item.get("notice_id") or "").strip()
        url = str(item.get("notice_url") or "").strip()
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in {"https", "http"} and bool(parsed.hostname)
        except ValueError:
            valid_url = False
        path_text = str(item.get("rfp_path") or "").strip()
        root = PROJECT_ROOT.resolve()
        path = (root / path_text).resolve()
        in_project = not Path(path_text).is_absolute() and path.is_relative_to(root)
        source_ok = bool(notice_id and item.get("title") and valid_url and path_text and in_project and path.is_file())
        if source_ok:
            try:
                excerpt = str(item.get("profile_excerpt") or "").strip()
                source_ok = bool(excerpt and _source_profile(path, item.get("title") or "",
                    item.get("buyer") or "").startswith(excerpt))
            except Exception:  # 손상된 원문/추출 실패도 검토 필요로 남긴다.
                source_ok = False
        add(prefix + ":source", "passed" if source_ok else "needs_review",
            f"{notice_id}: 공고 식별자·URL 및 RFP 원문 발췌를 다시 대조했습니다. 유사성 판정은 담당자 검토 대상입니다."
            if source_ok else f"{notice_id or '식별자 없음'}: 공고 출처 또는 RFP 원문 발췌를 확인할 수 없습니다.")
        # 유사도는 모든 후보에 붙는 해석 주의사항이다. needs_review로 두면 후보가 하나만 있어도
        # T4가 항상 보류가 되므로 참고(info)로 기록한다. 출처·낙찰 연결 오류만 보류 사유로 센다.
        if item.get("same_project_suspected"):
            add(prefix + ":similarity", "info", "동일 사업·재공고 의심 후보는 별도 비교 실적으로 확정할 수 없습니다(비교 목록 제외).")
        else:
            add(prefix + ":similarity", "info", "임베딩 유사도는 사업 범위의 실질적 유사성 또는 실적 인정 근거가 아닙니다.")
        if item.get("award"):
            # 재공고 그룹의 다른 공고번호 낙찰정보는 이 후보의 낙찰로 쓰지 않는다(같은 공고번호의 차수만 인정).
            award_id = str(item.get("award_notice_id") or "")
            linked = bool(award_id and notice_id and award_id.split("-")[0] == notice_id.split("-")[0]
                          and str(item.get("award_source_url") or "").startswith(("https://", "http://")))
            add(prefix + ":award", "passed" if linked else "needs_review",
                "낙찰 공고·차수 식별자와 출처가 후보 공고와 일치합니다. 금액·수상자 원문은 담당자 확인 대상입니다."
                if linked else "낙찰정보의 공고번호·차수·출처 연결이 검증되지 않아 보고서에 확정 사실로 쓸 수 없습니다.")
        else:
            add(prefix + ":award", "passed", "연결된 낙찰정보가 없으며 낙찰자·금액을 주장하지 않습니다.")

    status = "needs_review" if any(x["status"] == "needs_review" for x in checks) else "passed"
    return {"agent23_evaluation": {"status": status, "checks": checks,
            "scope": "회사 자료와 T2 판단, T3 후보와 원문 식별·발췌의 연결 검사. 법적 자격·실적 인정·유사성의 최종 판정은 담당자 확인 필요."}}
