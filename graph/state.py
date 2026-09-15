"""图的状态定义与对外结果契约。

对应 PROJECT_PLAN 4.1 的 `TaskState`，有三处有意的偏离，都写在下面字段注释里。

状态里只放**可序列化的纯数据**（trace 存 asdict 后的 dict 而不是 ToolTrace 对象），
这样 Phase 4 接 thread checkpointer 时不用返工。
"""

from dataclasses import dataclass, field
from typing import TypedDict

from langchain_core.documents import Document


class TaskState(TypedDict, total=False):
    """在主图各节点之间流动的共享状态。

    偏离 4.1 的三处：

    - `evidence` 是 `list[Document]` 而非 `list[dict]`。rag 的 format_context /
      format_citations / format_locator 三个函数都吃 Document，存 Document 才能
      零改动复用它们；存 dict 就要给三个函数各写一层适配。
    - `history` 是 `list[dict]`（role/content）而非 `list[BaseMessage]`，
      直接对接 memory.Memory.history()。messages 归 Phase 4 的 checkpointer 管。
    - 不加 `thread_id` / `image_context`：前者属 Phase 4，后者属 Phase 5，
      现在没有任何节点读写，加上去就是死代码。
    """

    # ---- 输入 ----
    question: str
    user_id: str
    k: int
    history: list[dict]

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
