"""질문 하나 → ① 쪼개기 → ② 분류 → ③ 라우터 → ④ 검색 → LlmRequest.

LLM 호출과 HR DB 조회는 여기서 하지 않는다. hr_needed=True 면 API 쪽에서 HrContext 를 채워 넣으면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classify import Classifier, QueryClass
from .embed import make_embedder
from .goldenset import ChunkHit, LlmRequest, Route, chunk_hit_from_row, route
from .retrieve import Retriever, SearchResult
from .split import split_question

# 라우트 이름
SEARCH, SEARCH_HR, CLARIFY, OUT, CONTACT, TEAM = "검색", "검색+HR", "되묻기", "범위밖", "담당자연결", "권한확인"


@dataclass
class SubResult:
    question: str
    query_class: QueryClass
    route: str
    search: SearchResult | None


@dataclass
class PipelineResult:
    question: str
    status: str                        # 검색 / 검색+HR / 되묻기 / 범위밖 / 담당자연결 / 권한확인
    message: str | None
    hr_needed: bool
    team_scope: bool
    hr_route: Route
    subs: list[SubResult]
    chunks: list[ChunkHit] = field(default_factory=list)
    llm_request: LlmRequest | None = None


def _sub_route(qc: QueryClass, hr: Route) -> str:
    if not qc.in_scope:
        return OUT
    if qc.sensitive:
        return CONTACT
    if hr.team_scope:
        return TEAM
    if qc.ambiguous:
        return CLARIFY
    return SEARCH_HR if hr.hr else SEARCH


class Pipeline:
    def __init__(self, cfg, embedder=None, _retriever=None, _classifier=None):
        self.cfg = cfg
        self.embedder = embedder or make_embedder(cfg)
        self.retriever = _retriever or Retriever(cfg, self.embedder)
        self.classifier = _classifier or Classifier(self.embedder, cfg)

    def with_config(self, cfg) -> "Pipeline":
        """모델·색인은 그대로, 스위치(검색 방식·필터)만 바꾼 파이프라인."""
        return Pipeline(cfg, self.embedder, self.retriever.with_config(cfg), self.classifier.with_config(cfg))

    # ------------------------------------------------------------
    def run(self, question: str) -> PipelineResult:
        question = question.strip()
        hr = route(question)
        subs: list[SubResult] = []
        parts = split_question(question)
        vecs = self.embedder.encode(parts, is_query=True)
        for q, v in zip(parts, vecs):
            qc = self.classifier(q, v)
            rt = _sub_route(qc, hr)
            sr = self.retriever.search(q, qc, v) if rt not in (OUT,) else None
            subs.append(SubResult(q, qc, rt, sr))

        routes = [s.route for s in subs]
        message = None
        if all(r == OUT for r in routes):
            status, message = OUT, "인사·복리후생 규정에 관한 질문만 답할 수 있어요."
        elif CONTACT in routes:
            status, message = CONTACT, "민감한 사안이라 담당 부서(인사팀·고충상담 창구) 연결을 안내해요. 관련 규정은 같이 찾아 둬요."
        elif TEAM in routes:
            status, message = TEAM, "팀·부서 단위 데이터는 다른 사람 정보라 권한 확인이 필요해요 (팀장·인사팀만 조회)."
        elif len(subs) == 1 and routes[0] == CLARIFY:
            opts = subs[0].query_class.clarify_options
            status, message = CLARIFY, "어떤 것을 말씀하시는 건가요? " + " / ".join(opts)
        else:
            status = SEARCH_HR if hr.hr else SEARCH
            if hr.hr:
                message = f"HR 조회 필요 ({hr.reason})"

        res = PipelineResult(question, status, message, hr.hr, hr.team_scope, hr, subs)
        if status in (OUT, CLARIFY):
            return res
        res.chunks = self._merge([s.search for s in subs if s.search])
        res.llm_request = LlmRequest(question=question, chunks=res.chunks)
        return res

    def _merge(self, results: list[SearchResult]) -> list[ChunkHit]:
        """하위 질문들의 후보를 번갈아 가며 합친다 (각 질문의 1등부터). 최대 top_k 개."""
        picked: list = []
        seen: set[str] = set()
        depth = max((len(r.candidates) for r in results), default=0)
        for d in range(depth):
            for r in results:
                if d < len(r.candidates) and r.candidates[d].chunk_id not in seen:
                    seen.add(r.candidates[d].chunk_id)
                    picked.append(r.candidates[d])
        picked = picked[: self.cfg.top_k]
        rows = self.retriever.rows
        return [
            chunk_hit_from_row(rows[c.chunk_id], score=max(-1.0, min(1.0, c.cos)), rank=i)
            for i, c in enumerate(picked, 1)
        ]
