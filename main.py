"""Study Helper Agent 主程序。

    python main.py                 开始对话（资料放 ./data/uploads）
    python main.py --ingest        只入库后退出（增量，只处理新增和变动的文件）
    python main.py --ingest --all  忽略清单，全部重新分片
    python main.py --session 数学   指定会话，使用 SQLite checkpointer 保存

问答走 graph 包里的 LangGraph 主图（Phase 3），本文件只负责显示。
"""

import argparse

from config import cfg  # 必须第一个导入：确保 .env 先于 langchain 加载

import graph
import rag
from memory import thread_config, thread_store
from tools import describe_tools

BANNER = """Study Helper Agent
  资料目录 : {data}
  向量库   : {chroma}（{files} 个文件 / {chunks} 个分片）
  对话模型 : {model}
  工具     : {tools}
  联网搜索 : {search}
  当前会话 : {session}（已存 {size} 条消息）
  当前用户 : {user}
  检查点库 : {checkpoint}
  多 Agent : Planner → Retriever → Solver → Tutor → Reviewer（审核上限 {rounds} 轮）

  /ingest [路径]  增量入库：只处理新增和变动的文件（默认扫 {data}）
  /ingest --all   忽略清单，全部重新分片
  /tools          查看可用工具
  /graph          打印主图的 Mermaid 源码
  /new            清空当前会话记忆
  /resume         恢复中断轮次（工具副作用暂不保证恰好执行一次）
  /threads        列出当前用户的会话
  /session 名称   创建或切换会话
  /stats          查看向量库状态
  /exit           退出
"""


def do_ingest(path=None, full: bool = False, user_id: str = "default") -> None:
    """入库。

    默认增量：哈希没变的文件直接跳过，只处理新增和变动的，并清理已删除文件的分片。
    full=True 时忽略清单，全部重新分片。
    """
    target = path or cfg.data_dir
    print(f"扫描 {target}（{'全部重建' if full else '增量'}）...")

    result = rag.sync_dir(target, force=full, user_id=user_id)

    if not (result.indexed or result.skipped or result.empty or result.removed or result.failed):
        print("没找到支持的文件（当前支持 pdf / md / txt）")
        return

    if full:
        # 全量重建时文件并没有「变动」，区分新增/更新只会让人困惑
        for name, count in result.added + result.updated:
            print(f"  [重建] {name} -> {count} 个分片")
    else:
        for name, count in result.added:
            print(f"  [新增] {name} -> {count} 个分片")
        for name, count in result.updated:
            print(f"  [更新] {name} -> {count} 个分片")
    for name in result.removed:
        print(f"  [清理] {name} —— 文件已不在磁盘上，分片已移除")
    for name in result.empty:
        print(f"  [无文本] {name} —— 解析不出文字层，可能是扫描版 PDF")
    for name, err in result.failed:
        print(f"  [失败] {name}: {err}")
    if result.skipped:
        print(f"  [跳过] {len(result.skipped)} 个文件内容未变：{'、'.join(result.skipped)}")

    if result.indexed:
        print(f"共写入 {result.chunks} 个分片")
    else:
        print("没有需要入库的新内容")


def show_tools() -> None:
    names = describe_tools()
    print(f"可用工具（{len(names)} 个）：")
    for line in names:
        print(f"  {line}")
    print(f"\n约束：单轮最多调用 {cfg.max_tool_calls} 次，"
          f"单个工具超时 {cfg.tool_timeout:g}s，失败重试 {cfg.tool_max_retries} 次")
    if not cfg.web_search_enabled:
        print("\n联网搜索未启用：.env 里的 TAVILY_API_KEY 是空的。")


def show_graph() -> None:
    """打印主图的 Mermaid 源码。不调模型，可直接贴进 README。"""
    print(graph.compiled_graph(persistent=False).get_graph().draw_mermaid())


