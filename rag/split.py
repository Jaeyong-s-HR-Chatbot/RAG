"""① 질문 쪼개기 (규칙, LLM 안 씀).

"결혼하면 휴가 며칠이고 경조금 얼마야?" → ["결혼하면 휴가 며칠인가요?", "결혼하면 경조금 얼마야?"]
"복지포인트는 언제 들어오고 어디서 쓸 수 있어요?" → ["복지포인트는 언제 들어오나요?", "복지포인트는 어디서 쓸 수 있어요?"]
"5일 썼는데, 며칠까지 돼요?" → 그대로 (앞 절은 상황 설명이지 따로 묻는 게 아님)

규칙
- '…고 ' / '…이고 ' / '그리고' 로 이어진 두 절이 **둘 다 의문사(며칠·얼마·언제·어디·어떻게 …)** 를 가질 때만 나눈다.
- 앞 절 맨 앞의 조건("결혼하면", "출산 후에")·주제("복지포인트는")는 뒤 절에 없으면 이어 붙인다.
"""

from __future__ import annotations

import re

WH = re.compile(r"(며칠|몇|얼마|언제|어디|어떻게|어떤|무엇|뭐|뭘|누가|누구|왜|있어|있나|되나|돼|가능|나와|받아|받나)")
# 연결: "며칠이고 ", "들어오고 ", "그리고 ", "…하고 "
_JOIN = re.compile(r"(?<=[가-힣])(이고|고)\s+|\s*그리고\s+|\s*,\s*그리고\s+")
_COND = re.compile(r"^(\S+(?:하면|되면|이면|으면|면|때|후에|전에|중에|경우))\s+")
_TOPIC = re.compile(r"^(\S+(?:은|는))\s+")


def _close(head: str, joiner: str) -> str:
    """'며칠이' + '고' → '며칠인가요?', '들어오' + '고' → '들어오나요?'"""
    head = head.rstrip(" ,")
    if joiner == "이고":
        return head + "인가요?"
    if joiner == "고":
        return head + "나요?"
    return head if head.endswith("?") else head + "?"


def _carry(first: str, second: str) -> str:
    """앞 절의 조건·주제를 뒤 절에 이어 붙인다 (뒤 절에 이미 있으면 안 붙임)."""
    prefix = []
    rest = first
    for pat in (_COND, _TOPIC):
        m = pat.match(rest)
        if m:
            prefix.append(m.group(1))
            rest = rest[m.end():]
    if not prefix:
        return second
    if _COND.match(second) or _TOPIC.match(second):
        return second
    return " ".join(prefix) + " " + second


def split_question(q: str) -> list[str]:
    q = re.sub(r"\s+", " ", q.strip())
    m = _JOIN.search(q)
    if not m:
        return [q]
    head, tail = q[: m.start()], q[m.end():]
    joiner = (m.group(1) or "그리고").strip()
    if not (WH.search(head) and WH.search(tail)) or len(tail) < 3:
        return [q]
    first = _close(head, joiner)
    second = _carry(first, tail)
    return [first] + split_question(second)[: 3]   # 최대 4개
