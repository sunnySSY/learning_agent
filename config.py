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
    checkpoint_db: Path = field(
        default_factory=lambda: Path(_opt("CHECKPOINT_DB", "./data/memory/checkpoints.sqlite"))
    )
    learning_db: Path = field(
        default_factory=lambda: Path(_opt("LEARNING_DB", "./data/memory/learning.sqlite"))
    )
    user_timezone: str = field(default_factory=lambda: _opt("USER_TIMEZONE", "Asia/Shanghai"))
    memory_auto_extract: bool = field(
        default_factory=lambda: _opt("MEMORY_AUTO_EXTRACT", "true").lower() == "true"
    )
    memory_auto_confirm_threshold: float = field(
        default_factory=lambda: float(_opt("MEMORY_AUTO_CONFIRM_THRESHOLD", "0.90"))
    )
    memory_pending_threshold: float = field(
        default_factory=lambda: float(_opt("MEMORY_PENDING_THRESHOLD", "0.70"))
    )
    review_daily_minutes: int = field(
        default_factory=lambda: max(1, int(_opt("REVIEW_DAILY_MINUTES", "30")))
    )
    memory_history_max_messages: int = field(
        default_factory=lambda: max(2, int(_opt("MEMORY_HISTORY_MAX_MESSAGES", "20")))
    )
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

    # ---- Phase 5：服务与产品化 ----
    app_env: str = field(default_factory=lambda: _opt("APP_ENV", "development"))
    api_host: str = field(default_factory=lambda: _opt("API_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: int(_opt("API_PORT", "8000")))
    api_bearer_tokens: str = field(default_factory=lambda: _opt("API_BEARER_TOKENS", ""))
    auth_mode: str = field(default_factory=lambda: _opt("AUTH_MODE", "dev"))
    auth_issuer: str = field(default_factory=lambda: _opt("AUTH_ISSUER", ""))
    auth_audience: str = field(default_factory=lambda: _opt("AUTH_AUDIENCE", ""))
    auth_jwks_url: str = field(default_factory=lambda: _opt("AUTH_JWKS_URL", ""))
    auth_secret: str = field(default_factory=lambda: _opt("AUTH_SECRET", ""))
    product_db: Path = field(
        default_factory=lambda: Path(_opt("PRODUCT_DB", "./data/product.sqlite"))
    )
    product_upload_dir: Path = field(
        default_factory=lambda: Path(_opt("PRODUCT_UPLOAD_DIR", "./data/product_uploads"))
    )
    product_image_dir: Path = field(
        default_factory=lambda: Path(_opt("PRODUCT_IMAGE_DIR", "./data/product_images"))
    )
    file_max_bytes: int = field(
        default_factory=lambda: int(_opt("FILE_MAX_BYTES", str(25 * 1024 * 1024)))
    )
    file_quota_bytes: int = field(
        default_factory=lambda: int(_opt("FILE_QUOTA_BYTES", str(1024 * 1024 * 1024)))
    )
    image_max_bytes: int = field(
        default_factory=lambda: int(_opt("IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))
    )
    image_max_count: int = field(default_factory=lambda: int(_opt("IMAGE_MAX_COUNT", "4")))
    image_max_edge: int = field(default_factory=lambda: int(_opt("IMAGE_MAX_EDGE", "4096")))
    image_ttl_hours: int = field(default_factory=lambda: int(_opt("IMAGE_TTL_HOURS", "24")))
    rate_api_user_per_minute: int = field(default_factory=lambda: int(_opt("RATE_API_USER_PER_MINUTE", "120")))
    rate_api_ip_per_minute: int = field(default_factory=lambda: int(_opt("RATE_API_IP_PER_MINUTE", "300")))
    rate_chat_user_per_minute: int = field(default_factory=lambda: int(_opt("RATE_CHAT_USER_PER_MINUTE", "10")))
    rate_chat_ip_per_minute: int = field(default_factory=lambda: int(_opt("RATE_CHAT_IP_PER_MINUTE", "30")))
    max_chat_concurrency: int = field(default_factory=lambda: int(_opt("MAX_CHAT_CONCURRENCY", "2")))
    max_vision_concurrency: int = field(default_factory=lambda: int(_opt("MAX_VISION_CONCURRENCY", "1")))
    index_max_attempts: int = field(default_factory=lambda: int(_opt("INDEX_MAX_ATTEMPTS", "3")))
    index_daily_limit: int = field(default_factory=lambda: int(_opt("INDEX_DAILY_LIMIT", "20")))
    embedding_daily_token_limit: int = field(default_factory=lambda: int(_opt("EMBEDDING_DAILY_TOKEN_LIMIT", "2000000")))
    otel_endpoint: str = field(default_factory=lambda: _opt("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
    cors_origins: str = field(default_factory=lambda: _opt("CORS_ORIGINS", "http://localhost:8000"))
    service_version: str = field(default_factory=lambda: _opt("SERVICE_VERSION", "phase5-dev"))
    service_langsmith_tracing: bool = field(default_factory=lambda: _opt("SERVICE_LANGSMITH_TRACING", "false").lower() == "true")

    @property
    def web_search_enabled(self) -> bool:
        return bool(self.tavily_api_key)

    def validate_service(self) -> None:
        """Validate deployment-sensitive combinations without printing secrets."""
        if self.app_env not in {"development", "test", "staging", "production"}:
            raise RuntimeError("APP_ENV 必须是 development、test、staging 或 production")
        if self.auth_mode not in {"dev", "token", "jwt"}:
            raise RuntimeError("AUTH_MODE 必须是 dev、token 或 jwt")
        if self.app_env == "production" and self.auth_mode == "dev":
            raise RuntimeError("生产环境禁止 AUTH_MODE=dev")
        if self.app_env == "production" and "*" in {item.strip() for item in self.cors_origins.split(",")}:
            raise RuntimeError("携带凭证的生产 CORS_ORIGINS 禁止使用 *")
        if self.auth_mode == "jwt" and not self.auth_secret and not self.auth_jwks_url:
            raise RuntimeError("AUTH_MODE=jwt 时必须配置 AUTH_SECRET 或 AUTH_JWKS_URL")
        if self.file_max_bytes <= 0 or self.file_quota_bytes < self.file_max_bytes:
            raise RuntimeError("FILE_MAX_BYTES 与 FILE_QUOTA_BYTES 配置无效")
        if not 1 <= self.image_max_count <= 4 or self.image_max_edge <= 0:
            raise RuntimeError("图片配额配置无效")


cfg = Config()
