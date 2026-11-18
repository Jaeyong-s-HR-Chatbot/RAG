"""골든셋(eval/golden/golden_set.json, 279문항)으로 검색·답변을 단계별로 평가한다.

노션 '골든셋 > 답변생성(LLM) 골든셋 > 평가' 페이지 기준.

단계
  split      dev(조정용 ~80) / test(최종용 ~170) 나누기. uncertain 29문항은 점수에서 빼고 따로 집계
  retrieval  ② 하이브리드 검색 / ③ 리랭커  → Recall@k, MRR, nDCG@5, 문서 Recall
  generate   ④ 답변 생성. oracle = 정답 조문 + 비슷한 오답 조문 5개를 넣음 (모델 비교용)
                         e2e    = run_pipeline 전체 실행 (의도 분류 → 조회 → 검색 → 생성)
  score      ① 규칙 채점(계산 값·숫자·지어낸 숫자·근거 조문) → ② 의미 판정(--judge, Opus)
             → ③ 최종 판정: key_facts 80% 이상 + 금지 주장 0건 + (계산 문항) 값 일치

예시
  python scripts/eval_golden.py split
  python scripts/eval_golden.py retrieval --split test
  python scripts/eval_golden.py generate --mode oracle --split dev --limit 20
  python scripts/eval_golden.py score --run eval/golden/runs/<폴더> --judge
"""
import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]          # eval/ 폴더가 있는 곳 (backend 또는 RAG-hyerim)
# 챗봇 코드(app/)가 있는 backend 폴더. 이 파일이 backend 밖에 있으면 옆 폴더의 backend, BACKEND_DIR로 바꿀 수 있음
BACKEND = Path(os.getenv("BACKEND_DIR") or (ROOT if (ROOT / "app").is_dir() else ROOT.parent / "backend")).resolve()
REPO = BACKEND.parent                                 # hr_chatbot (DB/ 폴더가 있는 곳)
GOLDEN_DIR = ROOT / "eval" / "golden"
GOLDEN_PATH = GOLDEN_DIR / "golden_set.json"
SPLIT_PATH = GOLDEN_DIR / "splits.json"
RUNS_DIR = GOLDEN_DIR / "runs"
CORPUS_FILES = [REPO / "DB" / "data" / "regs" / "chunks.jsonl", REPO / "DB" / "data" / "laws" / "chunks.jsonl"]
MERGED_CORPUS = GOLDEN_DIR / "cache" / "all_chunks.jsonl"
PERSONAS_PATH = REPO / "DB" / "data" / "meta" / "demo_personas.json"

DEV_RATIO = 0.32            # 250문항 중 약 80
KEY_FACT_PASS = 0.8         # 최종 정답 기준: key_facts 포함 비율
NUM_TOL = 0.01              # 숫자 일치 허용 오차 (1%: "약 191만원" ↔ 1,913,876)
JUDGE_MODEL = "claude-opus-5-5"
REFUSAL_HINTS = ("규정에서 확인되지 않", "확인되지 않아요", "찾지 못했", "인사팀에 문의", "인사팀 확인", "담당 부서")


# ───────────── 골든셋·분할 ─────────────
def load_items() -> list[dict]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["items"]


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def make_splits(items: list[dict]) -> dict:
    """유형별로 (페르소나, 카테고리, id 해시) 순으로 줄 세운 뒤 일정 간격으로 dev를 뽑음.
    작은 유형(틀린 전제 9개 등)도 dev·test에 고르게 들어가고, 페르소나·카테고리 비율도 비슷해짐 (재현됨)"""
    groups = defaultdict(list)
    out = {}
    for it in items:
        if it["uncertain"]:
            out[it["id"]] = "uncertain"
        else:
            groups[it["type"]].append(it)
    for its in groups.values():
        its.sort(key=lambda it: (it["persona"], it["category"], _h(it["id"])))
        for i, it in enumerate(its):
            out[it["id"]] = "dev" if math.floor((i + 1) * DEV_RATIO) > math.floor(i * DEV_RATIO) else "test"
    return out


def get_splits(items: list[dict]) -> dict:
    if SPLIT_PATH.exists():
        return json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    s = make_splits(items)
    SPLIT_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    return s


def select(items, split: str, limit: int | None, ids: str | None):
    sp = get_splits(items)
    want = set(ids.split(",")) if ids else None
    out = [it for it in items if (want and it["id"] in want) or (not want and (split == "all" or sp[it["id"]] == split))]
    for it in out:
        it["_split"] = sp[it["id"]]
    return out[:limit] if limit else out


