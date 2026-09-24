"""图的状态定义与对外结果契约。

对应 PROJECT_PLAN 4.1 的 `TaskState`，有三处有意的偏离，都写在下面字段注释里。

messages 和 Document 由 saver 序列化，工具 trace 保存为 asdict 后的 dict。
当前 SQLite saver 的消息、证据往返已通过离线集成测试。
"""

from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class TaskState(TypedDict, total=False):
    """在主图各节点之间流动的共享状态。

    偏离 4.1 的三处：

    - `evidence` 是 `list[Document]` 而非 `list[dict]`。rag 的 format_context /
      format_citations / format_locator 三个函数都吃 Document，存 Document 才能
      零改动复用它们；存 dict 就要给三个函数各写一层适配。
    - `history` 仅为旧调用方的非持久化兼容输入。
    - `messages` 由 checkpointer 跨轮保存；thread_id 是注册表生成的 UUID。
      image_context 只保存结构化的图片识别上下文，不保存原始图片字节。
    """

    # ---- 输入 ----
    question: str
    user_id: str
    k: int
    history: list[dict]
    messages: Annotated[list[AnyMessage], add_messages]
    thread_id: str
    turn_id: str
    memory_context: str
    image_context: dict
    active_versions: dict[str, int]

    # ---- Planner ----
    plan: dict  # {intent, needs_tools, steps[], reason}

    # ---- Retriever ----
    evidence: list[Document]

    # ---- Solver ----
    draft_answer: str
    tool_results: list[dict]  # asdict(ToolTrace)
    trace_path: str
    hit_tool_limit: bool

    # ---- Reviewer ----
    review: dict | None  # {passed, issues[], suggestion}
    review_rounds: int

    # ---- 出口 ----
    clarify_reason: str  # "" / "ambiguous" / "no_evidence" / "unverified"
    steps: int


# 澄清节点的三种触发原因，作为 clarify_reason 的取值集合
CLARIFY_AMBIGUOUS = "ambiguous"      # Planner 判定问题本身不清楚
CLARIFY_NO_EVIDENCE = "no_evidence"  # 本地资料里没检索到，且无需联网
CLARIFY_UNVERIFIED = "unverified"    # Reviewer 连续打回、重试次数用尽


@dataclass
class AgentResult:
    """一次问答的完整结果。

    字段与改造前 planner.AgentResult 完全一致，main.py 的渲染逻辑因此不用动。
    """

    answer: str
    docs: list = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    traces: list = field(default_factory=list)
    steps: int = 0
    hit_tool_limit: bool = False
    trace_path: str = ""
    turn_id: str = ""
    thread_id: str = ""
    memory_status: str = "disabled"
