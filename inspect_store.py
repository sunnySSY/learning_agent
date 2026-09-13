"""查看本地向量库里到底存了什么。

chroma.sqlite3 是二进制文件，直接用编辑器打开只会看到乱码，
这个脚本绕过 Chroma 客户端、直接读 SQLite，把内容翻译成可读的文本。

    python inspect_store.py                      总览：集合、维度、每个文件多少分片
    python inspect_store.py --files              按文件汇总
    python inspect_store.py --file SPEC.md       看某个文件的分片（默认前 3 条）
    python inspect_store.py --file SPEC.md -n 0  看某个文件的全部（-n 0 = 不限）
    python inspect_store.py --search 特征值       按关键词搜正文，含位置
    python inspect_store.py --vector 1           看第 1 条分片的向量（前 8 维 + 统计量）
    python inspect_store.py --raw                看数据库原始表结构

位置信息由 rag/splitter 在切分时写入 metadata，所以这里显示的就是
检索时喂给模型的那份内容，和线上跑的是同一批数据。
"""

import argparse
import sqlite3
import struct
import sys
from pathlib import Path

from config import cfg

# Windows 控制台默认 GBK，中文和长文本会花屏
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

DB = Path(cfg.chroma_dir) / "chroma.sqlite3"


def connect() -> sqlite3.Connection:
    if not DB.is_file():
        raise SystemExit(f"没找到向量库：{DB}\n先执行 python main.py --ingest 建索引。")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def collection_info(con: sqlite3.Connection) -> sqlite3.Row | None:
    return con.execute(
        "select id, name, dimension from collections where name = ?", (cfg.collection,)
    ).fetchone()


# ---------------------------------------------------------------- 取数
# Chroma 1.x 把一个集合拆成两个 segment：
#   METADATA（embedding_metadata）存正文和 metadata
#   VECTOR  （embeddings_queue）存 float32 向量
# 正文在 FTS5 表里，rowid 与 embedding_metadata.id 对齐。

def chunks(con: sqlite3.Connection, limit: int = 0, file_name: str | None = None):
    """产出 (id, 正文, {metadata}) 三元组。"""
    sql = """
        select e.id as id,
               f.string_value as text,
               m.key as key,
               m.string_value as sval,
               m.int_value as ival,
               m.float_value as fval
        from embeddings e
        join embedding_metadata m on m.id = e.id
        left join embedding_fulltext_search f on f.rowid = e.id
        order by e.id
    """
    rows: dict[int, dict] = {}
    for r in con.execute(sql):
        item = rows.setdefault(r["id"], {"text": r["text"], "meta": {}})
        if r["text"]:
            item["text"] = r["text"]
        value = r["sval"]
        if value is None:
            value = r["ival"] if r["ival"] is not None else r["fval"]
        item["meta"][r["key"]] = value

    items = [(cid, it["text"] or "", it["meta"]) for cid, it in sorted(rows.items())]
    if file_name:
        items = [x for x in items if x[2].get("file_name") == file_name]
    return items[:limit] if limit > 0 else items


def vector_of(con: sqlite3.Connection, chunk_id: int) -> tuple[list[float], str] | None:
    """从 embeddings_queue 里取出向量，返回 (浮点数组, 编码)。"""
    row = con.execute(
        "select e.embedding_id, q.vector, q.encoding "
        "from embeddings e join embeddings_queue q on q.id = e.embedding_id "
        "where e.id = ?",
        (chunk_id,),
    ).fetchone()
    if not row or not row["vector"]:
        return None
    values = struct.unpack(f"<{len(row['vector']) // 4}f", row["vector"])
    return list(values), row["encoding"]


# ---------------------------------------------------------------- 展示

