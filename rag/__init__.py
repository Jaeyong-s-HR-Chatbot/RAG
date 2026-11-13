"""누리철도 HR 챗봇 규정 검색(RAG) 베이스라인.

    from rag import Pipeline, load_config, split_question
    pipe = Pipeline(load_config())
    r = pipe.run("결혼하면 휴가 며칠이고 경조금 얼마야?")
"""

from .config import PRESETS, Config, load_config
from .pipeline import Pipeline, PipelineResult, SubResult
from .split import split_question

__all__ = ["PRESETS", "Config", "load_config", "Pipeline", "PipelineResult", "SubResult", "split_question"]
