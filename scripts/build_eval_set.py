"""M25: build a labelled-threshold evaluation set for the three semantic search points.

Picks candidates *regardless of the current thresholds* (so labels can move a threshold up or down):
  t15      — T1.5 보조 검색: retrieve_more claim query → semantic top-2 in the RFP + top-1 in its 공고문
  company  — Agent 2: requirement → top-3 company records (allowed record types, 행정 조항 제외)
  project  — Agent 3: current RFP profile → top-5 past projects
Writes outputs/eval/eval_items.json (with scores) and the reviewer page
docs/final_project/implementation/M25_임계값_평가표.html (scores hidden).
"""
from __future__ import annotations

import json
import random
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

from bid_review.rag import past_projects as pp
from bid_review.rag.company_retriever import (
    administrative_clause, clean_requirement_query, company_record_metadata, company_semantic_scores,
)
from bid_review.rag.retriever import search_keyword
from bid_review.rag.semantic import search_semantic
from bid_review.settings import PROJECT_ROOT

CASES = {  # 기준선 LangGraph 결과 파일 → 공고번호
    "AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역": "R26BK01762350",
    "통합정보시스템 고도화 구축 용역": "R26BK01747679",
    "AI의료 기술 개발 고도화 관련 임상연구정보검색시스템(CDW) 구축": "R26BK01751619",
}
COMPANY_REQUIREMENTS_PER_CASE = 4
TEMPLATE = PROJECT_ROOT / "scripts/eval_page_template.html"


def _baseline(name: str) -> dict:
    for path in (PROJECT_ROOT / "outputs/langgraph_reviews").glob("*.json"):
        if unicodedata.normalize("NFC", path.stem) == name:
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(name)


SHORT = {"AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역": "상수도관",
         "통합정보시스템 고도화 구축 용역": "통합정보시스템",
         "AI의료 기술 개발 고도화 관련 임상연구정보검색시스템(CDW) 구축": "CDW"}
FIELD_LABELS = {"budget_krw": "사업예산(금액)", "estimated_price_krw": "추정가격(금액)", "g2b_crosscheck_estimated_price_krw": "추정가격 교차확인",
                "proposal_submission_start": "제안서 제출 시작일", "proposal_deadline": "제안서 제출 마감일",
                "bid_deadline": "입찰서 제출 마감일", "joint_venture_deadline": "공동수급협정서 제출 마감일",
                "contract_period": "계약(수행) 기간"}
RECORD_TYPE_LABELS = {"project_reference": "수행실적", "business_capability": "사업역량", "certification": "인증",
                      "financial_reference": "재무", "identity": "법인정보"}


def _rel(project_path: str) -> str:
    """Link from docs/final_project/implementation/ to a project file (URL-encoded)."""
    from urllib.parse import quote
    return "../../../" + quote(project_path)


def _profile_body(text: str) -> str:
    return re.sub(r"^(사업명|수요기관):.*$", "", text or "", flags=re.M)


def _clip(text: str, size: int = 420) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= size else text[:size] + "…"


def render_page(items: list[dict]) -> Path:
    """평가표 HTML만 다시 만든다(점수 제외). 평가 세트는 그대로."""
    public = [{k: v for k, v in item.items() if k != "score"} for item in items]  # 화면에는 점수를 넣지 않음
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*ITEMS*/[]", json.dumps(public, ensure_ascii=False).replace("</", "<\\/"))
    target = PROJECT_ROOT / "docs/final_project/implementation/M25_임계값_평가표.html"
    target.write_text(page, encoding="utf-8")
    return target


