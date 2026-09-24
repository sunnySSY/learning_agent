"""检索与引用格式化。

引用按资料格式分别标注（用户选定的方案）：
- PDF      -> 文件名 + 页码
- Markdown -> 文件名 + 标题路径，如「讲义.md > 第3章 > 3.2 特征值」
- TXT      -> 文件名 + 行号区间
"""

from langchain_core.documents import Document

from .store import vectorstore

DEFAULT_K = 4


def retrieve(query: str, k: int = DEFAULT_K, user_id: str | None = None,
             active_versions: dict[str, int] | None = None) -> list[Document]:
    """向量召回，并可限制到 Web 文件的 active 版本。"""
    kwargs: dict = {"k": k}
    if user_id:
        if active_versions is not None:
            if not active_versions:
                # Product users with no active file versions must not retrieve
                # stale/orphan vectors that happen to carry the same user ID.
                kwargs["filter"] = {"$and": [{"user_id": user_id}, {"file_id": "__no_active_file__"}]}
                return vectorstore().similarity_search(query, **kwargs)
            clauses = [
                {"$and": [{"user_id": user_id}, {"file_id": file_id}, {"index_version": version}]}
                for file_id, version in active_versions.items()
            ]
            kwargs["filter"] = clauses[0] if len(clauses) == 1 else {"$or": clauses}
        else:
            kwargs["filter"] = {"user_id": user_id}
    return vectorstore().similarity_search(query, **kwargs)


def format_locator(meta: dict) -> str:
    """把元数据渲染成人类可读的位置串。"""
    name = meta.get("file_name", "未知文件")
    file_type = meta.get("file_type")

    if file_type == "pdf" and isinstance(meta.get("page"), int):
        return f"{name} 第{meta['page'] + 1}页"  # 内部 0 基，展示时 +1

    if file_type == "markdown" and meta.get("heading_path"):
        return f"{name} > {meta['heading_path']}"

    if file_type == "text" and meta.get("line_start"):
        end = meta.get("line_end", meta["line_start"])
        return f"{name} 第{meta['line_start']}-{end}行"

    return name


def format_context(docs: list[Document]) -> str:
    """拼成给模型看的上下文，编号与引用一一对应。"""
    return "\n\n".join(
        f"[{i}] 来源：{format_locator(d.metadata)}\n{d.page_content}"
        for i, d in enumerate(docs, start=1)
    )


def format_citations(docs: list[Document]) -> list[str]:
    """拼成给用户看的引用列表。"""
    return [f"[{i}] {format_locator(d.metadata)}" for i, d in enumerate(docs, start=1)]
