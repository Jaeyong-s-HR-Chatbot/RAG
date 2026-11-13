"""② 질문 분류 (BGE-M3 프로토타입 + 규칙, LLM 안 씀).

- 업무 영역(멀티라벨): 카테고리마다 설명·예시 질문을 임베딩해 평균(프로토타입)을 만들고 코사인으로 비교.
  키워드가 맞으면 점수를 더한다. 1등과 proto_margin 안쪽이면 같이 붙인다.
- slots: 이벤트·대상·지역·숫자 같은 조건 (정규식)
- 범위 밖 / 애매함(되묻기) / 민감(담당자 연결) 표시
카테고리 이름은 DB 저장소 rules/regulations.yaml 과 같다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

# 카테고리 → (키워드, 예시 질문). 키워드는 DB rules/regulations.yaml category_rules 와 같게 유지.
CATEGORIES: dict[str, tuple[list[str], list[str]]] = {
    "근태관리": (
        ["휴가", "연차", "반차", "근무시간", "휴일", "휴게", "병가", "지각", "조퇴", "당직", "유연근무", "시차출퇴근", "교대근무", "출근", "퇴근"],
        ["연차휴가는 며칠 나와요?", "병가는 1년에 며칠까지 쓸 수 있어?", "반차 쓰면 연차에서 얼마나 빠져요?",
         "출근 시간을 늦추는 유연근무 신청할 수 있어?", "지각하면 어떻게 처리돼요?"],
    ),
    "수당": (
        ["수당", "보수", "봉급", "급여", "월급", "상여", "임금", "성과연봉", "통상임금", "야근", "시간외", "야간", "가족수당"],
        ["시간외근무수당은 어떻게 계산해?", "월급날이 언제야?", "가족수당은 얼마 나와요?",
         "야간근무하면 수당이 얼마예요?", "성과연봉은 언제 지급돼요?"],
    ),
    "교육": (
        ["교육", "훈련", "연수", "학점", "수료", "강사", "법정교육", "예방교육", "대학원"],
        ["교육 수료 기준 점수가 몇 점이야?", "법정 의무교육은 꼭 들어야 해?", "사내 강사로 나가면 강사료 나와요?",
         "국외훈련 지원 자격이 어떻게 돼?", "안전교육은 몇 시간 받아야 해?"],
    ),
    "출장": (
        ["출장", "여비", "일비", "숙박비", "식비", "교통비", "국외여비", "운임"],
        ["서울 출장 가면 숙박비 얼마까지 나와?", "출장 일비는 직급별로 얼마예요?", "국외출장 여비 기준이 뭐야?",
         "1박 2일 출장 가면 얼마 받아요?", "출장 교통비는 실비로 줘요?"],
    ),
    "기타 복리후생": (
        ["복지", "포인트", "복지포인트", "학자금", "대부", "생활안정", "주택", "의료비", "건강검진", "동호회", "휴양", "콘도"],
        ["복지포인트는 몇 점 주나요?", "자녀 학자금 지원 받을 수 있어?", "생활안정자금 대부 한도가 얼마야?",
         "건강검진은 회사에서 해줘요?", "휴양소 이용 신청은 어떻게 해?"],
    ),
    "생애이벤트-결혼": (
        ["결혼", "혼인", "청원휴가", "경조", "경조금", "경조사", "사망", "조사", "장례", "부고"],
        ["결혼하면 휴가 며칠 받아요?", "결혼 경조금 얼마 나와?", "부모님 돌아가시면 경조휴가 며칠이야?",
         "자녀 결혼하면 휴가 있어요?", "경조사 휴가는 언제까지 써야 해?"],
    ),
    "생애이벤트-출산": (
        ["출산", "육아", "임신", "난임", "태아", "유산", "사산", "입양", "자녀돌봄", "배우자출산", "모성"],
        ["배우자가 출산하면 휴가 있어?", "출산전후휴가는 며칠이야?", "임신 중에 근무시간 줄일 수 있어?",
         "난임치료 휴가 있어요?", "육아기 근로시간 단축 신청할 수 있어?"],
    ),
    "생애이벤트-휴직·복직·퇴직": (
        ["휴직", "복직", "퇴직", "퇴직금", "정년", "명예퇴직", "임금피크", "임피", "육아휴직", "사직"],
        ["육아휴직은 몇 년까지 가능해?", "퇴직금은 어떻게 계산해?", "정년이 몇 살이에요?",
         "임금피크제 들어가면 월급이 얼마나 줄어?", "휴직 끝나고 복직 신청은 언제 해?"],
    ),
    "생애이벤트-입사·승진": (
        ["승진", "승급", "근무성적", "평정", "승격", "채용", "신규임용", "수습", "임용", "입사", "시험승진"],
        ["승진 평정 점수 비율이 어떻게 돼?", "수습 기간은 얼마나 돼?", "4급 시험승진 응시 자격이 뭐야?",
         "승진 최소 소요연수가 몇 년이야?", "신규 채용 절차가 어떻게 돼?"],
    ),
}
COMMON = "공통"

# 범위 밖: HR·규정과 무관한 주제
OUT_OF_SCOPE = re.compile(
    r"(주식|코인|비트코인|부동산 투자|로또|날씨|맛집|점심 메뉴|연애|게임|축구|야구|노래 추천|영화 추천|여행지 추천|주가|환율|선거|정치)"
)
# 민감: 담당자 연결이 맞는 주제 (검색은 하되 표시)
SENSITIVE = re.compile(r"(성희롱|성폭력|괴롭힘|갑질|징계|신고|고충|폭행|자살|우울)")

# 애매함: 이 일반 명사만 있고 무엇인지 안 정해지면 되묻기
AMBIGUOUS: dict[str, list[str]] = {
    "휴가": ["연차휴가", "경조휴가(결혼·사망)", "병가", "출산전후휴가·배우자출산휴가"],
    "수당": ["시간외·야간·휴일근무수당", "가족수당", "연차휴가 미사용수당", "당직비"],
    "휴직": ["육아휴직", "질병휴직", "가족돌봄휴직"],
    "교육": ["법정 의무교육", "직무교육·집합교육", "국외훈련"],
}
_QUALIFIER = re.compile(
    r"(연차|경조|결혼|출산|병가|배우자|육아|청원|포상|공가|특별|가족|시간외|야간|휴일|연장|미사용|당직|질병|돌봄|"
    r"법정|안전|직무|집합|국외|성희롱|예방|사망|임신|난임|입양|여름|하계|장기)"
)

# slots
_EVENT = re.compile(r"(결혼|혼인|출산|사망|돌아가|장례|입양|임신|유산|입사|승진|퇴직|정년|휴직|복직|이사|전보)")
_RELATION = re.compile(r"(본인|배우자|아내|남편|와이프|자녀|아들|딸|부모|아버지|어머니|엄마|아빠|조부모|할머니|할아버지|형제|자매|장인|장모|시부모)")
_REGION = re.compile(r"(서울|부산|대구|인천|광주|대전|울산|세종|수도권|광역시|충청|영남|호남|강원|제주|국외|해외)")
_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(일|박|시간|년|개월|만원|원|급|세|살|회|명)")
_GRADE = re.compile(r"([1-6])\s*급")


@dataclass
class QueryClass:
    categories: list[tuple[str, float]]          # 붙인 카테고리 (멀티라벨, 점수 순)
    scores: dict[str, float]                     # 전체 카테고리 점수
    confident: bool                              # 하드 필터를 걸어도 될 만큼 확실한가
    slots: dict[str, list[str]] = field(default_factory=dict)
    in_scope: bool = True
    ambiguous: bool = False
    clarify_options: list[str] = field(default_factory=list)
    sensitive: bool = False
    keyword_hits: dict[str, list[str]] = field(default_factory=dict)

    @property
    def category_names(self) -> list[str]:
        return [c for c, _ in self.categories]


def extract_slots(q: str) -> dict[str, list[str]]:
    slots = {
        "event": _EVENT.findall(q),
        "relation": _RELATION.findall(q),
        "region": _REGION.findall(q),
        "number": ["".join(m) for m in _NUMBER.findall(q)],
        "grade": [g + "급" for g in _GRADE.findall(q)],
    }
    return {k: list(dict.fromkeys(v)) for k, v in slots.items() if v}


class Classifier:
    def __init__(self, embedder, cfg):
        self.cfg = cfg
        self.embedder = embedder
        self.names = list(CATEGORIES)
        protos = []
        for name, (kws, examples) in CATEGORIES.items():
            texts = [f"{name}: {', '.join(kws)}"] + examples
            v = embedder.encode(texts, is_query=True).mean(axis=0)
            protos.append(v / (np.linalg.norm(v) + 1e-9))
        self.protos = np.stack(protos)

    def with_config(self, cfg) -> "Classifier":
        new = object.__new__(Classifier)
        new.__dict__.update(self.__dict__, cfg=cfg)
        return new

    def __call__(self, q: str, qvec: np.ndarray | None = None) -> QueryClass:
        cfg = self.cfg
        if qvec is None:
            qvec = self.embedder.encode([q], is_query=True)[0]
        cos = self.protos @ qvec
        hits = {n: [k for k in CATEGORIES[n][0] if k in q] for n in self.names}
        hits = {n: v for n, v in hits.items() if v}
        scores = {n: float(c) + (0.08 if n in hits else 0.0) for n, c in zip(self.names, cos)}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top, top_s = ranked[0]
        second_s = ranked[1][1]
        cats = [(n, s) for n, s in ranked if s >= top_s - cfg.proto_margin]
        cats += [(n, scores[n]) for n in hits if n not in dict(cats)]   # 키워드가 맞은 카테고리는 항상 붙임

        in_scope = not OUT_OF_SCOPE.search(q) and (top_s >= cfg.in_scope or bool(hits))
        confident = in_scope and (
            (top_s >= cfg.high_conf and top_s - second_s >= cfg.conf_gap) or (len(hits) == 1 and top in hits)
        )

        # 애매함: '휴가 며칠이야?'처럼 짧고, 일반 명사만 있고 종류를 가르는 말이 없음
        ambiguous, options = False, []
        short = len(re.sub(r"[\s?!.,]", "", q)) <= 10
        for word, opts in AMBIGUOUS.items():
            if short and word in q and not _QUALIFIER.search(q) and not extract_slots(q).get("event"):
                ambiguous, options = True, opts
                break
        if ambiguous:
            confident = False

        return QueryClass(
            categories=cats, scores=scores, confident=confident, slots=extract_slots(q),
            in_scope=in_scope, ambiguous=ambiguous and in_scope, clarify_options=options,
            sensitive=bool(SENSITIVE.search(q)), keyword_hits=hits,
        )
