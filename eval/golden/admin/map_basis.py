"""관리자 질문셋의 '규정 근거' 글자 → 조문(chunk) ID. 매핑 결과를 출력해 사람이 검수하도록."""
import csv, json, re, sys
from collections import defaultdict
from pathlib import Path

REPO = next(p for p in Path(__file__).resolve().parents if (p / "DB" / "data").is_dir())   # DB/ 와 질문셋/ 이 있는 hr_chatbot 폴더
ALIAS = {
    "남녀고용평등법 시행령": "L03", "남녀고용평등법": "L02", "고용보험법 시행령": "L05", "시행령": "L05", "고용보험법": "L04",
    "근로자퇴직급여 보장법 시행령": "L06", "산업안전보건법 시행규칙": "L08", "철도안전법 시행규칙": "L09",
    "민방위기본법": "L10", "근로기준법": "L01",
}

# 근거가 문서 이름만 있어 자동 매핑이 안 되는 문항: 조문을 사람이 찾아 지정
MANUAL = {
 "20": ["L08-0213", "L08-0214", "L08-0314", "r03-0042"],
 "24": ["r04-0012"],
 "29": ["r03-0025", "L10-0029"],
 "36": ["L04-0085", "L05-0144"],
 "51": ["r03-0058", "r03-0059", "L01-0086", "L01-0087", "L04-0091"],
 "74": ["L02-0015", "L08-0029", "L08-0263", "L08-0264"],
 "76": ["L08-0029", "L08-0263", "L08-0264"],
 "77": ["L09-0067", "L09-0068", "L09-0220", "L09-0221"],
 "101": ["r06-0001", "r06-F04"],
 "118": ["r13-0033", "r13-0034", "r13-0035", "r13-0036", "r13-0037", "r13-0038", "r13-0039", "r13-0040", "r03-0007", "r03-0008", "r03-0009"],
}

def load():
    chunks, by = [], defaultdict(list)
    names = {}
    for p in [REPO / "DB/data/regs/chunks.jsonl", REPO / "DB/data/laws/chunks.jsonl"]:
        for l in open(p, encoding="utf-8"):
            r = json.loads(l); chunks.append(r)
            names[r["doc_name"].replace(" ", "")] = r["document_id"]
            by[(r["document_id"], norm_art(r.get("article") or ""))].append(r)
    return chunks, by, names

def norm_art(a):
    a = a.replace(" ", "")
    a = re.sub(r"제(\d+)조의(\d+)", r"제\1의\2조", a)   # 법령식 제18조의2 → 제18의2조
    return a

def parse(basis, names):
    """'취업규칙 제26조·별표3, 근로기준법 제61조' → [(doc_id, [art...]), ...]"""
    out = []
    basis = re.sub(r"\([^)]*\)", "", basis.split("→")[0])
    for part in re.split(r",\s*", basis):
        part = part.strip()
        if not part:
            continue
        doc = None
        key = part.replace(" ", "")
        for alias, did in sorted(ALIAS.items(), key=lambda x: -len(x[0])):
            if key.startswith(alias.replace(" ", "")):
                doc, rest = did, key[len(alias.replace(" ", "")):]; break
        if doc is None:
            for nm, did in sorted(names.items(), key=lambda x: -len(x[0])):
                if key.startswith(nm):
                    doc, rest = did, key[len(nm):]; break
        if doc is None:
            out.append((None, [part])); continue
        rest = re.sub(r"제[\d·]+항", "", rest.replace("·시행령", ""))
        arts = []
        m = re.match(r"제(\d+)~(\d+)조", rest)
        if m:
            arts = [f"제{i}조" for i in range(int(m.group(1)), int(m.group(2)) + 1)]
        else:
            for t in re.split(r"[·]", rest):
                t = re.sub(r"제\d+(?:·\d+)*항$", "", re.sub(r"(제\d+(?:의\d+)?조)제[\d·]+항", r"\1", t))
                if not t: continue
                if t.startswith("별표") or t.startswith("별지"):
                    arts.append(re.sub(r"(별[표지])(\d+)", r"\1\2", t))
                elif t.startswith("부칙"):
                    arts.append(t)
                else:
                    arts.append(norm_art(t if t.startswith("제") else "제" + t))
        out.append((doc, arts))
    return out

def resolve(parsed, by):
    ids, missing = [], []
    for doc, arts in parsed:
        if doc is None:
            missing += arts; continue
        if not arts:
            missing.append(f"{doc}(조문 미지정)"); continue
        for a in arts:
            hit = by.get((doc, a))
            if hit:
                ids += [h["chunk_id"] for h in hit if h.get("searchable", True)] or [h["chunk_id"] for h in hit]
            else:
                missing.append(f"{doc} {a}")
    return list(dict.fromkeys(ids)), missing

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    chunks, by, names = load()
    rows = list(csv.DictReader(open(REPO / "질문셋/관리자_질문셋_v2.csv", encoding="utf-8-sig")))
    out = {}
    for r in rows:
        if not r["규정 근거"].strip():
            continue
        ids, miss = resolve(parse(r["규정 근거"], names), by)
        out[r["번호"]] = {"ids": ids, "missing": miss}
        if r["번호"] in MANUAL:
            ids, miss = MANUAL[r["번호"]], []
            out[r["번호"]] = {"ids": ids, "missing": [], "manual": True}
        print(r["번호"], r["규정 근거"][:50], "→", ids, ("MISSING " + str(miss)) if miss else "")
    json.dump(out, open(Path(__file__).with_name("basis_map.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
