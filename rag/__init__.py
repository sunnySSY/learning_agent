"""RAG 包：加载 → 切分 → 向量化 → 入库 → 检索 → 引用。

模块划分
    loader.py     文件加载，产出带定位信息的 Document
    splitter.py   切分，保留标题路径 / 行号
    embedder.py   阿里云百炼 embedding 封装（批量与维度约束都封在这一层）
    store.py      本地 Chroma 向量库
    retriever.py  检索与引用格式化
    pipeline.py   把上面几步串成 index / ask 入口

日常只用本文件导出的这几个名字，其余属于实现细节。
"""

from .pipeline import ask, index_dir, index_files, index_path
from .retriever import format_citations, format_context, retrieve
from .store import reset, stats

__all__ = [
    "ask",
    "index_path",
    "index_files",
    "index_dir",
    "retrieve",
    "format_context",
    "format_citations",
    "reset",
    "stats",
]
