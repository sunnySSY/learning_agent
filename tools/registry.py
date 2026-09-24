"""工具注册与执行护栏。

对应 PROJECT_PLAN 3.3：工具要有 schema、超时、重试、调用次数限制，
每次调用记录名称、参数摘要、耗时和结果状态。

三道闸都在这里，各工具自己不重复实现：
    ToolBudget   单轮对话的调用次数上限，防止 Agent 无限循环
    超时         用线程池包一层（Windows 上没有 signal.alarm，只能用这个办法）
    重试         失败后重试，仍有次数上限

超时的一个已知局限：超时后线程不会被强杀，只是调用方不再等待。
对联网搜索这类请求影响不大，但如果某个工具会改文件，要注意它可能仍在后台跑完。
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import wraps
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from config import cfg

# 模块级线程池，避免每次调用都新建
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")

# 参数摘要里每个值最多留多少字符
_ARG_PREVIEW = 60


class ToolError(RuntimeError):
    """工具自己抛的错误。

    retryable=False 用于「再试多少次都一样」的失败，比如认证失败、参数非法。
    这类错误不重试，直接返回，省掉无谓的等待。
    """

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class ToolTrace:
    """一次工具调用的记录。"""

    name: str
    args: dict
    status: str  # ok / error / timeout / rejected
    duration: float
    attempts: int = 1
    result_chars: int = 0
    preview: str = ""
    error: str = ""
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def line(self) -> str:
        """给终端看的一行摘要。"""
        args = ", ".join(f"{k}={_short(v)}" for k, v in self.args.items())
        icon = {"ok": "✓", "error": "✗", "timeout": "⏱", "rejected": "⊘"}.get(self.status, "?")
        text = f"{icon} {self.name}({args}) {self.duration:.2f}s"
        if self.attempts > 1:
            text += f" 重试{self.attempts - 1}次"
        if self.error:
            text += f" → {self.error[:60]}"
        return text


def _short(value, limit: int = _ARG_PREVIEW) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


class ToolBudget:
    """单轮对话的工具调用次数上限。"""
    # 这是全局额度，不区分工具。如果模型连着调 10 次 calculator，就把 flashcard 的额度也耗光了。要按工具限额需要把 used 改成 dict[str,


    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.used = 0

    def consume(self) -> bool:
        """还有额度就扣一个并返回 True，用完了返回 False。"""
        if self.used >= self.max_calls:
            return False
        self.used += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)


class ToolTracer:
    """收集工具调用记录，可落盘成 JSONL。"""

    def __init__(self, trace_dir: Path | None = None, *, redact: bool = False):
        self.records: list[ToolTrace] = []
        self.trace_dir = trace_dir or cfg.trace_dir
        self.redact = redact

    def add(self, trace: ToolTrace) -> None:
        self.records.append(trace)

    def save(self, run_id: str) -> Path | None:
        """追加写入 data/traces/<run_id>.jsonl。写不进去也不影响主流程。"""
        if not self.records:
            return None
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            path = self.trace_dir / f"{run_id}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                for trace in self.records:
                    payload = asdict(trace)
                    if self.redact:
                        payload["args"] = {key: "[redacted]" for key in payload["args"]}
                        payload["preview"] = ""
                        payload["error"] = "[redacted]" if payload["error"] else ""
                    fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return path
        except OSError as exc:
            print(f"[trace] 写入失败: {exc}")
            return None

    @property
    def failed(self) -> list[ToolTrace]:
        return [t for t in self.records if t.status != "ok"]


def _call_with_timeout(fn, kwargs: dict, timeout: float):
    future = _EXECUTOR.submit(fn, **kwargs)
    return future.result(timeout=timeout)


def guard(
    fn,
    *,
    name: str,
    budget: ToolBudget,
    tracer: ToolTracer,
    description: str,
) -> StructuredTool:
    """把一个普通函数包成受护栏约束的 LangChain 工具。

    失败时不抛异常，而是把错误信息当作工具返回值交回模型——
    这样模型有机会自己换个问法重试，而不是整个对话崩掉。

    @wraps 不能省：StructuredTool 靠 inspect.signature 推断参数 schema，
    而 inspect.signature 会跟随 __wrapped__。少了它，签名只剩 **kwargs，
    模型就看不到任何参数，工具等于废掉。
    """

    @wraps(fn)
    def wrapper(**kwargs) -> str:
        if not budget.consume():
            tracer.add(
                ToolTrace(
                    name=name,
                    args=kwargs,
                    status="rejected",
                    duration=0.0,
                    error=f"已达调用上限 {budget.max_calls} 次",
                )
            )
            return (
                f"[已达工具调用上限 {budget.max_calls} 次，本次调用被拒绝。"
                f"请基于已有信息直接给出最终回答。]"
            )

        attempts = 0
        last_error = ""
        started = time.perf_counter()

        for attempt in range(1, cfg.tool_max_retries + 2):
            attempts = attempt
            try:
                result = _call_with_timeout(fn, kwargs, cfg.tool_timeout)
                text = str(result)
                tracer.add(
                    ToolTrace(
                        name=name,
                        args=kwargs,
                        status="ok",
                        duration=time.perf_counter() - started,
                        attempts=attempts,
                        result_chars=len(text),
                        preview=_short(text, 120),
                    )
                )
                return text
            except FutureTimeout:
                last_error = f"超时（>{cfg.tool_timeout}s）"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                # 认证失败、参数非法这类错误重试没意义，立刻收手
                if getattr(exc, "retryable", True) is False:
                    break

        tracer.add(
            ToolTrace(
                name=name,
                args=kwargs,
                status="timeout" if "超时" in last_error else "error",
                duration=time.perf_counter() - started,
                attempts=attempts,
                error=last_error,
            )
        )
        return f"[工具 {name} 调用失败：{last_error}。请换一种方式，或基于已有信息回答。]"

    return StructuredTool.from_function(
        func=wrapper,
        name=name,
        description=description,
    )


def build_tools(budget: ToolBudget, tracer: ToolTracer, user_id: str = "default") -> list[BaseTool]:
    """装配本次运行可用的工具列表。

    联网搜索需要 key，没配就不装配，避免模型调用一个注定失败的工具。
    """
    from .calculator import CALCULATOR_DESC, calculate
    from .flashcard import FLASHCARD_DESC, make_flashcards
    from .web_search import WEB_SEARCH_DESC, web_search

    def user_flashcards(topic: str, content: str, count: int = 5) -> str:
        return make_flashcards(topic, content, count, user_id=user_id)

    tools = [
        guard(calculate, name="calculate", budget=budget, tracer=tracer,
              description=CALCULATOR_DESC),
        guard(user_flashcards, name="make_flashcards", budget=budget, tracer=tracer,
              description=FLASHCARD_DESC),
    ]

    if cfg.web_search_enabled:
        tools.append(
            guard(web_search, name="web_search", budget=budget, tracer=tracer,
                  description=WEB_SEARCH_DESC)
        )

    return tools


def describe_tools() -> list[str]:
    """列出当前可用的工具，供 /tools 命令展示。

    只为读名字，所以额度、重试这些都不关心；tracer 随用随弃。
    """
    budget, tracer = ToolBudget(cfg.max_tool_calls), ToolTracer()
    return [f"{t.name} — {t.description.splitlines()[0]}" for t in build_tools(budget, tracer)]
