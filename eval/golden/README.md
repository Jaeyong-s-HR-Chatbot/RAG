# 임직원 골든셋

| 파일 | 문항 | 쓰는 단계 |
| --- | --- | --- |
| [`../intent/intent_cases.jsonl`](../intent/intent_cases.jsonl) | 178 | ① 의도 분류 |
| [`golden_set.json`](golden_set.json) | 279 | ② 검색 · ③ 리랭커 · 계산 · ④ 답변 생성 |
| [`rerank_cases.jsonl`](rerank_cases.jsonl) | 30 | ③ 리랭커 (되묻기 · 함정 조문) |
| [`../security/security_cases.jsonl`](../security/security_cases.jsonl) | 32 | 보안 |

## 본인 데이터 문항 (golden_set.json, needs_hr_data 33문항)

질문셋 페르소나와 맞춘 DB 직원(`DB/data/meta/golden_personas.json`)으로 평가해요.

| 페르소나 | DB 직원 | 요약 |
| --- | --- | --- |
| P1 | E1634 이승민 | 2024-02 입사, 5급, 부모님 피부양 (가족수당 4만) |
| P2 | E1033 최준서 | 1967-04생, 1996 입사, 3급, 아내·아들(24)·딸(19) (가족수당 3만), 정년 2027-06-30 |
| P3 | E1768 송은지 | 2026-04 입사, 5급, 어머니·남편 피부양 (가족수당 5만) |

- 각 문항에 `emp_id`(로그인할 사번)와 `expected_hr`(DB 기준일 2026-11-09 값)를 붙였어요.
- 정답 문장과 DB가 어긋나는 4문항에는 `hr_note`를 달았어요.
  - P2-6-03 · P1-6-06: 정답은 부여일수(25일·15일) 기준, DB상 남은 연차는 8일·9.5일
  - P2-8-05: 질문은 '29년' 가정, DB 직원은 정년까지 약 30년 11개월
  - P1-6-08: 정답은 '3년차라 이제 가능', DB 직원은 2024-02 입사라 권역 외 전보는 2027-02-01부터
- `scripts/eval_golden.py generate --mode e2e`는 `emp_id`가 있으면 그 사번으로 로그인해서 돌려요.

## 리랭커 문항 (rerank_cases.jsonl, 30문항 · dev 11 · test 19)

| kind | 문항 | 보는 것 | 정답 |
| --- | --- | --- | --- |
| `clarify` | 20 | "휴가 며칠이야?"처럼 여러 뜻이 가능한 질문 | `should_clarify: true` → 선택지(`options`)를 주며 되묻기 |
| `wrong_target` | 7 | 직원 질문에 임원 복무지침·임원 퇴직금·국외출장 규정이 섞이는지 | `must_not_chunks`가 상위 5개에 들어가면 실패 |
| `internal_first` | 3 | 사내 규정과 법이 둘 다 맞을 때 사내 규정이 위에 오는지 | `gold_chunks`가 `rank_below` 조문보다 높은 순위 |

지표
- 되묻기 재현율: clarify 20문항 중 되물은 비율 (높을수록 좋음)
- 되묻기 정밀도: golden_set.json의 일반 문항(test)에서 쓸데없이 되묻지 않은 비율
- 함정 조문 혼입률: wrong_target에서 `must_not_chunks`가 상위 5개에 들어간 비율 (0%가 목표)
- 사내 우선 정확도: internal_first에서 사내 조문이 법 조문보다 위에 온 비율

참고: 지금 코퍼스에는 같은 조문의 개정 전·후 버전이 따로 없어서 '최신 시행일 우선'은 평가할 수 없어요. 개정 이력이 적재되면 같은 방식(`must_not_chunks`)으로 추가하면 돼요.