def main() -> None:
    import sys

    if "--page-only" in sys.argv:  # 임베딩 재계산 없이 저장된 평가 세트로 HTML만
        items = json.loads((PROJECT_ROOT / "outputs/eval/eval_items.json").read_text(encoding="utf-8"))
        print(render_page(items).relative_to(PROJECT_ROOT))
        return
    load_dotenv(PROJECT_ROOT / ".env")
    rng = random.Random(25)
    items: list[dict] = []
    records = company_record_metadata()
    for name, notice_no in CASES.items():
        state = _baseline(name)
        verification, rfp = state["verification"], state["rfp"]
        short = SHORT[name]
        notice = (state.get("g2b_notice_lookup") or {}).get("notice") or {}
        # t15: retrieve_more claims whose keyword search finds nothing (the only case semantic search runs)
        for category in verification["categories"].values():
            for claim in category["claims"]:
                if claim["status"] != "retrieve_more" or not claim["retrieval_queries"]:
                    continue
                query = claim["retrieval_queries"][0]
                if search_keyword(query, limit=3, document_id=verification["document_id"], match_mode="all"):
                    continue
                hits = search_semantic(query, limit=2, document_id=verification["document_id"]) + \
                    search_semantic(query, limit=1, notice_no=notice_no, document_type="notice_pdf")
                for hit in hits:
                    page, is_notice = hit.metadata.get("page"), hit.metadata["document_type"] == "notice_pdf"
                    items.append({"kind": "t15", "case": short, "score": hit.metadata["semantic_score"],
                                  "ctx_title": FIELD_LABELS.get(claim["field"], claim["field"]),
                                  "ctx_body": f"RFP에서 이 항목을 찾지 못해 \u201c{query}\u201d로 다시 검색했습니다.",
                                  "cand_title": f"{'공고문' if is_notice else 'RFP'} {page}쪽 · {hit.metadata.get('section') or ''}".strip(" ·"),
                                  "cand_body": _clip(hit.text, 600),
                                  "link": _rel(f"data/notice_attachments/{notice_no}-000/notice.pdf" if is_notice else f"data/input/{name}.pdf") + f"#page={page}",
                                  "link_label": "PDF 해당 쪽 열기"})
        # company: sample technical requirements, top-3 records of the allowed types
        requirements = [r for r in rfp["requirements"]
                        if not administrative_clause(r["id"], f"{r['category']} {r['name']} {r['details']}")]
        picked = rng.sample(requirements, min(COMPANY_REQUIREMENTS_PER_CASE, len(requirements)))
        scores, ids = company_semantic_scores([clean_requirement_query(f"{r['category']} {r['name']} {r['details']}") for r in picked])
        for row, requirement in enumerate(picked):
            for column in sorted(range(len(ids)), key=lambda c: -scores[row][c])[:3]:
                record = records[ids[column]]
                source = record.get("source") or {}
                items.append({"kind": "company", "case": short, "score": round(float(scores[row][column]), 4),
                              "ctx_title": f"{requirement['id']} · {_clip(clean_requirement_query(requirement['name']), 90)}",
                              "ctx_body": _clip(clean_requirement_query(requirement["details"]), 600),
                              "cand_title": f"{record.get('title')} ({RECORD_TYPE_LABELS.get(record.get('record_type'), record.get('record_type'))})",
                              "cand_body": _clip(record.get("excerpt") or "", 600),
                              "link": source.get("url") if str(source.get("url", "")).startswith("https://") else None,
                              "link_label": f"출처 열기 · {source.get('title') or ''}".strip(" ·")})
        # project: top-5 past projects without the threshold
        title, buyer = notice.get("bidNtceNm") or name, notice.get("dminsttNm") or ""
        profile = pp.project_profile(PROJECT_ROOT / f"data/input/{name}.pdf", title, buyer)
        saved, pp.MIN_PROJECT_SCORE = pp.MIN_PROJECT_SCORE, -1.0
        try:
            projects = pp.search_similar_projects(profile, limit=5, exclude_notice_no=notice_no)
        finally:
            pp.MIN_PROJECT_SCORE = saved
        for project in projects:
            award = project.get("award")
            amount = int(project["amount_krw"]) if str(project.get("amount_krw") or "").isdigit() else None
            facts = " · ".join(x for x in (project.get("buyer"), f"예산 {amount:,}원" if amount else "",
                                           f"낙찰 {award['bidwinnrNm']}" if award else "") if x)
            items.append({"kind": "project", "case": short, "score": project["similarity"],
                          "ctx_title": f"{title}" + (f" ({buyer})" if buyer else ""),
                          "ctx_body": _clip(_profile_body(profile), 600),
                          "cand_title": project["title"], "cand_meta": facts,
                          "cand_body": _clip(_profile_body(project["profile_excerpt"]), 600),
                          "link": project.get("notice_url"), "link_label": "나라장터 공고 열기"})
    rng.shuffle(items)  # 종류·점수 순서가 판단에 영향을 주지 않게
    for number, item in enumerate(items, start=1):
        item["id"] = f"E{number:03d}"
    out = PROJECT_ROOT / "outputs/eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_items.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    target = render_page(items)
    counts = {kind: sum(1 for item in items if item["kind"] == kind) for kind in ("t15", "company", "project")}
    print(json.dumps({"items": len(items), **counts, "page": str(target.relative_to(PROJECT_ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
