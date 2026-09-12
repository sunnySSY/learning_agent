"""文件加载：PDF / Markdown / TXT → Document。

这一层只负责把文件读成文本，并贴上基础元数据。
位置信息（标题路径、行号）由 splitter 在切分时填充，因为只有切分后才知道边界。
"""

from pathlib import Path

from langchain_core.documents import Document

PDF_SUFFIXES = {".pdf"}
MARKDOWN_SUFFIXES = {".md", ".markdown"}
TEXT_SUFFIXES = {".txt"}
SUPPORTED_SUFFIXES = PDF_SUFFIXES | MARKDOWN_SUFFIXES | TEXT_SUFFIXES


def detect_type(path: Path) -> str | None:
    """返回 pdf / markdown / text，不支持的格式返回 None。"""
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return None


def _base_meta(path: Path, user_id: str, file_type: str) -> dict:
    return {
        "source": str(path.resolve()),
        "file_name": path.name,
        "file_type": file_type,
        "user_id": user_id,
    }


def _load_pdf(path: Path, meta: dict) -> list[Document]:
    """按页产出，page 为 0 基页码（展示时 +1）。"""
    from pypdf import PdfReader

    docs = []
    for i, page in enumerate(PdfReader(str(path)).pages):
        text = (page.extract_text() or "").strip()
        if text:
            docs.append(Document(page_content=text, metadata={**meta, "page": i}))
    return docs


def _load_markdown(path: Path, meta: dict) -> list[Document]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [Document(page_content=text, metadata=dict(meta))]


def _load_text(path: Path, meta: dict) -> list[Document]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [Document(page_content=text, metadata=dict(meta))]


def load_file(path: str | Path, user_id: str = "default") -> list[Document]:
    """加载单个文件。格式不支持或内容为空时返回空列表。"""
    path = Path(path)
    if not path.is_file():
        return []

    file_type = detect_type(path)
    if file_type is None:
        return []

    meta = _base_meta(path, user_id, file_type)
    if file_type == "pdf":
        return _load_pdf(path, meta)
    if file_type == "markdown":
        return _load_markdown(path, meta)
    return _load_text(path, meta)


def list_files(root: str | Path) -> list[Path]:
    """递归列出目录下所有支持的文件。"""
    root = Path(root)
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
