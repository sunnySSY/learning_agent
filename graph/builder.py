"""装主图，以及对外入口 run / run_stream。

路由函数都放在这里，贴着驱动它们的 add_conditional_edges——图从上往下能一次读完。
它们是纯函数（只读 state，不碰 IO），所以 PROJECT_PLAN 7 要求的「状态路由」单测
直接从本模块导入即可。

图（对应 2.3 的端到端流程）：

    START -> planner -+- unclear ---------------------------> clarify -> END
                      +- chat（短路）--------> tutor -+
                      +- calc（不依赖资料）--> solver -+
                      +- 其他 -> retriever -+- 无证据+需工具 -> solver -+
                                            +- 无证据 --------> clarify  |
                                            +- 有证据+需工具 --> solver -+
                                            +- 有证据 -------> tutor ---+
                                                                       |
                                              tutor -> reviewer -+- 通过 -> END
                                                                 +- 打回 -> tutor
                                                                 +- 用尽 -> clarify -> END
"""

from collections.abc import Iterator
import re

from langgraph.graph import END, START, StateGraph

from config import cfg
from rag import format_locator
from tools.registry import ToolTrace

from .nodes import (
    clarify_node,
    planner_node,
    retriever_node,
    reviewer_node,
    solver_node,
    tutor_node,
)
from .state import (
    CLARIFY_AMBIGUOUS,
    CLARIFY_NO_EVIDENCE,
    CLARIFY_UNVERIFIED,
    AgentResult,
    TaskState,
)

# ---------------------------------------------------------------- 路由

def route_by_intent(state: TaskState) -> str:
    """Planner 之后。问题含糊就澄清，闲聊走短路，纯计算跳过检索，其余进检索。"""
    intent = state["plan"]["intent"]
    if intent == "unclear":
        return "clarify"
    if intent == "chat":
        # 短路：闲聊、道谢、问助手能力，不需要检索也没有可核的东西
        return "tutor"
    if intent == "calc":
        # 纯计算不依赖资料。走检索只会召回与题目毫不相关的分片，
        # 让 Tutor 把算出来的结果标上假引用 [1]，再被 Reviewer 正确地打回。
        # 跳过检索后 evidence 为空，最终结果里也就不会出现任何引用。
        return "solver"
    return "retriever"


def route_by_evidence(state: TaskState) -> str:
    """Retriever 之后。没证据但需要工具时，交给 Solver 用 web_search 兜底。"""
    if not state.get("evidence"):
        return "solver" if state["plan"]["needs_tools"] else "clarify"
    return "solver" if state["plan"]["needs_tools"] else "tutor"


def route_after_tutor(state: TaskState) -> str:
    """Tutor 之后。有证据或调过工具才有可核的东西，否则跳过 Reviewer。"""
    if state.get("evidence") or state.get("tool_results"):
        return "reviewer"
    return "done"


def route_after_review(state: TaskState) -> str:
    """Reviewer 之后。打回则重写，轮数用尽就降级为澄清（2.3 第 6 步）。"""
    if state["review"]["passed"]:
        return "done"
    if state.get("review_rounds", 0) >= cfg.review_max_rounds:
        return "clarify"
    return "again"


def _set_clarify(reason: str):
    """澄清节点有三种入口，用它把原因写进状态，让 clarify_node 知道该问什么。"""

    def mark(state: TaskState) -> dict:
        return {"clarify_reason": reason}

    return mark


# ---------------------------------------------------------------- 装图