# ───────────── 백엔드 불러오기 ─────────────
def _prepare_corpus():
    """DB가 없으면 규정+법령 청크를 하나로 합쳐 RULES_CHUNKS_PATH로 지정 (검색이 법령도 보도록)"""
    if os.getenv("HRDB_URL") or os.getenv("RULES_CHUNKS_PATH"):
        return
    MERGED_CORPUS.parent.mkdir(parents=True, exist_ok=True)
    if not MERGED_CORPUS.exists():
        with open(MERGED_CORPUS, "w", encoding="utf-8") as w:
            for p in CORPUS_FILES:
                if p.exists():
                    w.write(p.read_text(encoding="utf-8").rstrip("\n") + "\n")
    os.environ["RULES_CHUNKS_PATH"] = str(MERGED_CORPUS)


def backend():
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
    _prepare_corpus()
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app import config
    from app.graph import build, nodes, retriever
    return config, build, nodes, retriever


def chunk_texts() -> dict:
    out = {}
    for p in CORPUS_FILES:
        if not p.exists():
            continue
        for line in open(p, encoding="utf-8"):
            r = json.loads(line)
            cid = r.get("chunk_id") or r.get("id")
            art = (r.get("article") or "").strip()
            title = (r.get("article_title") or "").strip()
            out[cid] = {"id": cid, "citation": f"{r.get('doc_name', '')} {art}" + (f"({title[:20]})" if title and art else ""),
                        "revised": r.get("effective_date") or "", "text": r["text"]}
    return out


# ───────────── ②③ 검색 지표 ─────────────
def retrieval_metrics(gold: list[str], got: list[str], gold_docs: list[str]) -> dict:
    g = set(gold)
    first = next((i + 1 for i, x in enumerate(got) if x in g), None)
    dcg = sum(1 / math.log2(i + 2) for i, x in enumerate(got[:5]) if x in g)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(g), 5)))
    m = {f"recall@{k}": len(g & set(got[:k])) / len(g) for k in (5, 10, 30)}
    m |= {"all_primary@5": float(g <= set(got[:5])), "mrr": 1 / first if first else 0.0,
          "ndcg@5": dcg / idcg if idcg else 0.0,
          "doc_recall@5": len(set(gold_docs) & {x.split("-")[0] for x in got[:5]}) / len(gold_docs) if gold_docs else 0.0}
    return m


def cmd_retrieval(a):
    config, _, _, retriever = backend()
    items = [it for it in select(load_items(), a.split, a.limit, a.ids) if it["gold_chunks"]["primary"]]
    run = new_run(a.name or f"retrieval_{retriever.mode()}")
    rows = []
    for it in items:
        t = perf_counter()
        hits = retriever.search(it["question"], k=a.k)
        got = [h["id"] for h in hits]
        rows.append({"id": it["id"], "split": it["_split"], "type": it["type"], "category": it["category"],
                     "multi_hop": it["multi_hop"], "gold": it["gold_chunks"]["primary"], "got": got[:10],
                     "ms": int((perf_counter() - t) * 1000),
                     **retrieval_metrics(it["gold_chunks"]["primary"], got, it["document_ids"])})
    write_jsonl(run / "retrieval.jsonl", rows)
    keys = ["recall@5", "recall@10", "recall@30", "all_primary@5", "mrr", "ndcg@5", "doc_recall@5"]
    summary = {"mode": retriever.mode(), "n": len(rows), "k": a.k,
               "overall": avg(rows, keys),
               "by_category": {c: avg([r for r in rows if r["category"] == c], keys[:1] + ["mrr"]) for c in sorted({r["category"] for r in rows})},
               "multi_hop": avg([r for r in rows if r["multi_hop"]], keys),
               "misses@30": [r["id"] for r in rows if r["recall@30"] == 0]}
    finish(run, summary, f"검색 평가 ({summary['mode']}, {len(rows)}문항)")


# ───────────── ④ 답변 생성 ─────────────
def _personas() -> dict:
    if not PERSONAS_PATH.exists():
        return {}
    return {p["key"]: p for p in json.loads(PERSONAS_PATH.read_text(encoding="utf-8"))}


