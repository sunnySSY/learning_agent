"""切分。按格式选策略，切完给每个分片贴上位置信息，供引用标注使用。

- Markdown：先按标题结构切，再按长度细分，保留 heading_path（如「第3章 > 3.2 特征值」）
- PDF     ：按长度切，page 由 loader 给出
- TXT     ：按长度切，另算出 line_start / line_end
"""

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

# 中文大致 1 字 ≈ 1.5 token，800 字约 500-550 token，落在计划书要求的 500-800 区间
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

# 从粗到细：优先在标题、段落处断开，避免把一道题或一个公式切两半
SEPARATORS = [
    "\n## ",
    "\n### ",
    "\n#### ",
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "，",
    " ",
    "",
]

HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]


def _size_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS,
        keep_separator=True,
    )


def _split_markdown(documents: list[Document]) -> list[Document]:
    """按标题切，再按长度细分，把标题层级拼成 heading_path。"""
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=HEADERS,
        strip_headers=False,  # 标题留在正文里，检索时更容易命中
    )
    size_splitter = _size_splitter()
    out: list[Document] = []

    for doc in documents:
        for section in header_splitter.split_text(doc.page_content):
            heading_path = " > ".join(
                section.metadata[key] for _, key in HEADERS if section.metadata.get(key)
            )
            for piece in size_splitter.split_text(section.page_content):
                out.append(
                    Document(
                        page_content=piece,
                        metadata={**doc.metadata, "heading_path": heading_path or doc.metadata["file_name"]},
                    )
                )
    return out


def _split_with_lines(documents: list[Document]) -> list[Document]:
    """按长度切，并反查出每个分片在原文中的行号区间。

    切分器保留了分隔符，所以每个分片都是原文的子串，可以用 find 顺序定位。
    """
    size_splitter = _size_splitter()
    out: list[Document] = []

    for doc in documents:
        text = doc.page_content
        cursor = 0
        for piece in size_splitter.split_text(text):
            idx = text.find(piece, cursor)
            if idx < 0:  # 兜底：回退到全局查找
                idx = text.find(piece)

            meta = dict(doc.metadata)
            if idx >= 0:
                meta["line_start"] = text.count("\n", 0, idx) + 1
                meta["line_end"] = meta["line_start"] + piece.count("\n")
                cursor = idx + len(piece)
            out.append(Document(page_content=piece, metadata=meta))
    return out


def split_documents(documents: list[Document]) -> list[Document]:
    """按文件类型分派切分策略，最后统一编号。"""
    grouped: dict[str, list[Document]] = {}
    for doc in documents:
        grouped.setdefault(doc.metadata.get("file_type", "text"), []).append(doc)

    chunks: list[Document] = []
    for file_type, docs in grouped.items():
        if file_type == "markdown":
            chunks.extend(_split_markdown(docs))
        else:
            chunks.extend(_split_with_lines(docs))

    # 按文件各自编号，方便定位「同一个文件的第几个分片」
    counters: dict[str, int] = {}
    for chunk in chunks:
        source = chunk.metadata["source"]
        chunk.metadata["chunk_index"] = counters.get(source, 0)
        counters[source] = chunk.metadata["chunk_index"] + 1

    return chunks
