"""goldenset 저장소(contracts/, router/)를 불러온다.

데이터 형식(ChunkHit, LlmRequest)과 build_messages, 라우터는 goldenset 저장소에 있는 걸 그대로 쓴다.
위치는 환경변수 GOLDENSET_DIR (없으면 ../goldenset, /content/goldenset 순서로 찾음).
"""

from __future__ import annotations

import sys

from .config import load_config

_dir = load_config().goldenset_dir
if not (_dir / "contracts" / "schemas.py").is_file():
    raise ImportError(
        f"goldenset 저장소를 못 찾았어요: {_dir}\n"
        "  git clone https://github.com/Jaeyong-s-HR-Chatbot/goldenset 한 뒤 "
        "os.environ['GOLDENSET_DIR'] = '<그 경로>' 로 알려 주세요."
    )
if str(_dir) not in sys.path:
    sys.path.insert(0, str(_dir))

from contracts.pipeline import (  # noqa: E402
    SYSTEM_PROMPT, build_messages, chunk_hit_from_row, parse_llm_answer, to_chat_response,
)
from contracts.schemas import ChunkHit, LlmRequest  # noqa: E402
from router import Route, route  # noqa: E402

__all__ = [
    "SYSTEM_PROMPT", "build_messages", "chunk_hit_from_row", "parse_llm_answer", "to_chat_response",
    "ChunkHit", "LlmRequest", "Route", "route",
]
