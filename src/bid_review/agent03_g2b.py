"""Nara Market award-information API client with secret-safe provenance."""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The award API rejects ranges longer than one month (resultCode 07).
MAX_WINDOW_DAYS = 31


class G2BApiError(RuntimeError):
    """A reportable G2B failure that never contains the service key."""


class G2BAwardClient:
    def __init__(self, settings: dict[str, Any], service_key: str | None = None, session: requests.Session | None = None):
        self.settings = settings
        self.service_key = service_key if service_key is not None else os.getenv("G2B_SERVICE_KEY", "")
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("allow_live_lookup") and self.service_key)

    def search_service_awards(self, project_name: str | None, buyer: str | None = None) -> tuple[list[dict[str, Any]], str]:
        """Search service bid-opening records by RFP title and buyer."""
        filters: dict[str, Any] = {}
        if project_name:
            filters["bidNtceNm"] = project_name[:200]
        if buyer:
            filters["dminsttNm"] = buyer[:100]
        return self._search(self.settings["service_operation"], filters)

    def search_company_awards(self, company_name: str) -> tuple[list[dict[str, Any]], str]:
        """Filter the collected group-award dataset by winner name.

        The award API ignores winner-name filters, so scripts/collect_g2b_awards.py downloads
        the full service-award list and keeps group awards; this reads that file.
        """
        path = Path(self.settings.get("company_awards_file", "data/market/itcen_group_awards_3y.json"))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise G2BApiError(f"회사 낙찰 데이터 파일이 없습니다: {path.name}. scripts/collect_g2b_awards.py로 수집하세요.")
        dataset = json.loads(path.read_text(encoding="utf-8"))
        records = [item for item in dataset.get("awards", []) if company_name in (item.get("bidwinnrNm") or "")]
        return records, dataset.get("meta", {}).get("source_url", "https://www.data.go.kr/data/15129397/openapi.do")

    def _search(self, operation: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        """Query newest-first in windows of at most one month (the API's range limit)."""
        if not self.enabled:
            raise G2BApiError("G2B_SERVICE_KEY가 없거나 config의 market.allow_live_lookup가 비활성화되어 있습니다.")
        limit = int(self.settings.get("max_results", 20))
        end = date.today()
        start = end - timedelta(days=int(self.settings.get("lookback_days", 30)))
        records: list[dict[str, Any]] = []
        source_url = ""
        # ponytail: one page per window; a window with more than max_results matches is truncated.
        while end >= start and len(records) < limit:
            window_start = max(start, end - timedelta(days=MAX_WINDOW_DAYS - 1))
            items, url = self._request(operation, filters, window_start, end, limit)
            records += items
            source_url = source_url or url
            end = window_start - timedelta(days=1)
        return records[:limit], source_url

    def _request(self, operation: str, filters: dict[str, Any], begin: date, end: date, rows: int) -> tuple[list[dict[str, Any]], str]:
        """Perform one source-auditable G2B request without persisting its key."""
        params: dict[str, Any] = {
            "serviceKey": self.service_key,
            "type": "json",
            "numOfRows": rows,
            "pageNo": 1,
            "inqryDiv": "1",
            "inqryBgnDt": begin.strftime("%Y%m%d0000"),
            "inqryEndDt": end.strftime("%Y%m%d2359"),
        }
        params.update(filters)
        endpoint = f"{self.settings['base_url'].rstrip('/')}/{operation}"
        # Keep an already URL-encoded data.go.kr key encoded once, while raw '+' and '/'
        # are safely encoded. The key itself never appears in artifacts or error messages.
        query = urlencode(params, doseq=True, safe="%")
        try:
            response = self.session.get(f"{endpoint}?{query}", timeout=float(self.settings.get("timeout_seconds", 20)))
        except requests.RequestException as exc:
            # Do not interpolate `exc`: requests includes the complete URL (and thus
            # the service key) in several exception messages.
            raise G2BApiError(f"나라장터 낙찰정보 API 네트워크 연결 실패({type(exc).__name__}). 네트워크·DNS 상태를 확인하세요.") from None
        source_url = f"{endpoint}?{urlencode({key: value for key, value in params.items() if key != 'serviceKey'}, doseq=True)}"
        if response.status_code >= 400:
            raise G2BApiError(f"나라장터 낙찰정보 API HTTP {response.status_code} 응답. 활용신청·파라미터·호출량을 확인하세요.")
        payload = self._decode(response)
        # Errors arrive as {"nkoneps.com.response.ResponseError": {"header": ...}} instead of {"response": ...}.
        envelope = payload.get("response") or next((v for k, v in payload.items() if k.endswith("ResponseError")), {})
        header = _dig(envelope, "header") or {}
        result_code = str(header.get("resultCode", "00" if "response" in payload else "99"))
        if result_code not in {"00", "0"}:
            message = str(header.get("resultMsg", "알 수 없는 API 오류"))
            raise G2BApiError(f"나라장터 낙찰정보 API 오류 {result_code}: {message}")
        items = _dig(envelope, "body", "items") or []
        if isinstance(items, dict):  # XML form: <items><item>...</item></items>
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            items = []
        return [item for item in items if isinstance(item, dict)], source_url

    @staticmethod
    def _decode(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except ValueError:
            pass
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise G2BApiError("나라장터 낙찰정보 API 응답을 JSON 또는 XML로 해석하지 못했습니다.") from exc
        return _xml_to_dict(root)


def _dig(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _xml_to_dict(element: ET.Element) -> dict[str, Any]:
    """Convert the small G2B XML envelope while preserving repeated item nodes."""
    def convert(node: ET.Element) -> Any:
        children = list(node)
        if not children:
            return (node.text or "").strip()
        grouped: dict[str, Any] = {}
        for child in children:
            value = convert(child)
            if child.tag in grouped:
                grouped[child.tag] = grouped[child.tag] if isinstance(grouped[child.tag], list) else [grouped[child.tag]]
                grouped[child.tag].append(value)
            else:
                grouped[child.tag] = value
        return grouped
    return {element.tag: convert(element)}


def compact_awards_json(project_name: str, buyer: str | None, settings: dict[str, Any]) -> str:
    """CrewAI tool adapter: returns key-free, source-linked evidence JSON."""
    records, source_url = G2BAwardClient(settings).search_service_awards(project_name, buyer)
    return json.dumps({"source_url": source_url, "records": records}, ensure_ascii=False)
