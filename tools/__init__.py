"""工具包。

模块划分
    registry.py    执行护栏：超时、重试、调用次数上限、trace 记录
    calculator.py  安全数学计算
    web_search.py  Tavily 联网搜索（未配置 key 时自动禁用）
    flashcard.py   生成并保存复习卡片

计划书 3.3 要求所有工具调用都记录名称、参数摘要、耗时和结果状态，
这件事由 registry 统一做，各个工具自己不用管。
"""

from .registry import ToolBudget, ToolError, ToolTracer, build_tools


__all__ = ["build_tools", "ToolBudget", "ToolError", "ToolTracer"]
