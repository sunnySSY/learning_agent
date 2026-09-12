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


def drop_source(source: str) -> None:
    """删掉某个文件的旧分片。文件改短后不做这步会残留孤儿分片。"""
    try:
        vectorstore().delete(where={"source": source})
    except Exception:
        # 集合为空或该 source 不存在时，部分 Chroma 版本会抛错，忽略即可
        pass


def reset() -> None:
    """清空向量库。"""
    try:
        vectorstore().delete_collection()
    except Exception as exc:
        print(f"[store] 清空失败: {exc}")
    # 集合没了，缓存里的客户端也失效了，重建
    vectorstore.cache_clear()


def stats() -> dict:
    """当前库里的分片数和文件数，用于启动时展示。"""
    try:
        data = vectorstore().get(include=["metadatas"])
    except Exception:
        return {"chunks": 0, "files": 0}

    metadatas = data.get("metadatas") or []
    files = {m.get("source") for m in metadatas if m}
    return {"chunks": len(metadatas), "files": len(files)}
