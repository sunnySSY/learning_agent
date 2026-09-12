"""配置。从 .env 读取。

注意：这里的 load_dotenv() 必须在导入 langchain 之前执行，
LangSmith 只在创建 tracer 的那一刻读 LANGSMITH_* 变量，晚一步设上就没有 trace。
所以任何入口文件都要先 `import config`。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _req(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f".env 里缺少必填项 {key}")
    return value


def _opt(key: str, default: str) -> str:
    value = os.getenv(key)
    # .env 里 ${XXX} 这种引用写法解析失败时会原样留下，当作没填
    if not value or value.startswith("${"):
        return default
    return value


@dataclass(frozen=True)
class Config:
    # 阿里云百炼 —— 对话模型
    api_key: str = field(default_factory=lambda: _req("DASHSCOPE_API_KEY"))
    base_url: str = field(default_factory=lambda: _req("DASHSCOPE_BASE_URL"))
    llm_model: str = field(default_factory=lambda: _opt("LLM_MODEL", "qwen-plus"))
    temperature: float = field(default_factory=lambda: float(_opt("LLM_TEMPERATURE", "0.7")))

    # 阿里云百炼 —— Embedding
    # 专属部署端点通常不挂 embedding，所以单独配一套 key / 地址
    embed_api_key: str = field(
        default_factory=lambda: _opt("EMBEDDING_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    )
    embed_base_url: str = field(
        default_factory=lambda: _opt(
            "EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    )
    embed_model: str = field(
        default_factory=lambda: _opt("EMBEDDING_MODEL", "text-embedding-v4")
    )
    embed_dim: int = field(default_factory=lambda: int(_opt("EMBEDDING_DIMENSION", "1024")))

    # 本地存储
    chroma_dir: Path = field(
        default_factory=lambda: Path(_opt("CHROMA_PERSIST_DIR", "./data/chroma"))
    )
    collection: str = field(default_factory=lambda: _opt("COLLECTION_NAME", "study_helper"))
    data_dir: Path = field(default_factory=lambda: Path(_opt("DATA_DIR", "./data/uploads")))
    memory_dir: Path = field(default_factory=lambda: Path("./data/memory"))

    # LangSmith
    langsmith_project: str = field(
        default_factory=lambda: _opt("LANGSMITH_PROJECT", "learning_agent")
    )
    langsmith_tracing: bool = field(
        default_factory=lambda: _opt("LANGSMITH_TRACING", "false").lower() == "true"
    )


cfg = Config()
