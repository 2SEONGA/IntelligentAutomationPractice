# 보완 2 — 담당자 검토 후 중단·재개

작성자: 이주안  
작성일: 2026-11-19  
상태: **구현 및 제공 측 테스트 완료 — 사용자 환경 적용·실제 PDF 검증 예정**

## 요약

- 목적: 사람 검토에서 종료되던 실행을 저장하고, 승인·수정·반려 후 해당 실행을 이어간다.
- 구현: SQLite Checkpointer, `interrupt()`, 동일 thread의 `Command(resume=...)`, 검토 이력 및 입력 파일 해시 관리.
- 제공 측 검증: 새 테스트 20개와 보완 1 테스트 35개, 총 55개 통과. 실제 SQLite와 별도 프로세스 종료 후 재개를 확인했다.
- 범위: PDF 추출·API·회사 매칭·검색은 테스트 대역을 사용했다. 실제 Workflow의 승인 후 T4 도달도 고정 응답으로 검사했다. 실제 문서 판정 정확도·API 응답 품질을 확인한 결과는 아니다.

## 수정 전 동작

`human_review`에서 확인 필요 상태를 기록하고 종료했다. 기존 실행 함수에 승인 정보를 전달해 다시 호출하면 T1부터 분석을 다시 시작했다.

## 변경 내용

### 실행 상태 저장

`create_bid_review_graph(checkpointer=None)`로 확장했다. 체크포인터를 전달한 실행에서는 사람 검토 Node가 `interrupt()`로 일시 중지한다. 전달하지 않은 기존 실행 방식은 종료형 동작을 유지한다.

- SQLite: `outputs/langgraph_checkpoints/reviews.sqlite3`
- 실행 식별: `thread_id` 및 State의 `run_id`
- 재개 시 해당 중단 ID를 검사해 오래된 응답·다른 실행의 응답을 거부
- 입력 RFP·공고문·회사 자료의 경로와 SHA-256을 저장하고 재개 전 변경 여부 확인

### 검토 결과에 따른 경로

| 응답 | 경로와 처리 |
|---|---|
| approve | 검토자·사유를 기록하고 T2부터 진행. 미확인 사항과 최종 결정은 자동 확정하지 않음 |
| edit | 기본정보 수정과 전후 값·근거를 기록하고 T1.5 재검증. 이전 후속 분석·보고서 결과는 비움 |
| reject | 반려 사유를 기록하고 종료. 후속 분석·보고서 생성은 실행하지 않음 |

승인·수정·반려의 감사 기록은 `review_history`에 보존한다. `interrupt()` 이후에 기록하여 Node 재실행 시 같은 기록이 중복되지 않게 했다.

수정 가능 필드는 `budget_krw`, `estimated_price_krw`, `contract_period`, `proposal_deadline`, `bid_deadline`, `proposal_submission_start`, `joint_venture_deadline`, `joint_venture_policy`이다. 원문 페이지·발췌·문서 종류가 필요하며, 수정만으로 검증을 통과시키지 않는다.

### 재개 동작의 의미

완료된 T1을 다시 실행하지 않고 저장된 검토 지점에서 이어간다. LangGraph 특성상 **중단된 human_review Node 자체는 처음부터 다시 실행**된다. 해당 Node의 중단 앞에는 외부 호출이나 이력 쓰기를 두지 않았다.

## 변경 파일

| 파일 | 변경 |
|---|---|
| `src/bid_review/rag/workflow.py` | 선택적 Checkpointer, 새 State, 사람 검토 후 조건부 연결 |
| `src/bid_review/rag/hitl_review.py` | 검토 요청 중단·응답 검사·수정 반영·감사 이력 |
| `src/bid_review/rag/hitl_runtime.py` | SQLite 실행 시작·조회·재개·식별자 및 입력 변경 검사 |
| `scripts/run_hitl_review.py` | start/status/resume CLI와 응답 템플릿 저장 |
| `pyproject.toml` | `langgraph-checkpoint-sqlite==3.1.3` 추가 |
| `tests/test_hitl.py` | 중단·프로세스 재시작·승인·수정·반려·격리·T4 연결 검사 |

## 실행 방법

```bash
python tests/test_hitl.py
python scripts/run_hitl_review.py --help
```

새 실제 실행은 `start`, 저장 상태 조회는 `status`, 작성한 응답 파일을 이용한 재개는 `resume --response`를 사용한다. 상세 명령과 응답 작성법은 제공 묶음의 `hitl_update/README.md`에 있다.

## 검증 결과

| 항목 | 제공 측 결과 |
|---|---|
| 새 HITL 테스트 | 20개 통과 |
| 보완 1 회귀 테스트 | 35개 통과 |
| 별도 프로세스에서 시작한 실행의 재개 | T1 반복 없이 완료 |
| 원래 Workflow의 사람 검토 → 승인 → T4 | 외부 단계 고정 응답을 사용해 확인 |
| 패치 적용 | 문법·구조 확인·백업·재적용 시 변경 없음 확인 |
| 코드 구조 불일치 | 적용 전 중단 확인 |
| 사용자 컴퓨터 테스트 | 적용 후 기록 예정 |
| 실제 RFP의 중단·재개 | 2026-11-19 상수도관 RFP로 중단 → 승인 → T4 완료 (`회의_개선점/261118/보완사항_점검결과.md` 3.7) |

## 한계 및 다음 단계

- 로컬 단일 작업자용이며 macOS/Linux 파일 잠금을 사용한다. Windows·웹 UI·검토자 인증은 이번 범위가 아니다.
- 승인자는 이름과 사유로 기록되지만 본인 인증·권한 검증은 구현하지 않았다.
- 기본정보 필드 수정만 지원한다. 전체 참가자격·요구사항 목록을 편집하는 기능은 별도 설계가 필요하다.
- 실제 자료의 불일치·누락을 해소하지 않고 후속 분석 진행을 승인해서는 안 된다. 진행 승인은 최종 Go 결정과 다르다.
- T1·T1.5 내용 선택 정확도와 T2·T3 결과 Evaluation은 관련 보완 작업에서 추가 확인한다.

## 공식 참고 자료

- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [SqliteSaver.from_conn_string](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver/from_conn_string)
