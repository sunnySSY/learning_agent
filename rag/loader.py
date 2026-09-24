"""文件加载：PDF / Markdown / TXT → Document。

这一层只负责把文件读成文本，并贴上基础元数据。
位置信息（标题路径、行号）由 splitter 在切分时填充，因为只有切分后才知道边界。
"""

from pathlib import Path
from datetime import datetime, timezone

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


def _base_meta(path: Path, user_id: str, file_type: str, *, file_id: str | None = None,
               index_version: int | None = None, index_config_hash: str | None = None) -> dict:
    meta = {
        "source": str(path.resolve()),
        "source_locator": path.name,
        "file_name": path.name,
        "file_type": file_type,
        "user_id": user_id,
        "parser_version": "pdf_text_v1",
        "splitter_version": "recursive_character_v1",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    # Web ingestion uses stable opaque IDs and versioned vectors.  CLI callers
    # keep the legacy source-only metadata for compatibility.
    if file_id:
        meta["file_id"] = file_id
    if index_version is not None:
        meta["index_version"] = int(index_version)
    if index_config_hash:
        meta["index_config_hash"] = index_config_hash
    return meta


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


def load_file(path: str | Path, user_id: str = "default", *, file_id: str | None = None,
              index_version: int | None = None, index_config_hash: str | None = None) -> list[Document]:
    """加载单个文件。格式不支持或内容为空时返回空列表。"""
    path = Path(path)
    if not path.is_file():
        return []

    file_type = detect_type(path)
    if file_type is None:
        return []

    meta = _base_meta(path, user_id, file_type, file_id=file_id,
                      index_version=index_version, index_config_hash=index_config_hash)
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