def cmd_generate(a):
    config, build, nodes, retriever = backend()
    items = select(load_items(), a.split, a.limit, a.ids)
    texts = chunk_texts() if a.mode == "oracle" else {}
    personas = _personas()
    model = "fake" if config.USE_FAKE_LLM else config.LLM_MODEL
    run = new_run(a.name or f"{a.mode}_{re.sub(r'[^A-Za-z0-9.]+', '-', model)}")
    out_path = run / "answers.jsonl"
    rows = []
    for n, it in enumerate(items, 1):
        p = personas.get(it["persona"], {})
        t = perf_counter()
        try:
            if a.mode == "oracle":
                ctx = oracle_context(it, texts, retriever)
                s = {"employee_id": p.get("emp_id", ""), "role": "employee", "masked_question": it["question"],
                     "intent": "both" if it["needs_hr_data"] else "rule", "chunks": ctx, "lookup": {}, "calc": {}}
                ans = nodes.generate(s)["answer"]
                rec = {"answer": ans, "kind": "answer", "intent": s["intent"], "chunks": [c["id"] for c in ctx],
                       "context_text": "\n".join(c["text"] for c in ctx)}
            else:
                emp = it.get("emp_id") or p.get("emp_id", "E1584")   # 본인 데이터 문항은 골든 페르소나 직원(E1634·E1033·E1768)
                s = build.run_pipeline(emp, "employee", it["question"], p.get("name", "") if emp == p.get("emp_id") else "")
                rec = {"answer": s.get("answer", ""), "kind": s.get("kind"), "intent": s.get("intent"),
                       "tools_used": s.get("tools_used", []), "chunks": [c["id"] for c in s.get("chunks", [])],
                       "context_text": "\n".join(c.get("text", "") for c in s.get("chunks", []))
                       + "\n" + json.dumps(s.get("lookup", {}), ensure_ascii=False, default=str)
                       + "\n" + json.dumps(s.get("calc", {}), ensure_ascii=False, default=str)}
            err = None
        except Exception as e:      # 한 문항 실패로 전체가 멈추지 않게
            rec, err = {"answer": "", "kind": "error", "chunks": [], "context_text": ""}, f"{type(e).__name__}: {e}"[:300]
        rows.append({"id": it["id"], "split": it["_split"], "mode": a.mode, "model": model,
                     "ms": int((perf_counter() - t) * 1000), "error": err, **rec})
        print(f"[{n}/{len(items)}] {it['id']} {rows[-1]['ms']}ms" + (f" ERROR {err}" if err else ""), flush=True)
        write_jsonl(out_path, rows)   # 중간에 끊겨도 남도록 매번 저장
    print(f"\n저장: {out_path}\n다음: python scripts/eval_golden.py score --run {run.relative_to(ROOT)}")


def oracle_context(it: dict, texts: dict, retriever, n: int = 5) -> list[dict]:
    """정답 조문(primary → supporting) + 검색 상위의 비슷한 오답 조문으로 5개. 순서는 id 해시로 섞음"""
    gold = [texts[c] for c in it["gold_chunks"]["primary"] + it["gold_chunks"]["supporting"] if c in texts][:n]
    ids = {c["id"] for c in gold}
    distract = [h for h in retriever.search(it["question"], k=n * 3) if h["id"] not in ids]
    ctx = gold + [{k: h[k] for k in ("id", "citation", "revised", "text")} for h in distract[: n - len(gold)]]
    ctx.sort(key=lambda c: _h(it["id"] + c["id"]))
    return [{**c, "score": 1.0} for c in ctx]


# ───────────── 규칙 채점 ─────────────
_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|만|천)?")
_UNIT = {"억": 1e8, "천만": 1e7, "만": 1e4, "천": 1e3, None: 1}


def numbers(text: str) -> list[float]:
    """'4만 7,847원' → 47847, '191만원' → 1910000, '2027.6.30' → 2027, 6, 30. 묶인 값과 낱개 값을 모두 돌려줌"""
    out, acc, prev, end = [], None, None, -1
    for m in _NUM.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", "")) * _UNIT[m.group(2)]
        except ValueError:
            continue
        out.append(v)
        mult = _UNIT[m.group(2)]
        if acc is not None and prev and prev > mult and text[end:m.start()].strip() == "":
            acc += v
        else:
            if acc is not None:
                out.append(acc)
            acc = v
        prev, end = mult, m.end()
    if acc is not None:
        out.append(acc)
    return out


def has_num(target: float, pool: list[float]) -> bool:
    return any(abs(x - target) <= max(NUM_TOL * abs(target), 0.5) for x in pool)


