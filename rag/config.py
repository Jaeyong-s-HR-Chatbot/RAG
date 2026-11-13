"""설정. 모델·색인은 그대로 두고 스위치(검색 방식·필터)만 바꿔 실험한다.

경로는 환경변수로 바꿀 수 있다.
- DB_DIR         : DB 저장소 (data/regs/chunks.jsonl, data/laws/chunks.jsonl 이 있는 곳)
- GOLDENSET_DIR  : goldenset 저장소 (contracts/, router/)
- RAG_CACHE_DIR  : 임베딩 캐시 저장 위치
- RAG_EMBEDDER   : "bge-m3"(기본) / "mock"(모델 없이 코드 흐름만 점검)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # 이 저장소(RAG) 루트


def _first_dir(*cands: str | Path | None) -> Path:
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c).resolve()
    return Path(next(c for c in cands if c))   # 없어도 일단 첫 후보 (나중에 친절한 오류)


# 실험 구성. 위에서부터 하나씩 더해진다.
PRESETS: dict[str, dict] = {
    "A0": dict(use_dense=True, use_bm25=False, filter_mode="none"),       # Dense만
    "A1": dict(use_dense=True, use_bm25=True, filter_mode="none"),        # + BM25, RRF로 합치기
    "B":  dict(use_dense=True, use_bm25=True, filter_mode="hard"),        # + 분류 결과로 하드 필터
    "C":  dict(use_dense=True, use_bm25=True, filter_mode="adaptive"),    # + confidence 따라 하드/소프트, 약하면 해제 (최종)
}


@dataclass(frozen=True)
class Config:
    # 경로
    db_dir: Path
    goldenset_dir: Path
    cache_dir: Path
    data_subdirs: tuple[str, ...] = ("data/regs", "data/laws")

    # 모델
    embedder: str = "bge-m3"          # "bge-m3" / "mock"
    model_name: str = "BAAI/bge-m3"
    max_seq_len: int = 1024           # 메모리 부족하면 512
    batch_size: int = 16

    # 검색 스위치 (preset 으로 바꿈)
    preset_name: str = "C"
    use_dense: bool = True
    use_bm25: bool = True
    filter_mode: str = "adaptive"     # none / hard / adaptive

    # 검색 숫자
    n_candidates: int = 50            # Dense·BM25 각각 뽑는 후보 수
    rrf_k: int = 60
    pool: int = 20                    # 화면에 보여줄 후보 수 (검색 후보 최대 20개)
    top_k: int = 8                    # LLM에 넘길 조각 수 (LlmRequest 최대 8)
    min_filtered: int = 3             # 하드 필터 뒤 후보가 이보다 적으면 필터 해제
    soft_boost: float = 0.3           # 소프트 필터: 카테고리 맞는 조각 RRF 점수 가산 비율
    reg_boost: float = 0.1            # 내부규정 우선 (법령이 규정을 밀어내지 않게)
    exclude_docs: tuple[str, ...] = ("r18",)   # 임원 복무 지침: 직원 답변에 섞이면 안 됨

    # 분류 임계값 (bge-m3 코사인 기준)
    proto_margin: float = 0.03        # 1등 카테고리와 이만큼 가까우면 같이 붙임 (멀티라벨)
    high_conf: float = 0.50           # 이 이상 + 1·2등 차이가 conf_gap 이상이면 confident
    conf_gap: float = 0.04
    in_scope: float = 0.35            # 1등 카테고리 점수가 이보다 낮으면 범위 밖 후보
    fallback_cos: float = 0.40        # 필터 검색 1등 코사인이 이보다 낮으면 필터 풀고 재검색

    extra: dict = field(default_factory=dict)

    def preset(self, name: str) -> "Config":
        if name not in PRESETS:
            raise KeyError(f"모르는 구성 {name!r}. 가능한 값: {list(PRESETS)}")
        return replace(self, preset_name=name, **PRESETS[name])

    def replace(self, **kw) -> "Config":
        return replace(self, **kw)

    @property
    def chunk_files(self) -> list[Path]:
        return [self.db_dir / d / "chunks.jsonl" for d in self.data_subdirs]


def load_config(preset: str = "C", **overrides) -> Config:
    env = os.environ.get
    db_dir = _first_dir(env("DB_DIR"), ROOT.parent / "DB", "/content/DB", ROOT / "DB")
    gs_dir = _first_dir(env("GOLDENSET_DIR"), ROOT.parent / "goldenset", "/content/goldenset", ROOT / "goldenset")
    cache = Path(env("RAG_CACHE_DIR") or (ROOT / "cache"))
    cfg = Config(db_dir=db_dir, goldenset_dir=gs_dir, cache_dir=cache,
                 embedder=env("RAG_EMBEDDER", "bge-m3"))
    cfg = cfg.preset(preset)
    return replace(cfg, **overrides) if overrides else cfg