def show_overview(con: sqlite3.Connection) -> None:
    col = collection_info(con)
    if not col:
        print(f"集合 {cfg.collection!r} 不存在。先执行 python main.py --ingest。")
        return

    items = chunks(con)
    print(f"库文件   : {DB}")
    print(f"体积     : {DB.stat().st_size / 1024:.0f} KB")
    print(f"集合     : {col['name']}")
    print(f"维度     : {col['dimension']}（每条向量 {col['dimension'] * 4} 字节 float32）")
    print(f"分片总数 : {len(items)}")

    by_file: dict[str, int] = {}
    for _, _, meta in items:
        by_file[meta.get("file_name", "?")] = by_file.get(meta.get("file_name", "?"), 0) + 1
    print(f"文件数   : {len(by_file)}")
    print()
    for name, count in sorted(by_file.items(), key=lambda x: -x[1]):
        print(f"  {count:>4}  {name}")

    print()
    print("提示：向量本体在 data/chroma/<segment-id>/data_level0.bin（HNSW 二进制索引），")
    print("      正文和 metadata 在下面这几张表里，用 --file / --search / --vector 查看。")


def render(cid: int, text: str, meta: dict, show_text: bool = True) -> None:
    from rag.retriever import format_locator  # 复用线上的引用渲染，保证一致

    print(f"── 分片 #{cid}  {format_locator(meta)}")
    if meta.get("chunk_index") is not None:
        print(f"   chunk_index={meta['chunk_index']}  user_id={meta.get('user_id')}")
    if show_text:
        body = text if len(text) <= 400 else text[:400] + f" …（共 {len(text)} 字）"
        print("   " + body.replace("\n", "\n   "))
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="查看本地 Chroma 向量库内容")
    parser.add_argument("--files", action="store_true", help="按文件汇总")
    parser.add_argument("--file", help="按文件名过滤")
    parser.add_argument("-n", type=int, default=3, help="显示条数，0 表示不限（默认 3）")
    parser.add_argument("--search", help="按关键词搜正文")
    parser.add_argument("--vector", type=int, metavar="ID", help="显示指定分片的向量")
    parser.add_argument("--raw", action="store_true", help="显示数据库表结构")
    args = parser.parse_args()

    con = connect()

    if args.raw:
        cur = con.execute("select name from sqlite_master where type='table' order by name")
        for (name,) in cur:
            count = con.execute(f"select count(*) from {name}").fetchone()[0]
            print(f"{name:<40} {count:>6} 行")
        return 0

    if args.vector is not None:
        got = vector_of(con, args.vector)
        if not got:
            print(f"分片 #{args.vector} 没有向量（可能 ID 越界）")
            return 1
        vec, encoding = got
        head = ", ".join(f"{v:+.4f}" for v in vec[:8])
        norm = sum(v * v for v in vec) ** 0.5
        print(f"分片 #{args.vector}  向量编码={encoding}  维度={len(vec)}")
        print(f"前 8 维 : [{head}, ...]")
        print(f"取值范围: [{min(vec):+.4f}, {max(vec):+.4f}]")
        print(f"L2 范数 : {norm:.4f}")
        print()
        print("这就是「打不开」的东西：1024 个浮点数，不是文本。")
        return 0

    if args.search:
        hits = [x for x in chunks(con) if args.search in x[1]]
        if not hits:
            print(f"没有正文包含 {args.search!r} 的分片")
            return 0
        print(f"命中 {len(hits)} 条：\n")
        for cid, text, meta in hits[: args.n or len(hits)]:
            idx = text.find(args.search)
            start = max(0, idx - 60)
            print(f"── 分片 #{cid}  {meta.get('file_name')}")
            print("   …" + text[start : idx + 120].replace("\n", " ") + "…\n")
        return 0

    if args.files and not args.file:
        items = chunks(con)
        by_file: dict[str, list] = {}
        for cid, text, meta in items:
            by_file.setdefault(meta.get("file_name", "?"), []).append((cid, text, meta))
        for name, group in sorted(by_file.items(), key=lambda x: -len(x[1])):
            types = {m.get("file_type") for _, _, m in group}
            users = {m.get("user_id") for _, _, m in group}
            print(f"{len(group):>4} 分片  {name}  [{('/'.join(sorted(types)))}]  user={','.join(sorted(users))}")
        return 0

    if args.file:
        items = chunks(con, file_name=args.file)
        if not items:
            print(f"没有 {args.file!r} 的分片（用 --files 看有哪些文件）")
            return 0
        for cid, text, meta in items[: args.n or len(items)]:
            render(cid, text, meta)
        shown = min(args.n, len(items)) if args.n else len(items)
        if shown < len(items):
            print(f"（省略 {len(items) - shown} 条，用 -n 0 看全部）")
        return 0

    show_overview(con)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
