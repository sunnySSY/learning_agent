"""RAG 回归集跑分。

    python evaluation/run.py                # 完整跑（检索 + 生成，会消耗 token）
    python evaluation/run.py --no-llm       # 只测检索，不调模型，快且免费
    python evaluation/run.py --top-k 6      # 覆盖题目文件里的 top_k
    python evaluation/run.py --limit 10     # 只跑前 10 题

输出的四项指标对应 PROJECT_PLAN 第 7 节：
    Recall@k      期望来源被检索命中的比例
    引用准确率    召回的分片里，属于期望来源的比例
    拒答正确率    该拒答的题是否正确拒答
    平均延迟
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import cfg  # noqa: F401  必须最先导入，确保 .env 先于 langchain 生效

import rag  # noqa: E402

DATASET = Path(__file__).parent / "dataset.json"

# 判断模型有没有拒答的口径。宽松一点，只要能看出「资料里没有」就算拒答。
REFUSAL_MARKERS = [
    "没有提到",
    "没有找到",
    "未找到",
    "没有相关",
    "没有涉及",
    "资料中没有",
    "无法回答",
    "没有包含",
    "未提及",
]


def match_source(expected: dict, meta: dict) -> bool:
    """判断一个召回分片是否落在期望来源上。"""
    if expected.get("file_name") and expected["file_name"] != meta.get("file_name"):
        return False

    if "page" in expected:
        # 数据集里写 PDF 阅读器上看到的页码（1 基），元数据内部是 0 基，这里做转换
        if meta.get("page") != expected["page"] - 1:
            return False

    if "heading_path" in expected:
        actual = meta.get("heading_path") or ""
        if expected["heading_path"] not in actual:
            return False

    if "line_start" in expected:
        start = meta.get("line_start")
        end = meta.get("line_end", start)
        if not isinstance(start, int) or not (start <= expected["line_start"] <= end):
            return False

    return True


def is_refusal(text: str) -> bool:
    return any(marker in text for marker in REFUSAL_MARKERS)


def run_one(item: dict, top_k: int, use_llm: bool) -> dict:
    question = item["question"]
    expected = item.get("expected_sources") or []
    should_refuse = bool(item.get("should_refuse"))

    started = time.perf_counter()
    docs = rag.retrieve(question, k=top_k)
    answer = ""
    if use_llm:
        # 传空 history，保证每题独立，不受其他题目的对话上下文影响
        answer, docs = rag.ask(question, history=[], k=top_k)
    latency = time.perf_counter() - started

    matched = [
        any(match_source(exp, doc.metadata) for exp in expected) for doc in docs
    ]

    # Recall@k：期望来源被命中的比例
    recall = None
    if expected:
        hit = sum(1 for exp in expected if any(match_source(exp, d.metadata) for d in docs))
        recall = hit / len(expected)

    # 引用准确率：召回的分片里属于期望来源的比例
    citation_precision = (sum(matched) / len(docs)) if (docs and expected) else None

    keywords = item.get("answer_keywords") or []
    keyword_hits = sum(1 for kw in keywords if kw in answer) if (answer and keywords) else None

    refused = is_refusal(answer) if answer else None
    refuse_ok = None
    if should_refuse and answer:
        refuse_ok = refused

    # 判定这题过没过
    if should_refuse:
        passed = bool(refuse_ok) if use_llm else True
        note = "" if passed else "该拒答却给了答案"
    else:
        passed = (recall or 0) > 0
        note = "" if passed else "期望来源一个都没召回"

    return {
        "id": item.get("id", "?"),
        "question": question,
        "locators": rag.format_citations(docs),
        "recall": recall,
        "citation_precision": citation_precision,
        "keyword_hits": keyword_hits,
        "keyword_total": len(keywords),
        "refuse_ok": refuse_ok,
        "latency": latency,
        "passed": passed,
        "note": note,
    }


def summarize(results: list[dict], use_llm: bool) -> dict:
    recalls = [r["recall"] for r in results if r["recall"] is not None]
    precisions = [r["citation_precision"] for r in results if r["citation_precision"] is not None]
    refuse = [r["refuse_ok"] for r in results if r["refuse_ok"] is not None]
    kw_hits = sum(r["keyword_hits"] for r in results if r["keyword_hits"] is not None)
    kw_total = sum(r["keyword_total"] for r in results if r["keyword_hits"] is not None)

    def avg(values):
        return sum(values) / len(values) if values else None

    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "recall": avg(recalls),
        "citation_precision": avg(precisions),
        "refuse_ok": avg(refuse) if refuse else None,
        "keyword_rate": (kw_hits / kw_total) if kw_total else None,
        "latency": avg([r["latency"] for r in results]),
        "llm_ran": use_llm,
    }


def fmt(value, as_percent=True):
    if value is None:
        return "  -  "
    return f"{value * 100:5.1f}%" if as_percent else f"{value:6.2f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 回归集跑分")
    parser.add_argument("--no-llm", action="store_true", help="只测检索，不调模型")
    parser.add_argument("--top-k", type=int, help="覆盖题目文件里的 top_k")
    parser.add_argument("--limit", type=int, help="只跑前 N 题")
    args = parser.parse_args()

    data = json.loads(DATASET.read_text(encoding="utf-8"))
    questions = data.get("questions", [])
    top_k = args.top_k or data.get("top_k", 4)
    use_llm = not args.no_llm

    if not questions:
        print(f"回归集是空的：{DATASET}")
        print("把资料放进 data/uploads，然后按文件里的 _字段 说明填写题目。")
        return 1

    if args.limit:
        questions = questions[: args.limit]

    print(f"回归集 {len(questions)} 题，top_k={top_k}，"
          f"{'含生成' if use_llm else '仅检索'}\n")

    results = []
    for i, item in enumerate(questions, start=1):
        result = run_one(item, top_k, use_llm)
        results.append(result)
        flag = "通过" if result["passed"] else "失败"
        print(f"  [{i:2d}/{len(questions)}] {result['id']:<6} {flag}  "
              f"recall={fmt(result['recall'])}  "
              f"引用={fmt(result['citation_precision'])}  "
              f"{result['latency']:.2f}s")
        if not result["passed"] and result["note"]:
            print(f"           {result['note']}")

    summary = summarize(results, use_llm)

    print("\n" + "=" * 56)
    print(f"  题目数        {summary['total']}")
    print(f"  通过          {summary['passed']}/{summary['total']}")
    print(f"  Recall@{top_k}     {fmt(summary['recall'])}")
    print(f"  引用准确率    {fmt(summary['citation_precision'])}")
    print(f"  拒答正确率    {fmt(summary['refuse_ok'])}")
    print(f"  关键词命中    {fmt(summary['keyword_rate'])}")
    print(f"  平均延迟      {summary['latency']:.2f}s")
    if not use_llm:
        print("  （--no-llm 模式：拒答和关键词两项无意义）")
    print("=" * 56)

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\n失败样例（{len(failed)} 题）：")
        for r in failed:
            print(f"\n  {r['id']}  {r['question']}")
            print(f"    召回：{'；'.join(r['locators']) or '（无）'}")
            if r["note"]:
                print(f"    原因：{r['note']}")

    print("\n指标说明：目标为引用准确率 >= 90%，核心回归题正确率 >= 80%（PROJECT_PLAN 7）")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