def main() -> int:
    parser = argparse.ArgumentParser(description="Study Helper Agent")
    parser.add_argument("--session", default="default", help="会话名，同用户下独立保存检查点")
    parser.add_argument("--user", default="default", help="本地用户命名空间，不代表身份认证")
    parser.add_argument("--ingest", action="store_true", help="只入库后退出")
    parser.add_argument("--all", action="store_true", help="配合 --ingest 忽略清单全量重建")
    parser.add_argument("--path", help="配合 --ingest 指定路径（目录或单个文件）")
    args = parser.parse_args()

    if args.ingest:
        do_ingest(args.path, full=args.all, user_id=args.user)
        return 0

    store = thread_store()
    thread_id = store.get_thread(args.user, args.session)
    try:
        imported = store.import_legacy(graph.compiled_graph(), args.user, thread_id, cfg.memory_dir)
        if imported:
            print(f"[迁移] 已导入 {imported} 条旧会话消息；旧 JSON 保留但不再写入。")
    except (ValueError, OSError) as exc:
        print(f"[迁移失败] {exc}；旧文件未修改，可修复后重启，或 /new 跳过。")
    snapshot = graph.compiled_graph().get_state(thread_config(thread_id))
    tool_names = [line.split(" —")[0] for line in describe_tools()]
    print(BANNER.format(
        data=cfg.data_dir,
        chroma=cfg.chroma_dir,
        model=cfg.llm_model,
        tools="、".join(tool_names),
        search="已启用" if cfg.web_search_enabled else "未配置（.env 缺 TAVILY_API_KEY）",
        session=args.session,
        size=len(snapshot.values.get("messages", [])),
        user=args.user,
        checkpoint=cfg.checkpoint_db,
        rounds=cfg.review_max_rounds,
        **rag.stats(),
    ))
    if snapshot.next:
        print("[提示] 上轮尚未完成，使用 /resume 恢复或 /new 清空。\n")

    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue
        if question in ("/exit", "/quit"):
            break
        if question == "/new":
            store.clear(args.user, thread_id)
            print("会话记忆已清空\n")
            continue
        if question == "/threads":
            for tid, name in store.list_threads(args.user):
                print(f"  {name} ({tid}){' [当前]' if tid == thread_id else ''}")
            print()
            continue
        if question.startswith("/session "):
            name = question[len("/session "):].strip()
            if not name:
                print("请输入会话名称\n")
                continue
            thread_id = store.get_thread(args.user, name)
            args.session = name
            try:
                store.import_legacy(graph.compiled_graph(), args.user, thread_id, cfg.memory_dir)
            except (ValueError, OSError) as exc:
                print(f"[迁移失败] {exc}")
            current = graph.compiled_graph().get_state(thread_config(thread_id))
            print(f"已切换到 {name}（{len(current.values.get('messages', []))} 条消息）\n")
            if current.next:
                print("上轮尚未完成，使用 /resume 或 /new。\n")
            continue
        if question == "/tools":
            show_tools()
            print()
            continue
        if question == "/graph":
            show_graph()
            print()
            continue
        if question == "/stats":
            info = rag.stats()
            print(f"向量库：{info['files']} 个文件，{info['chunks']} 个分片\n")
            continue
        if question.startswith("/ingest"):
            rest = question[len("/ingest") :].strip()
            full = "--all" in rest
            target = rest.replace("--all", "").strip() or None
            do_ingest(target, full=full, user_id=args.user)
            print()
            continue

        result = None
        print()
        try:
            for event in graph.run_stream(
                None if question == "/resume" else question,
                user_id=args.user, thread_id=thread_id,
            ):
                if event["type"] == "node":
                    print(f"  [{event['name']}] {event['label']}")
                elif event["type"] == "done":
                    result = event["result"]
        except Exception as exc:  # noqa: BLE001
            print(f"[出错] {exc}\n")
            continue

        if result is None:
            print("[出错] 本轮没有产出结果\n")
            continue

        print(f"\n助手 > {result.answer}")

        if result.traces:
            print("\n工具调用：")
            for trace in result.traces:
                print(f"  {trace.line()}")

        if result.citations:
            print("\n引用：")
            for line in result.citations:
                print(f"  {line}")

        if result.hit_tool_limit:
            print(f"\n[提示] 本轮触达工具调用上限（{cfg.max_tool_calls} 次），已强制收口。")

        print()

    graph.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
