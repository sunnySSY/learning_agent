"""Study Helper Agent 主程序。

    python main.py                 开始对话（资料放 ./data/uploads）
    python main.py --ingest        只入库后退出（增量，只处理新增和变动的文件）
    python main.py --ingest --all  忽略清单，全部重新分片
    python main.py --session 数学   指定会话，使用 SQLite checkpointer 保存

问答走 graph 包里的 LangGraph 主图（Phase 3），本文件只负责显示。
"""

import argparse
import json
import shlex

from config import cfg  # 必须第一个导入：确保 .env 先于 langchain 加载

import graph
import rag
from memory import (
    MemoryService,
    delete_user_data,
    delete_fact_with_context,
    export_user,
    learning_store,
    preview_delete,
    reset_thread,
    thread_config,
    thread_store,
)
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
  /memory         查看长期记忆和待确认候选
  /knowledge      查看知识点状态
  /review today   查看今日复习任务
  /data export ...  导出长期记忆或会话
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
    learning = learning_store()
    memory_service = MemoryService(learning)
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
            reset_thread(learning, store, args.user, thread_id)
            print("会话记忆已清空\n")
            continue
        if question == "/threads":
            for tid, name in store.list_threads(args.user):
                print(f"  {name} ({tid}){' [当前]' if tid == thread_id else ''}")
            print()
            continue
        if question == "/memory" or question.startswith("/memory "):
            parts = shlex.split(question)
            try:
                if len(parts) == 1:
                    print(json.dumps(memory_service.list_memory(args.user), ensure_ascii=False, indent=2))
                elif parts[1] == "confirm" and len(parts) == 3:
                    print(f"已确认记忆：{learning.confirm_candidate(args.user, parts[2])}")
                elif parts[1] == "reject" and len(parts) == 3:
                    learning.reject_candidate(args.user, parts[2]); print("已拒绝候选记忆")
                elif parts[1] == "set" and len(parts) >= 4:
                    raw_key, value = parts[2], " ".join(parts[3:])
                    if ":" in raw_key:
                        kind, key = raw_key.split(":", 1)
                    else:
                        kind, key = "preference", raw_key
                    print(f"已保存记忆：{memory_service.set_fact(args.user, key, value, kind)}")
                elif parts[1] == "delete" and len(parts) == 3:
                    print(delete_fact_with_context(learning, store, args.user, parts[2]))
                elif parts[1] == "auto" and len(parts) == 3:
                    learning.set_auto_extract(args.user, parts[2].lower() == "on"); print("自动提取已更新")
                elif parts[1] == "reset":
                    preview = preview_delete(learning, store, args.user, "memory")
                    print(json.dumps(preview, ensure_ascii=False))
                    if input("确认清空长期记忆和相关会话？输入 yes：").strip().lower() == "yes":
                        print(delete_user_data(learning, store, args.user, "memory"))
                    else: print("已取消")
                else:
                    print("用法：/memory | confirm ID | reject ID | set kind:key value | delete ID | auto on/off | reset")
            except (ValueError, OSError) as exc:
                print(f"[记忆] {exc}")
            print()
            continue
        if question == "/knowledge" or question.startswith("/knowledge "):
            parts = shlex.split(question)
            try:
                if len(parts) >= 2 and parts[1] == "add" and len(parts) >= 4:
                    kid = learning.add_knowledge(args.user, parts[2], " ".join(parts[3:])); print(f"知识点 ID：{kid}")
                elif len(parts) >= 2 and parts[1] == "self-rate" and len(parts) == 4:
                    learning.self_rate(args.user, parts[2], int(parts[3])); print("已保存自评并安排复习")
                elif len(parts) >= 2 and parts[1] == "delete" and len(parts) == 3:
                    learning.delete_knowledge(args.user, parts[2]); print("已删除知识点及其学习事件")
                else:
                    print(json.dumps(learning.knowledge_rows(args.user, parts[1] if len(parts) == 2 else None), ensure_ascii=False, indent=2))
            except (ValueError, OSError) as exc:
                print(f"[知识点] {exc}")
            print()
            continue
        if question == "/review today" or question.startswith("/review "):
            parts = shlex.split(question)
            try:
                if len(parts) >= 2 and parts[1] == "today":
                    minutes = int(parts[3]) if len(parts) == 4 and parts[2] == "--minutes" else None
                    plan = memory_service.review_plan(args.user, minutes)
                    print(json.dumps({key: ([item.__dict__ for item in value] if isinstance(value, list) else value) for key, value in plan.items()}, ensure_ascii=False, indent=2))
                elif len(parts) == 4 and parts[1] == "feedback":
                    print(learning.feedback(args.user, parts[2], parts[3], f"cli-{args.user}-{parts[2]}-{parts[3]}-{int(__import__('time').time())}"))
                elif len(parts) in {4, 5} and parts[1] == "postpone":
                    days = int(parts[4]) if len(parts) == 5 and parts[3] == "--days" else int(parts[3])
                    print({"due_at": learning.postpone(args.user, parts[2], days)})
                else:
                    print("用法：/review today [--minutes N] | feedback TASK RATING | postpone TASK DAYS")
            except (ValueError, OSError) as exc:
                print(f"[复习] {exc}")
            print()
            continue
        if question == "/data export" or question.startswith("/data export "):
            parts = shlex.split(question)
            try:
                scope = parts[parts.index("--scope") + 1] if "--scope" in parts else "all"
                output = parts[parts.index("--out") + 1] if "--out" in parts else f"{args.user}-export.json"
                payload = export_user(learning, store, args.user, scope, output)
                print(f"已导出 {len(payload['data'])} 类数据到 {output}\n")
            except (ValueError, OSError, FileExistsError) as exc:
                print(f"[导出] {exc}\n")
            continue
        if question.startswith("/data delete") or question.startswith("/thread delete"):
            parts = shlex.split(question)
            try:
                if parts[0] == "/thread":
                    scope, target = "thread", parts[2]
                else:
                    scope = parts[parts.index("--scope") + 1] if "--scope" in parts else "all"
                    target = parts[parts.index("--thread") + 1] if "--thread" in parts else None
                preview = preview_delete(learning, store, args.user, scope, target)
                print(json.dumps(preview, ensure_ascii=False, indent=2))
                if input("确认删除？输入 yes：").strip().lower() == "yes":
                    print(delete_user_data(learning, store, args.user, scope, target))
                else: print("已取消")
            except (ValueError, OSError) as exc:
                print(f"[删除] {exc}")
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

        try:
            current = graph.compiled_graph().get_state(thread_config(thread_id)).values
            turn_question = current.get("question", question)
            candidates = memory_service.commit_turn(
                args.user, thread_id, result.turn_id, turn_question, result.answer, result.turn_id
            )
            if candidates:
                print(f"\n[记忆] 本轮新增 {len(candidates)} 条候选/事实；用 /memory 查看。")
        except (ValueError, OSError) as exc:
            print(f"[记忆] 本轮提取失败，不影响答案：{exc}")

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
