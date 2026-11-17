"""Internal business-scale screening, independent of legal eligibility."""
from .models import ProjectScaleReview, RFPAnalysisResult
from .money import format_krw


def review_project_scale(rfp: RFPAnalysisResult, minimum_krw: int) -> ProjectScaleReview:
    if isinstance(minimum_krw, bool) or not isinstance(minimum_krw, int) or minimum_krw < 0:
        raise ValueError("minimum_project_amount_krw는 0 이상의 정수여야 합니다.")
    field = rfp.basic_info.budget_krw
    amount = rfp.project_summary.budget_krw
    candidates = rfp.basic_info_candidates.budget_krw
    distinct = {c.value for c in candidates if isinstance(c.value, int) and c.invalid_reason is None}
    located = bool(field.source_page and field.source_excerpt and field.value == amount)
    if minimum_krw == 0:
        status, reason = "기준 미설정", "내부 최소 사업금액 기준이 설정되지 않았습니다."
    elif len(distinct) > 1:
        status, reason = "확인 필요", "사업금액 후보가 서로 다릅니다. 총액·연차별 금액·VAT 조건을 원문에서 확인하세요."
    elif amount is None or not located:
        status, reason = "확인 필요", "원문 페이지와 금액 근거가 없어 내부 규모 기준을 판단하지 않았습니다."
    elif amount < minimum_krw:
        status = "기준 미달"
        reason = f"사업금액 {format_krw(amount)}은 내부 최소 기준 {format_krw(minimum_krw)} 미만입니다. 원칙적 No-Go 검토 대상이며 전략적 예외는 사업본부장이 재검토합니다. 법적 참가자격 미충족을 뜻하지 않습니다."
    else:
        status = "기준 충족"
        reason = f"사업금액 {format_krw(amount)}은 내부 최소 기준 {format_krw(minimum_krw)} 이상입니다. 규모 기준만 충족하며 법적 자격·기술·인력 판단은 별도입니다."
    return ProjectScaleReview(amount_krw=amount, minimum_krw=minimum_krw, status=status,
                              source_document=field.source_document, source_page=field.source_page,
                              source_excerpt=field.source_excerpt, rationale=reason)
