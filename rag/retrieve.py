"""④ 검색기: (필터) → Dense + BM25 → RRF → 후보 최대 20개.

검색 대상은 DB 저장소의 조각 파일(data/regs, data/laws 의 chunks.jsonl) 중 searchable 인 것.
DB(docker)가 없어도 돌아가게 파일에서 바로 읽고, 임베딩은 cache_dir 에 저장해 두 번째부터는 바로 불러온다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .classify import COMMON, QueryClass


# ---------------------------------------------------------------- 조각 읽기
def load_rows(cfg) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    missing = [p for p in cfg.chunk_files if not p.is_file()]
    if len(missing) == len(cfg.chunk_files):
        raise FileNotFoundError(
            f"조각 파일이 없어요: {[str(p) for p in missing]}\n"
            "  git clone https://github.com/Jaeyong-s-HR-Chatbot/DB 한 뒤 "
            "os.environ['DB_DIR'] = '<그 경로>' 로 알려 주세요."
        )
    for p in cfg.chunk_files:
        if not p.is_file():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if not r.get("searchable", True) or r["document_id"] in cfg.exclude_docs:
                    continue
                rows[r["chunk_id"]] = r
    return rows


def embed_text(r: dict) -> str:
    """DB rag_embed.py 와 같은 입력: '규정명 조문 제목\\n본문'"""
    head = " ".join(x for x in (r["doc_name"], r.get("article") or "", r.get("article_title") or "") if x)
    return f"{head}\n{r['text']}"


def title_of(r: dict) -> str:
    head = f"{r['doc_name']} {r.get('article') or ''}".strip()
    return f"{head}({r['article_title']})" if r.get("article") and r.get("article_title") else head


# ---------------------------------------------------------------- BM25
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
_JOSA = re.compile(r"(은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|까지|부터|이나|나|요|이야|야|예요|이에요)$")


def _kiwi():
    try:
        from kiwipiepy import Kiwi
        return Kiwi()
    except Exception:
        return None


class BM25:
    """한국어 BM25. kiwipiepy 가 있으면 형태소(명사·동사 어간), 없으면 어절 + 글자 2-gram."""

    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75):
        self.kiwi = _kiwi()
        self.k1, self.b = k1, b
        toks = [self.tokenize(d) for d in docs]
        self.tf = [Counter(t) for t in toks]
        self.len = np.array([len(t) for t in toks], dtype=np.float32)
        self.avg = float(self.len.mean()) if len(toks) else 1.0
        df = Counter(w for t in self.tf for w in t)
        n = len(toks)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.index: dict[str, list[int]] = {}
        for i, t in enumerate(self.tf):
            for w in t:
                self.index.setdefault(w, []).append(i)

    def tokenize(self, s: str) -> list[str]:
        if self.kiwi is not None:
            return [t.form for t in self.kiwi.tokenize(s) if t.tag[:2] in ("NN", "VV", "VA", "XR", "SL", "SN") or t.tag == "NR"]
        out = []
        for w in _WORD.findall(s):
            w = _JOSA.sub("", w) or w
            out.append(w)
            out += [w[i:i + 2] for i in range(len(w) - 1)] if len(w) > 2 else []
        return out

    def scores(self, q: str) -> np.ndarray:
        s = np.zeros(len(self.tf), dtype=np.float32)
        for w in set(self.tokenize(q)):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i in self.index[w]:
                f = self.tf[i][w]
                s[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return s


# ---------------------------------------------------------------- 결과 형식
@dataclass
class Candidate:
    chunk_id: str
    score: float            # 최종 점수 (RRF + 가산)
    cos: float              # 질문과의 코사인 (Dense)
    dense_rank: int | None
    bm25_rank: int | None
    doc_kind: str
    categories: list[str]
    in_filter: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = round(d["score"], 5)
        d["cos"] = round(d["cos"], 4)
        return d


@dataclass
class SearchResult:
    candidates: list[Candidate]
    used_filter: bool
    filter_categories: list[str]
    filter_kind: str                     # none / hard / soft
    used_fallback: bool
    top_cos: float
    doc_kind_counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------- 검색기
class Retriever:
    def __init__(self, cfg, embedder, rows: dict[str, dict] | None = None, matrix: np.ndarray | None = None,
                 bm25: BM25 | None = None):
        self.cfg = cfg
        self.embedder = embedder
        self.rows = rows if rows is not None else load_rows(cfg)
        self.ids = list(self.rows)
        self.pos = {c: i for i, c in enumerate(self.ids)}
        self.cats = [set(self.rows[c].get("categories") or [COMMON]) for c in self.ids]
        self.is_reg = np.array([self.rows[c].get("doc_kind") != "법령" for c in self.ids])
        self.matrix = matrix if matrix is not None else self._load_or_embed()
        self.bm25 = bm25 if bm25 is not None else BM25([embed_text(self.rows[c]) for c in self.ids])

    def with_config(self, cfg) -> "Retriever":
        """모델·색인은 그대로, 스위치만 바꾼 새 검색기."""
        return Retriever(cfg, self.embedder, self.rows, self.matrix, self.bm25)

    def _load_or_embed(self) -> np.ndarray:
        cfg = self.cfg
        key = hashlib.md5(("|".join(self.ids) + self.embedder.name + cfg.model_name + str(cfg.max_seq_len)).encode()).hexdigest()[:12]
        path = Path(cfg.cache_dir) / f"emb_{self.embedder.name}_{key}.npy"
        if path.is_file():
            m = np.load(path)
            if m.shape[0] == len(self.ids):
                return m
        texts = [embed_text(self.rows[c]) for c in self.ids]
        # 긴 것부터 묶으면 패딩이 줄어 빠름
        order = np.argsort([-len(t) for t in texts])
        vecs = self.embedder.encode([texts[i] for i in order], show_progress=True)
        m = np.zeros_like(vecs)
        m[order] = vecs
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, m)
        return m

    # ------------------------------------------------------------
    def _ranks(self, scores: np.ndarray, mask: np.ndarray, n: int) -> list[int]:
        idx = np.where(mask)[0]
        if not len(idx):
            return []
        return [int(i) for i in idx[np.argsort(-scores[idx], kind="stable")[:n]]]

    def _search(self, q: str, qvec: np.ndarray, mask: np.ndarray, boost_cats: set[str] | None) -> list[Candidate]:
        cfg = self.cfg
        cos = self.matrix @ qvec
        fused: dict[int, float] = {}
        d_rank: dict[int, int] = {}
        b_rank: dict[int, int] = {}
        if cfg.use_dense:
            for r, i in enumerate(self._ranks(cos, mask, cfg.n_candidates), 1):
                d_rank[i] = r
                fused[i] = fused.get(i, 0.0) + 1.0 / (cfg.rrf_k + r)
        if cfg.use_bm25:
            bm = self.bm25.scores(q)
            idx = [i for i in self._ranks(bm, mask, cfg.n_candidates) if bm[i] > 0]
            for r, i in enumerate(idx, 1):
                b_rank[i] = r
                fused[i] = fused.get(i, 0.0) + 1.0 / (cfg.rrf_k + r)
        if not cfg.use_bm25 and not cfg.use_dense:
            raise ValueError("use_dense / use_bm25 중 하나는 켜야 해요")
        for i in fused:
            mult = 1.0
            if cfg.reg_boost and self.is_reg[i]:
                mult += cfg.reg_boost
            if boost_cats and self.cats[i] & boost_cats:
                mult += cfg.soft_boost
            fused[i] *= mult
        order = sorted(fused, key=lambda i: -fused[i])[: cfg.pool]
        return [
            Candidate(
                chunk_id=self.ids[i], score=fused[i], cos=float(cos[i]), dense_rank=d_rank.get(i), bm25_rank=b_rank.get(i),
                doc_kind=self.rows[self.ids[i]].get("doc_kind") or "", categories=sorted(self.cats[i]),
                in_filter=bool(self.cats[i] & boost_cats) if boost_cats else bool(mask[i]),
            )
            for i in order
        ]

    def search(self, q: str, qc: QueryClass | None = None, qvec: np.ndarray | None = None) -> SearchResult:
        cfg = self.cfg
        if qvec is None:
            qvec = self.embedder.encode([q], is_query=True)[0]
        everything = np.ones(len(self.ids), dtype=bool)
        cats = set(qc.category_names) | {COMMON} if qc else set()
        mode = cfg.filter_mode if qc else "none"
        if mode == "adaptive":
            mode = "hard" if qc.confident else "soft"

        used_fallback = False
        if mode == "hard":
            mask = np.array([bool(c & cats) for c in self.cats])
            cands = self._search(q, qvec, mask, None)
            top_cos = max((c.cos for c in cands), default=0.0)
            if cfg.filter_mode == "adaptive" and (len(cands) < cfg.min_filtered or top_cos < cfg.fallback_cos):
                cands = self._search(q, qvec, everything, cats)       # 필터가 너무 좁음 → 해제(소프트로)
                used_fallback, mode = True, "soft"
        elif mode == "soft":
            cands = self._search(q, qvec, everything, cats)
        else:
            cands = self._search(q, qvec, everything, None)
        return SearchResult(
            candidates=cands, used_filter=mode != "none", filter_categories=sorted(cats) if mode != "none" else [],
            filter_kind=mode, used_fallback=used_fallback, top_cos=round(max((c.cos for c in cands), default=0.0), 4),
            doc_kind_counts=dict(Counter(c.doc_kind for c in cands)),
        )
