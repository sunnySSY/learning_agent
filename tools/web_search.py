"""Tavily 联网搜索。

用标准库 urllib 直接发请求，不额外引入 tavily-python 依赖。
未配置 TAVILY_API_KEY 时整个工具不会被装配（见 registry.build_tools）。
"""

import json
import urllib.error
import urllib.request

from config import cfg
from .registry import ToolError

WEB_SEARCH_DESC = (
    "联网搜索最新信息。只在本地资料回答不了、或问题有时效性时使用，"
    "例如最近发生的事、当前版本号、新发布的工具。"
    "本地资料里能查到的问题不要用它，优先依据资料回答。"
)

_ENDPOINT = "https://api.tavily.com/search"


def web_search(query: str) -> str:
    """搜索互联网并返回若干条结果的标题、链接和摘要。"""
    query = (query or "").strip()
    if not query:
        raise ToolError("搜索词为空", retryable=False)

    payload = {
        "api_key": cfg.tavily_api_key,
        "query": query,
        "max_results": cfg.tavily_max_results,
        "search_depth": cfg.tavily_search_depth,
        "include_answer": False,
    }
    request = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=cfg.tool_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:200]
        except Exception:  # noqa: BLE001
            pass
        # 401/432 是 key 无效或额度耗尽，重试多少次都一样，直接判定为不可重试
        if exc.code in (401, 403, 432):
            raise ToolError(
                f"Tavily 认证失败或额度用尽（HTTP {exc.code}）{detail}",
                retryable=False,
            ) from exc
        # 429 限流和 5xx 是暂时性的，值得重试
        raise ToolError(f"Tavily 返回 HTTP {exc.code} {detail}",
                        retryable=exc.code == 429 or exc.code >= 500) from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"无法连接 Tavily: {exc.reason}") from exc

    results = data.get("results") or []
    if not results:
        return f"没有搜索到「{query}」的相关结果。"

    lines = [f"「{query}」的搜索结果（共 {len(results)} 条）："]
    for i, item in enumerate(results, start=1):
        title = item.get("title", "无标题")
        url = item.get("url", "")
        content = (item.get("content") or "").strip().replace("\n", " ")
        if len(content) > 400:
            content = content[:400] + "…"
        lines.append(f"\n[{i}] {title}\n    {url}\n    {content}")

    return "\n".join(lines)
