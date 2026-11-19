"""골든셋에 항 단위 정답 칸 추가: gold_articles(조문 단위), gold_hang(항 단위).

실행 (이 폴더에서): python add_hang_gold.py [DB 저장소의 data/hang 폴더, 기본 ../../../DB/data/hang]
입력: golden_set.json, DB/data/hang/old_to_new.json·chunks.jsonl (DB 저장소 rag_rechunk.py 결과)
출력: golden_set.json · employee_golden_set.csv 에 칸 추가, hang_review.csv (사람이 확인할 문항)

- 원래 gold_chunks 는 그대로 둠 (조문 단위 예전 평가와 비교하려고)
- gold_articles: gold_chunks 의 조각을 조문 묶음 id(article_id, 예 r03:제23조)로 바꾼 것. 같은 조문의 항이 나오면 맞음으로 채점할 때 씀
- gold_hang: 항으로 나뉜 조문이면 그중 정답 항만 고름
  · 조문이 항으로 안 나뉘었으면(조각 1개) 그 조각 그대로
  · 나뉘었으면 key_facts·정답 문장을 항마다 맞춰 봄 (숫자+단위가 맞으면 3점, 낱말이 맞으면 1점, 띄어쓰기·줄바꿈 무시)
    key_fact 마다 점수가 가장 높은 항이 2점 이상이고 2위보다 높으면 그 항을 정답으로
  · 아무 항도 못 고르면 auto=false, review 에 남김 → hang_review.csv 에서 사람이 고름
    (확인한 뒤에는 golden_set.json 의 gold_hang 을 직접 고치고 auto 를 "checked" 로)
- 다시 실행하면 auto 가 "checked" 인 문항은 건드리지 않음
"""
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
HANG = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "../../../DB/data/hang"
GOLD = HERE / "golden_set.json"
CSV = HERE / "employee_golden_set.csv"
REVIEW = HERE / "hang_review.csv"

NUM = re.compile(r"\d+(?:[.,]\d+)?\s*(?:만\s*원|천\s*원|원|일|개월|년|시간|분|%|퍼센트|회|급|세|명|주|호봉|km|박)")
WORD = re.compile(r"[가-힣]{2,}")
STOP = set("경우 있음 없음 가능 불가 해당 대상 기준 이상 이하 이내 초과 미만 지급 규정 따라 따름 조문 확인 인사팀 문의 받을 받음 수있음 "
           "그리고 또는 그러나 이면 이고 으로 에서 에게 까지 부터 하면 해야 한다 된다 있다 없다 본인 직원 자료".split())


def squash(s):
    return re.sub(r"\s+", "", s)


def facts_of(item):
    facts = list(item.get("key_facts") or [])
    facts += [p for p in re.split(r"[.,·/→()]|\s-\s", item.get("reference_answer") or "") if len(p.strip()) >= 4]
    return facts


def score(fact, text):
    t = squash(text)
    nums = [squash(n) for n in NUM.findall(fact)]
    words = [w for w in WORD.findall(fact) if w not in STOP]
    return 3 * sum(n in t for n in nums) + sum(w in t for w in words)


def pick(item, cands, text):
    """조문의 항 조각(cands) 중 key_facts·정답 문장과 맞는 항들"""
    chosen = []
    for f in facts_of(item):
        sc = sorted(((score(f, text[c]), c) for c in cands), reverse=True)
        if sc[0][0] >= 2 and (len(sc) == 1 or sc[0][0] > sc[1][0]) and sc[0][1] not in chosen:
            chosen.append(sc[0][1])
    return [c for c in cands if c in chosen]  # 조문 안 순서대로


def main():
    o2n = json.load(open(HANG / "old_to_new.json", encoding="utf-8"))
    text = {}
    for line in open(HANG / "chunks.jsonl", encoding="utf-8"):
        c = json.loads(line)
        text[c["chunk_id"]] = c["text"]
    g = json.load(open(GOLD, encoding="utf-8"))
    review, stats = [], dict(items=0, auto=0, review=0, checked=0, missing=0)
    for it in g["items"]:
        old = it["gold_chunks"]
        if not (old.get("primary") or old.get("supporting")):
            continue
        stats["items"] += 1
        it["gold_articles"] = {k: list(dict.fromkeys(o2n[c]["article_id"] for c in old.get(k, []) if c in o2n))
                               for k in ("primary", "supporting")}
        missing = [c for k in ("primary", "supporting") for c in old.get(k, []) if c not in o2n]
        if missing:
            stats["missing"] += 1
            print("[없는 조각]", it["id"], missing)
        if (it.get("gold_hang") or {}).get("auto") == "checked":
            stats["checked"] += 1
            continue
        hang, todo = {"primary": [], "supporting": []}, []
        for k in ("primary", "supporting"):
            seen = set()
            for c in old.get(k, []):
                if c not in o2n or o2n[c]["article_id"] in seen:
                    continue
                seen.add(o2n[c]["article_id"])
                cands = o2n[c]["new"]
                got = cands if len(cands) == 1 else pick(it, cands, text)
                if not got:
                    todo.append(o2n[c]["article_id"])
                    got = []
                hang[k] += [x for x in got if x not in hang[k]]
        it["gold_hang"] = dict(hang, auto=not todo, review=todo)
        if todo:
            stats["review"] += 1
            for aid in todo:
                cands = next(v["new"] for v in o2n.values() if v["article_id"] == aid)
                review.append({"id": it["id"], "질문": it["question"], "key_facts": " / ".join(it.get("key_facts") or []),
                               "정답": it.get("reference_answer", ""), "조문": aid,
                               "후보 항": "\n".join(f"{c}: {squash(text[c])[:80]}" for c in cands)})
        else:
            stats["auto"] += 1

    g.setdefault("fields", {})
    g["fields"]["gold_articles"] = "gold_chunks 를 조문 묶음 id(article_id = 문서id:조문, DB rag.chunks.article_id)로 바꾼 것. 조문 단위 채점용"
    g["fields"]["gold_hang"] = ("항 단위 정답 조각(DB rag.chunks, 2026-11-19 항 단위 청킹). auto=true 는 key_facts로 자동 매칭, "
                                "false 는 review 의 조문을 사람이 골라야 함, \"checked\" 는 사람이 확인함. add_hang_gold.py")
    json.dump(g, open(GOLD, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    by_id = {it["id"]: it for it in g["items"]}
    rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig", newline="")))
    cols = list(rows[0].keys())
    new_cols = ["정답조문(article)", "정답항(hang)", "항매칭"]
    at = cols.index("정답조문(primary)") + 1
    cols = cols[:at] + new_cols + [c for c in cols[at:] if c not in new_cols]
    for r in rows:
        it = by_id[r["id"]]
        gh = it.get("gold_hang") or {}
        r["정답조문(article)"] = ", ".join((it.get("gold_articles") or {}).get("primary", []))
        r["정답항(hang)"] = ", ".join(gh.get("primary", []))
        r["항매칭"] = "" if not gh else ("확인함" if gh.get("auto") == "checked" else "자동" if gh.get("auto") else "확인 필요")
    with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with open(REVIEW, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "질문", "key_facts", "정답", "조문", "후보 항"], lineterminator="\n")
        w.writeheader()
        w.writerows(review)
    print(f"정답 조각 있는 문항 {stats['items']}: 자동 {stats['auto']}, 확인 필요 {stats['review']}(조문 {len(review)}건), "
          f"이미 확인 {stats['checked']}, 없는 조각 {stats['missing']} → {REVIEW.name}")


if __name__ == "__main__":
    main()
