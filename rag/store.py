"""本地 Chroma 向量库。数据落在 ./data/chroma，不依赖任何外部服务。"""

from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document

from config import cfg

from .embedder import AliyunEmbeddings


@lru_cache(maxsize=1)
def embeddings() -> AliyunEmbeddings:
    return AliyunEmbeddings(
        api_key=cfg.embed_api_key,
        base_url=cfg.embed_base_url,
        model=cfg.embed_model,
        dimensions=cfg.embed_dim,
    )


@lru_cache(maxsize=1)
def vectorstore() -> Chroma:
    """打开（不存在则创建）本地向量库。"""
    cfg.chroma_dir.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=cfg.collection,
        embedding_function=embeddings(),
        persist_directory=str(cfg.chroma_dir),
        collection_metadata={"hnsw:space": "cosine"},
    )


def add(chunks: list[Document]) -> None:
    if chunks:
        vectorstore().add_documents(chunks)


def drop_source(source: str, user_id: str = "default") -> None:
    """删掉某个文件的旧分片。文件改短后不做这步会残留孤儿分片。"""
    try:
        vectorstore().delete(where={"$and": [{"source": source}, {"user_id": user_id}]})
    except Exception:
        # 集合为空或该 source 不存在时，部分 Chroma 版本会抛错，忽略即可
        pass


def drop_file(file_id: str, user_id: str = "default") -> None:
    """Remove every version of an opaque Web file for one user."""
    try:
        vectorstore().delete(where={"$and": [{"file_id": file_id}, {"user_id": user_id}]})
    except Exception:
        pass


# Product-facing name; keep ``drop_file`` for the existing CLI vocabulary.
delete_file = drop_file


def drop_file_version(file_id: str, index_version: int, user_id: str = "default") -> None:
    try:
        vectorstore().delete(where={"$and": [{"file_id": file_id}, {"index_version": int(index_version)}, {"user_id": user_id}]})
    except Exception:
        pass


def drop_file_old_versions(file_id: str, keep_version: int, user_id: str = "default") -> None:
    """Best-effort cleanup after a new version has become active."""
    try:
        data = vectorstore().get(where={"$and": [{"file_id": file_id}, {"user_id": user_id}]}, include=["metadatas"])
        ids = [identifier for identifier, metadata in zip(data.get("ids") or [], data.get("metadatas") or []) if metadata and metadata.get("index_version") != int(keep_version)]
        if ids:
            vectorstore().delete(ids=ids)
    except Exception:
        pass


def reset() -> None:
    """清空向量库。"""
    try:
        vectorstore().delete_collection()
    except Exception as exc:
        print(f"[store] 清空失败: {exc}")
    # 集合没了，缓存里的客户端也失效了，重建
    vectorstore.cache_clear()


def delete_user(user_id: str) -> int:
    """删除一个用户的所有向量，保留其他用户内容。"""
    try:
        data = vectorstore().get(where={"user_id": user_id}, include=["metadatas"])
        ids = data.get("ids") or []
        if ids:
            vectorstore().delete(ids=ids)
        return len(ids)
    except Exception:
        return 0


def stats() -> dict:
    """当前库里的分片数和文件数，用于启动时展示。"""
    try:
        data = vectorstore().get(include=["metadatas"])
    except Exception:
        return {"chunks": 0, "files": 0}

    metadatas = data.get("metadatas") or []
    files = {m.get("source") for m in metadatas if m}
    return {"chunks": len(metadatas), "files": len(files)}
