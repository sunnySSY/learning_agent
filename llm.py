"""对话模型工厂。

Embedding 不在这里——它有一堆阿里云特有的约束，封装在 rag/embedder.py。
"""

from functools import lru_cache

from langchain_openai import ChatOpenAI

from config import cfg


@lru_cache(maxsize=1)
def chat_model() -> ChatOpenAI:
    """主力对话模型，按 .env 指向的端点创建。"""
    return ChatOpenAI(
        model=cfg.llm_model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
    )