def build():
    """编译主图。结果缓存在 build_graph() 里，一般不用直接调这个。"""
    builder = StateGraph(TaskState)

    builder.add_node("planner", planner_node)
    builder.add_node("retriever", retriever_node)
    builder.add_node("solver", solver_node)
    builder.add_node("tutor", tutor_node)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("clarify", clarify_node)

    # 澄清节点的三种入口：各配一个极小的标记节点，把原因写进状态。
    # 比让 clarify_node 去猜「我是被谁叫来的」清楚得多。
    builder.add_node("mark_ambiguous", _set_clarify(CLARIFY_AMBIGUOUS))
    builder.add_node("mark_no_evidence", _set_clarify(CLARIFY_NO_EVIDENCE))
    builder.add_node("mark_unverified", _set_clarify(CLARIFY_UNVERIFIED))

    builder.add_edge(START, "planner")
    builder.add_conditional_edges(
        "planner",
        route_by_intent,
        {
            "retriever": "retriever",
            "solver": "solver",
            "tutor": "tutor",
            "clarify": "mark_ambiguous",
        },
    )
    builder.add_conditional_edges(
        "retriever",
        route_by_evidence,
        {"solver": "solver", "tutor": "tutor", "clarify": "mark_no_evidence"},
    )
    builder.add_edge("solver", "tutor")
    builder.add_conditional_edges(
        "tutor", route_after_tutor, {"reviewer": "reviewer", "done": END}
    )
    builder.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"again": "tutor", "clarify": "mark_unverified", "done": END},
    )

    for marker in ("mark_ambiguous", "mark_no_evidence", "mark_unverified"):
        builder.add_edge(marker, "clarify")
    builder.add_edge("clarify", END)

    return builder.compile()


_GRAPH = None


def compiled_graph():
    """全局唯一的主图实例。编译一次就够，避免每次问答重装一遍。"""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build()
    return _GRAPH


# ---------------------------------------------------------------- 对外入口

def run_stream(
    question: str,
    history: list[dict] | None = None,
    user_id: str = "default",
    k: int = 4,
) -> Iterator[dict]:
    """跑一轮问答，逐个产出事件。

    事件形状：
        {"type": "node", "name": "retriever", "label": "召回 4 个分片"}
        {"type": "done", "result": AgentResult}

    custom 事件由各节点的 get_stream_writer() 发出（显示文案在节点内部），
    values 只取最后一条作为终态来组装最终结果。
    """
    state: TaskState = {
        "question": question,
        "user_id": user_id,
        "k": k,
        "history": list(history or []),
    }

    final: dict = dict(state)
    steps = 0

    for mode, chunk in compiled_graph().stream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            steps += 1  # 每个节点恰好发一条事件，事件数即节点数
            yield chunk
        else:
            final = chunk  # values 每步回传全量状态，只要最后一条

    yield {"type": "done", "result": _to_result(final, steps)}


def run(
    question: str,
    history: list[dict] | None = None,
    user_id: str = "default",
    k: int = 4,
) -> AgentResult:
    """非流式入口，签名与改造前的 planner.run 完全一致。

    给 evaluation 和不关心进度的调用方留一条简单路径。
    """
    result = AgentResult(answer="")
    for event in run_stream(question, history=history, user_id=user_id, k=k):
        if event["type"] == "done":
            result = event["result"]
    return result


def _to_result(state: dict, steps: int) -> AgentResult:
    """把终态组装成对外的 AgentResult。"""
    docs, citations = _cited_only(
        state.get("evidence") or [], state.get("draft_answer") or ""
    )

    # 状态里存的是 asdict 后的纯数据，这里还原成 ToolTrace，保住 line() 的渲染
    traces = [ToolTrace(**d) for d in state.get("tool_results") or []]

    return AgentResult(
        answer=state.get("draft_answer") or "",
        docs=docs,
        citations=citations,
        traces=traces,
        steps=steps,
        hit_tool_limit=bool(state.get("hit_tool_limit")),
        trace_path=state.get("trace_path") or "",
    )


# 正文里的引用标记，如 [1] [2]
_CITE_RE = re.compile(r"\[(\d+)\]")


def _cited_only(docs: list, answer: str) -> tuple[list, list[str]]:
    """只保留正文真正引用过的分片，返回 (分片, 引用文案)。

    检索回来的分片未必都被用上。原先一律把检索到的全部列成引用，于是会出现
    「正文一个 [n] 都没标，下面却列了 4 条引用」——看起来像答案有据可依，
    实际那 4 条跟问题毫无关系。现在没被引用就不显示。

    编号必须**沿用 format_context 的检索顺序编号 1..N**，所以引用文案在这里自己拼，
    不能把过滤后的列表交给 format_citations——那会从 1 重新编号，
    正文里的 [3] 就被标成了 [1]，指到另一个分片上。
    """
    if not docs:
        return [], []

    used = {int(n) for n in _CITE_RE.findall(answer)}
    kept = [(i, d) for i, d in enumerate(docs, start=1) if i in used]
    return [d for _, d in kept], [
        f"[{i}] {format_locator(d.metadata)}" for i, d in kept
    ]
