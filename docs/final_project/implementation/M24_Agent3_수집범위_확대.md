# M24 Agent 3 과거 사업 수집 범위 확대

작성일: 2026-11-17  
상태: 코드·설정 완료, 테스트 통과. **라이브 수집은 미실행** — 나라장터 첨부파일정보 API 일일 할당량이 소진됨(오류 22/HTTP 429). 할당량이 초기화된 뒤 실행 필요.

## 요약

- **한 일:** 범위 확대 수집을 시작하다가 첨부 API 일일 할당량 초과를 발견했다. M23 수집기가 이 오류를 "PDF 제안요청서 없음"으로 세고 있었다. 수집기를 고쳤다(할당량 오류 시 중단하고 다음 날 이어받기, 확인한 공고 캐시, 오류 유형별 집계). 범위는 설정 파일로 옮겨 넓혔다(키워드 5→15개, 저장 80→200건, 기간 전체를 주 단위로 고르게).
- **결과:** 가짜 API 테스트로 할당량 중단, 이어받기(재조회 0건), 주 단위 배분을 확인했다. 테스트 16개 파일 통과. M23 문서의 "HWP뿐인 814건" 설명을 정정했다.
- **남은 일:** 할당량이 초기화된 뒤 `scripts/build_past_projects.py`를 실행한다. 호출 상한이 800회라 하루에 다 받지 못하면 다음 날 다시 실행하면 이어진다. 수집 후 세 공고의 유사 사업 결과(특히 상수도관)를 다시 확인해야 한다.

## 발견한 문제

- 범위 확대 전 표본 조사에서 `getBidPblancListInfoAtchFileInfo02`가 "오류 22: 일일 서비스 요청제한 횟수 초과"를 반환했다. `G2B_BID_NOTICE_KEY`는 HTTP 429였고, `G2B_SERVICE_KEY`는 이 API에 등록돼 있지 않았다(403).
- M23 수집 당시 첨부 API를 약 900회 호출했다. 수집기는 `NoticeError`를 모두 `skipped`(PDF 없음)로 셌으므로, M23의 "814건 건너뜀"에는 할당량 오류가 섞여 있었다. M23 문서에 정정을 남겼다.
- M20 승인 기록 누락, M23 낙찰 0건에 이어, **실패를 빈 결과로 처리한** 세 번째 사례다.

## 변경 내용

- `config/settings.yaml` → `past_projects`: `days: 180`, `max_projects: 200`, `max_lookups_per_run: 800`, `extra_keywords`(상수도·상수관·관망·GIS·공간정보·임상·의료정보·병원·학사·대학). 공고 수집 키워드 5개와 합쳐 15개다.
- `src/bid_review/rag/past_projects.py` `collect_past_projects()`
  - **할당량 오류(22/LIMITED_NUMBER)면 즉시 중단**하고 `stopped: api_quota_exhausted`를 반환한다. 다른 API 오류는 `api_error`로 따로 센다.
  - **이어받기:** 저장된 공고(`meta.json`)와 PDF 제안요청서가 없던 공고(`data/past_rfps/_checked.json`)는 API를 다시 호출하지 않는다. API 오류가 난 공고는 다음 실행에서 다시 시도한다.
  - **호출 상한:** 1회 실행의 첨부 API 호출을 800회로 제한해, 같은 날 웹앱 단건 조회가 쓸 할당량을 남긴다.
  - **주 단위 배분(`_spread_by_week`):** 최근 공고부터 채우지 않고, 최신 주부터 주마다 한 건씩 돌아가며 고른다. 상한에 걸려도 기간 전체를 덮는다.
  - 결과 집계: `saved`, `already_saved`, `no_rfp_pdf`, `already_checked`, `api_error`, `download_failed`, `attachment_lookups`, `stopped`
- `scripts/build_past_projects.py`: 기본값을 설정 파일에서 읽는다(`--days`, `--max`로 덮어쓰기 가능).
- `tests/test_semantic.py`: 할당량 오류 시 중단(api_error·no_pdf로 세지 않음), 두 번째 실행에서 이전 확인분 재조회 0건, 주 단위 순서를 검증한다.

## 실행 (할당량 초기화 후)

```bash
.venv/bin/python scripts/build_past_projects.py      # 수집(이어받기) + 색인
# 출력의 "stopped"가 api_quota_exhausted 또는 lookup_cap이면 다음 날 같은 명령을 다시 실행
```
