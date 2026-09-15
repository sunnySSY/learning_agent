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
    # 入库清单：记录每个文件的内容哈希，让重复入库变成幂等操作（见 rag/manifest.py）
    manifest_path: Path = field(
        default_factory=lambda: Path(_opt("INDEX_MANIFEST", "./data/index_manifest.json"))
    )

    # LangSmith
    langsmith_project: str = field(
        default_factory=lambda: _opt("LANGSMITH_PROJECT", "learning_agent")
    )
    langsmith_tracing: bool = field(
        default_factory=lambda: _opt("LANGSMITH_TRACING", "false").lower() == "true"
    )

    # ---- Phase 2：工具 ----
    # 留空则联网搜索工具自动禁用
    tavily_api_key: str = field(default_factory=lambda: _opt("TAVILY_API_KEY", ""))
    tavily_max_results: int = field(
        default_factory=lambda: int(_opt("TAVILY_MAX_RESULTS", "5"))
    )
    tavily_search_depth: str = field(
        default_factory=lambda: _opt("TAVILY_SEARCH_DEPTH", "basic")
    )

    # 工具调用的三道闸
    tool_timeout: float = field(default_factory=lambda: float(_opt("TOOL_TIMEOUT", "15")))
    tool_max_retries: int = field(default_factory=lambda: int(_opt("TOOL_MAX_RETRIES", "2")))
    max_tool_calls: int = field(default_factory=lambda: int(_opt("MAX_TOOL_CALLS", "6")))

    # 工具产物
    flashcard_dir: Path = field(
        default_factory=lambda: Path(_opt("FLASHCARD_DIR", "./data/flashcards"))
    )
    trace_dir: Path = field(default_factory=lambda: Path(_opt("TRACE_DIR", "./data/traces")))

    # ---- Phase 3：多 Agent 图 ----
    # Reviewer 的审核总轮数（含首次）。第 N 次仍不通过就降级为澄清节点。
    # 每多一轮就多一次模型往返，直接吃掉延迟预算，别调大。
    review_max_rounds: int = field(
        default_factory=lambda: int(_opt("REVIEW_MAX_ROUNDS", "2"))
    )

    @property
    def web_search_enabled(self) -> bool:
        return bool(self.tavily_api_key)


cfg = Config()
