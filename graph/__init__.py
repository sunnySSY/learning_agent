"""Agent 层：LangGraph 主图（PROJECT_PLAN 3.4 / 2.3）。

模块划分
    state.py     TaskState 与对外的 AgentResult
    nodes.py     六个节点：planner / retriever / solver / tutor / reviewer / clarify
    builder.py   路由函数、装图、run 与 run_stream

与 rag/ 和 tools/ 的分工：rag 负责「怎么把资料变成可检索的证据」，
tools 负责「单个工具怎么被安全地调用」，本包负责「一轮问答该走哪条路、
哪些节点参与、不合格怎么返工」。

日常只用本文件导出的这几个名字，其余属于实现细节。
"""

from .builder import build, compiled_graph, run, run_stream
from .state import AgentResult, TaskState

__all__ = [
    "run",
    "run_stream",
    "build",
    "compiled_graph",
    "AgentResult",
    "TaskState",
]
