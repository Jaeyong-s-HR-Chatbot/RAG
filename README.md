# RAG

누리철도 HR 챗봇 규정 검색(RAG) 베이스라인. 질문 → ① 쪼개기 → ② 분류 → ③ 라우터 → ④ 검색 → `LlmRequest` (LLM 호출 전까지).

## 빠르게 돌려보기 (Colab)

`Demo (2).ipynb` 를 Colab에서 열고 GPU 런타임으로 위에서부터 실행. 첫 셀이 `RAG`·[`DB`](https://github.com/Jaeyong-s-HR-Chatbot/DB)·[`goldenset`](https://github.com/Jaeyong-s-HR-Chatbot/goldenset) 저장소를 `/content` 에 받아요. DB(docker)는 없어도 돼요 — 조각 파일(`DB/data/regs`, `DB/data/laws` 의 `chunks.jsonl`)을 바로 읽어요.

```python
from rag import Pipeline, load_config, split_question
cfg = load_config()                 # 기본 구성 C
pipe = Pipeline(cfg)
r = pipe.run("결혼하면 휴가 며칠이고 경조금 얼마야?")
r.status, r.chunks, r.llm_request   # LlmRequest → rag.goldenset.build_messages() → LLM
pipe.with_config(cfg.preset("A1"))  # 모델·색인 그대로, 스위치만 바꿈
```

## `rag/` 구성

| 파일 | 하는 일 |
| --- | --- |
| `config.py` | 설정과 실험 구성(A0 Dense만 / A1 +BM25 RRF / B +하드 필터 / C +confidence 따라 하드·소프트, 약하면 해제) |
| `split.py` | 복합 질문 쪼개기 (규칙). 조건·주제는 뒤 질문에 이어 붙임 |
| `classify.py` | 카테고리 분류 (BGE-M3 프로토타입 + 키워드), slots, 범위 밖·애매함·민감 표시 |
| `retrieve.py` | Dense(BGE-M3) + BM25(kiwipiepy) → RRF, 카테고리 필터, 내부규정 우선, 후보 20개 |
| `pipeline.py` | 위를 묶고 라우팅(검색 / 검색+HR / 되묻기 / 범위밖 / 담당자연결 / 권한확인), 상위 8개 → `LlmRequest` |
| `goldenset.py` | goldenset 저장소의 `ChunkHit`·`LlmRequest`·`build_messages`·라우터를 불러옴 |

환경변수: `DB_DIR`, `GOLDENSET_DIR`, `RAG_CACHE_DIR`(임베딩 캐시), `RAG_EMBEDDER=mock`(모델 없이 흐름 점검).

HR DB 조회(`HrContext`)와 LLM 호출은 아직 없어요. `hr_needed=True` 면 API 쪽에서 채우면 돼요.

## 다른 노트북

- `Welfare Rag Baseline.ipynb` — 베이스라인 설계·실험 노트북 (A0~C 비교)
- `RAG Embedding.ipynb` — 부서별 질문셋 임베딩 (ChromaDB)
