# 퇴직연금 골든셋 20문항 (2026-11-19)

팀 골든셋(`eval/golden/golden_set.json`, 버전 2026-11-18)과 **같은 형식**이에요. 필드·페르소나·조각 ID를 그대로 쓰고, 퇴직연금 문항만 추가했어요. 퇴직연금 범위는 팀 확정 전이라 별도 파일로 둡니다.

| 파일 | 내용 |
| --- | --- |
| `pension_golden_set.json` | 20문항 (골든셋 `golden_set.json`과 같은 스키마 + `domain`·`kind`·`split`, 항 단위 정답 칸 `gold_articles`·`gold_hang` 포함) |
| `pension_golden_set.csv` | 같은 내용, 엑셀용 (`employee_golden_set.csv`와 같은 열 + domain·kind) |
| `pension_security_cases.jsonl` | 타인 조회 거절 1문항 (`eval/security` 형식) |

생성 스크립트(`build_pension_golden.py`)와 가상 퇴직연금 데이터(`pension` 스키마)는 작성자의 로컬 HR DB 작업 폴더(`hr_db`)에 있고, DB 저장소에는 아직 올리지 않았어요. 정답 숫자(적립금·부담금·퇴직금 등)는 그 DB에서 읽어 넣은 값이에요.

## 구성
| kind | 문항 | 근거 |
| --- | --- | --- |
| A 규정 | 5 | 퇴직급여 지급 규정(r09)·시행세칙(r10)·시행령(L06) 조각 |
| B 본인 조회 | 5 | `pension.pension_plans`·`pension_holdings`·`pension_contributions` |
| C 계산·상품 | 5 | 퇴직금·수익률·수수료 계산, 위험등급 (계산 정답 `calculation`) |
| D 거절 | 5 | 투자 추천·수익 확정·타인 조회 거절, 틀린 전제 2(중간정산, 생활비 인출) |

페르소나는 팀 골든셋과 같아요: P1 E1634 이승민(DC, 태용생명) 8문항 · P2 E1033 최준서(DB, 혜림증권) 6문항 · P3 E1768 송은지(DC, 혜림증권) 6문항. 유형은 일반 14 · 틀린 전제 2 · 규정 없음 4, `uncertain` 3문항(PN-P2-05 퇴직금 추정, PN-P3-03 1년 미만 DC, PN-P3-04 수수료)은 `split=uncertain`.

## 항 단위 정답
`gold_chunks`는 조문 단위 조각 id(DB `rag.chunks_article`), `gold_hang`은 항 단위 조각 id(DB `rag.chunks`, 항 단위 청킹)예요. 정답 조각이 있는 13문항 모두 `feat/golden-hang-labels`의 `add_hang_gold.py` 방식으로 자동 매칭했고(확인 필요 0건), 모든 id가 DB 저장소 `data/regs`·`data/hang` 조각 파일에 있는 것을 확인했어요.

## 주의
- 정답의 조각 ID는 모두 내 DB와 GitHub DB 저장소 조각 파일에 있어요(검사 통과).
- 기존 골든셋 `P2-8-09`("DB형이에요 DC형이에요?")는 정답이 "본인 유형은 급여 담당 확인"이었는데, `pension_plans`가 있으면 본인 조회로 답할 수 있어서 `PN-P1-03`·`PN-P2-03`에 본인 조회 버전을 뒀어요.
- 규정은 평균임금을 "보수규정에 의하여 산정"이라고만 해서(제3조 1호), 퇴직금 계산(PN-P2-05)은 근로기준법 3개월 방식으로 가정한 추정이에요.
- 근로자퇴직급여 보장법 본문·소득세법은 rag에 없어요. DC 위험자산 70% 한도 같은 문항은 그 근거가 들어온 뒤에 추가해요.
- 수수료·금리·적립금은 시연용 가상 데이터(`data/pension/README_퇴직연금_데이터.md`).