_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def calc_values(calc: dict | None) -> list[float]:
    """계산 정답을 숫자 목록으로 (지어낸 숫자 판정의 근거용). 날짜는 연·월·일, 정답이 없으면(None) 빈 목록"""
    a = (calc or {}).get("answer")
    if a is None:
        return []
    if isinstance(a, (int, float)):
        return [float(a)]
    if d := _DATE.fullmatch(str(a).strip()):
        return [float(x) for x in d.groups()]
    return numbers(str(a))


def calc_match(calc: dict | None, ans: str, a_nums: list[float]) -> bool | None:
    """계산 정답이 답에 있는지. 정답이 없는 문항(None: 본인 데이터로 실행 때 정해지는 값)은 채점하지 않음"""
    a = (calc or {}).get("answer")
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return has_num(float(a), a_nums)
    if d := _DATE.fullmatch(str(a).strip()):   # 2027-06-30 = "2027년 6월 30일" = "2027.6.30" = "2027-06-30"
        y, m, dd = (int(x) for x in d.groups())
        return bool(re.search(rf"{y}\s*(?:년|[./-])\s*0?{m}\s*(?:월|[./-])\s*0?{dd}(?!\d)", ans or ""))
    vals = numbers(str(a))
    return all(has_num(v, a_nums) for v in vals) if vals else re.sub(r"\s+", "", str(a)) in re.sub(r"\s+", "", ans or "")


def articles(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    return set(re.findall(r"제\d+조(?:의\d+)?", t)) | set(re.findall(r"별표\d+", t))


def is_refusal(ans: str, kind: str | None) -> bool:
    return kind == "refused" or any(h in (ans or "") for h in REFUSAL_HINTS)


def rule_score(it: dict, r: dict) -> dict:
    ans = r["answer"] or ""
    a_nums = numbers(ans)
    s = {"refused": is_refusal(ans, r.get("kind"))}
    calc = it.get("calculation")
    s["calc_match"] = calc_match(calc, ans, a_nums)
    # key_facts 중 숫자가 있는 것: 그 숫자가 답에 모두 있는지 (판정 모델이 없을 때의 근사치로도 씀)
    facts = []
    for f in it["key_facts"]:
        fn = [x for x in numbers(f) if x >= 2]
        if fn:
            facts.append(all(has_num(x, a_nums) for x in fn))
        else:
            facts.append(re.sub(r"\s+", "", f) in re.sub(r"\s+", "", ans))
    s["fact_rule"] = facts
    s["fact_numbers_ok"] = all(ok for f, ok in zip(it["key_facts"], facts) if any(x >= 2 for x in numbers(f))) \
        if any(numbers(f) for f in it["key_facts"]) else None
    # 지어낸 숫자: 답에 나온 10 이상 숫자가 질문·근거·조회·계산 결과 어디에도 없음 (계산 결과처럼 정당하게 새로 나온 값은 오탐 가능)
    support = numbers(it["question"] + "\n" + r.get("context_text", "")) + calc_values(calc)
    s["unsupported_numbers"] = sorted({x for x in a_nums if x >= 10 and not has_num(x, support)})[:10]
    gold_art = articles(it["basis"])
    s["citation_ok"] = bool(gold_art & articles(ans)) if gold_art else None
    s["gold_retrieved"] = (bool(set(it["gold_chunks"]["primary"]) & set(r.get("chunks", [])))
                           if it["gold_chunks"]["primary"] else None)
    return s


# ───────────── 의미 판정 (Opus) ─────────────
JUDGE_SYSTEM = """당신은 HR 챗봇 답변 채점자다. 정답 문장과 채점 기준을 보고 챗봇 답변을 판정한다.
- key_facts: 각 항목의 '내용'이 답변에 들어 있으면 true. 표현이 달라도 뜻이 같으면 true. 숫자는 표기(47,847원 / 약 4만 8천 원)가 달라도 값이 같으면 true.
- must_not_include: 답변이 그 주장을 '한' 경우에만 true. 그 주장을 부정하거나 바로잡는 문장("3만원이 아니라 2만원")은 위반이 아니다.
- declined_properly: 규정 없음 문항에서만 판정. 지어내지 않고 규정에 없음/인사팀 확인으로 안내했으면 true. 다른 문항은 null.
판정 근거는 reason에 한 문장으로 적는다."""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["key_facts", "must_not_include", "declined_properly", "reason"],
    "properties": {
        "key_facts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["fact", "included"],
                                                  "properties": {"fact": {"type": "string"}, "included": {"type": "boolean"}}}},
        "must_not_include": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                         "required": ["claim", "violated"],
                                                         "properties": {"claim": {"type": "string"}, "violated": {"type": "boolean"}}}},
        "declined_properly": {"type": ["boolean", "null"]},
        "reason": {"type": "string"},
    },
}


def judge(client, it: dict, answer: str) -> dict:
    user = json.dumps({"문항유형": it["type"], "질문": it["question"], "정답": it["reference_answer"], "근거규정": it["basis"],
                       "key_facts": it["key_facts"], "must_not_include": it["must_not_include"], "챗봇답변": answer},
                      ensure_ascii=False, indent=1)
    resp = client.beta.messages.create(
        model=JUDGE_MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        system=JUDGE_SYSTEM, messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("판정 모델이 거절함")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def cmd_score(a):
    run = Path(a.run) if Path(a.run).is_absolute() else ROOT / a.run
    gold = {it["id"]: it for it in load_items()}
    answers = read_jsonl(run / "answers.jsonl")
    judged_path = run / "judged.jsonl"
    cache = {r["id"]: r for r in read_jsonl(judged_path)} if judged_path.exists() else {}
    client = None
    if a.judge:
        import anthropic    # pip install anthropic, 인증은 ANTHROPIC_API_KEY 또는 ant auth login
        client = anthropic.Anthropic()
    rows = []
    for n, r in enumerate(answers, 1):
        it = gold[r["id"]]
        s = rule_score(it, r)
        j = cache.get(r["id"], {}).get("judge")
        if client and j is None and r["answer"]:
            try:
                j = judge(client, it, r["answer"])
            except Exception as e:
                print(f"  판정 실패 {r['id']}: {type(e).__name__}: {str(e)[:150]}")
            print(f"[{n}/{len(answers)}] 판정 {r['id']}", flush=True)
        if j:
            kf = [x["included"] for x in j["key_facts"]]
            viol = sum(x["violated"] for x in j["must_not_include"])
            declined = j.get("declined_properly")
        else:   # 판정 모델 없이: 규칙 근사치 (숫자 없는 key_fact는 글자 그대로 있어야 인정 → 낮게 나옴)
            kf, viol, declined = s["fact_rule"], None, None
        ratio = sum(kf) / len(kf) if kf else 0.0
        if it["type"] == "규정 없음":
            correct = bool(declined) if declined is not None else s["refused"]
        else:
            correct = ratio >= KEY_FACT_PASS and (viol or 0) == 0 and s["calc_match"] is not False and not r.get("error")
        rows.append({"id": r["id"], "split": r["split"], "type": it["type"], "category": it["category"],
                     "multi_hop": it["multi_hop"], "needs_hr_data": it["needs_hr_data"], "ms": r["ms"],
                     "intent": r.get("intent"), "tools_used": r.get("tools_used"), "judged": j is not None,
                     "key_fact_ratio": round(ratio, 3), "violations": viol, "correct": correct, **s, "judge": j})
    write_jsonl(judged_path, rows)
    finish(run, summarize(rows), f"답변 평가 ({answers[0]['mode'] if answers else '-'} / {answers[0]['model'] if answers else '-'})")


def rate(rows, f) -> float | None:
    rows = list(rows)
    return round(sum(map(f, rows)) / len(rows), 3) if rows else None


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["split"] != "uncertain"]
    normal = [r for r in scored if r["type"] == "일반"]
    lat = sorted(r["ms"] for r in rows)
    out = {
        "n": len(rows), "n_scored": len(scored), "judged": sum(r["judged"] for r in rows),
        "accuracy": rate(scored, lambda r: r["correct"]),
        "accuracy_by_split": {s: rate([r for r in scored if r["split"] == s], lambda r: r["correct"]) for s in ("dev", "test")},
        "uncertain_accuracy(참고)": rate([r for r in rows if r["split"] == "uncertain"], lambda r: r["correct"]),
        "key_fact_ratio": rate(scored, lambda r: r["key_fact_ratio"]),
        "calc_accuracy": rate([r for r in scored if r["calc_match"] is not None], lambda r: r["calc_match"]),
        "multi_hop_accuracy": rate([r for r in scored if r["multi_hop"]], lambda r: r["correct"]),
        "포괄질문_accuracy": rate([r for r in scored if r["type"] == "포괄 질문"], lambda r: r["correct"]),
        "틀린전제_교정률": rate([r for r in scored if r["type"] == "틀린 전제"], lambda r: r["correct"]),
        "규정없음_거절률": rate([r for r in scored if r["type"] == "규정 없음"], lambda r: r["correct"]),
        "잘못된_거절률": rate(normal, lambda r: r["refused"]),
        "금지주장_위반률": rate([r for r in scored if r["violations"] is not None], lambda r: r["violations"] > 0),
        "지어낸숫자_의심률": rate(scored, lambda r: bool(r["unsupported_numbers"])),
        "근거조문_인용률": rate([r for r in scored if r["citation_ok"] is not None], lambda r: r["citation_ok"]),
        "정답조문_검색됨": rate([r for r in scored if r["gold_retrieved"] is not None], lambda r: r["gold_retrieved"]),
        "latency_ms": {"p50": lat[len(lat) // 2], "p95": lat[int(len(lat) * 0.95) - 1] if len(lat) > 1 else lat[0]} if lat else None,
        "by_category": {c: rate([r for r in scored if r["category"] == c], lambda r: r["correct"])
                        for c in sorted({r["category"] for r in scored})},
    }
    if any(r["tools_used"] is not None for r in rows):      # e2e: 본인 조회가 필요한 문항에서 조회 도구를 썼는지 (① 의도 분류의 거친 확인)
        hr = [r for r in scored if r["needs_hr_data"]]
        out["조회필요_도구사용률"] = rate(hr, lambda r: bool(r["tools_used"]))
        out["조회불필요_도구사용률"] = rate([r for r in scored if not r["needs_hr_data"]], lambda r: bool(r["tools_used"]))
        out["intent_분포"] = dict(Counter(r["intent"] for r in rows))
    # 오답 원인 귀속: 검색에서 정답 조문을 못 찾음 / 찾았는데 생성이 틀림
    wrong = [r for r in scored if not r["correct"]]
    out["오답_원인"] = {"검색_실패": sum(r["gold_retrieved"] is False for r in wrong),
                    "생성_실패(정답조문 있음)": sum(r["gold_retrieved"] is True for r in wrong),
                    "기타": sum(r["gold_retrieved"] is None for r in wrong)}
    out["선정기준_체크"] = {"규정없음_거절 ≥ 0.9": (out["규정없음_거절률"] or 0) >= 0.9,
                       "잘못된_거절 ≤ 0.05": (out["잘못된_거절률"] or 0) <= 0.05,
                       "응답 p50 ≤ 10s": bool(lat) and out["latency_ms"]["p50"] <= 10000}
    if out["judged"] < len(rows):
        out["주의"] = f"{len(rows) - out['judged']}문항은 판정 모델 없이 규칙 근사치로 채점됨 (--judge로 다시 실행)"
    return out


# ───────────── 공통 ─────────────
def avg(rows, keys) -> dict:
    return {k: round(statistics.mean(r[k] for r in rows), 3) for k in keys} if rows else {}


def new_run(name: str) -> Path:
    p = RUNS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_jsonl(path: Path, rows: list[dict]):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def finish(run: Path, summary: dict, title: str):
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n== {title} ==\n" + json.dumps(summary, ensure_ascii=False, indent=1) + f"\n\n저장: {run}")


def cmd_split(a):
    items = load_items()
    if a.force and SPLIT_PATH.exists():
        SPLIT_PATH.unlink()
    sp = get_splits(items)
    print(Counter(sp.values()), "→", SPLIT_PATH)
    by = defaultdict(Counter)
    for it in items:
        by[sp[it["id"]]][it["type"]] += 1
    for k, v in by.items():
        print(f"  {k}: {dict(v)}")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("split")
    p.add_argument("--force", action="store_true", help="splits.json을 다시 만듦")
    for name in ("retrieval", "generate"):
        p = sub.add_parser(name)
        p.add_argument("--split", default="test", choices=["dev", "test", "uncertain", "all"])
        p.add_argument("--limit", type=int)
        p.add_argument("--ids", help="쉼표로 구분한 문항 id만")
        p.add_argument("--name", help="결과 폴더 이름")
        if name == "retrieval":
            p.add_argument("--k", type=int, default=30, help="후보 수 (② 단계 Recall@k)")
        else:
            p.add_argument("--mode", default="oracle", choices=["oracle", "e2e"])
    p = sub.add_parser("score")
    p.add_argument("--run", required=True, help="generate 결과 폴더")
    p.add_argument("--judge", action="store_true", help=f"{JUDGE_MODEL}로 의미 판정 (비용 발생)")
    a = ap.parse_args()
    {"split": cmd_split, "retrieval": cmd_retrieval, "generate": cmd_generate, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
