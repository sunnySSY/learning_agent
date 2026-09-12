"""编排层：把加载 → 切分 → 入库 → 检索 → 生成串成对外入口。"""

from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from llm import chat_model

from .loader import list_files, load_file
from .retriever import format_context, retrieve
from .splitter import split_documents
from .store import add, drop_source

RAG_SYSTEM = """你是一个学习助手。根据下面的资料片段回答用户的问题。

规则：
1. 只依据资料片段作答。片段里没有的内容，明确说「资料中没有提到」，不要用你自己的知识补全。
2. 每句结论后用 [1] [2] 标注来源，编号对应资料片段的序号。
3. 如果片段之间互相矛盾，把矛盾点指出来，不要擅自选一个。
4. 讲法要适合学习：先给结论，再拆解步骤，必要时举一个例子。
5. 用户的问题本身不清楚时，先问清楚再回答。

资料片段：
{context}"""

NO_CONTEXT_SYSTEM = """你是一个学习助手。用户提问了，但在他的资料里没有检索到相关内容。

不要编造答案。用一两句话说明没找到相关内容，然后二选一：
- 资料里可能确实没有，建议用户把文件放进 ./data/uploads 再执行 /ingest；
- 问题表述含糊，请用户把问题说得更具体一点。"""


# ---------------------------------------------------------------- 索引

def index_path(path: str | Path, user_id: str = "default") -> int:
    """索引单个文件，返回写入的分片数。格式不支持时返回 0。"""
    path = Path(path)
    documents = load_file(path, user_id=user_id)
    if not documents:
        return 0

    chunks = split_documents(documents)
    # 先删旧分片，否则文件改短后会残留
    drop_source(str(path.resolve()))
    add(chunks)
    return len(chunks)


def index_files(paths: list[str | Path], user_id: str = "default") -> dict[str, int]:
    """批量索引，返回 {文件名: 分片数}。单个文件失败不中断整批（记为 -1）。"""
    result: dict[str, int] = {}
    for path in paths:
        path = Path(path)
        try:
            result[path.name] = index_path(path, user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            result[path.name] = -1
            print(f"  [失败] {path.name}: {exc}")
    return result


def index_dir(root: str | Path, user_id: str = "default") -> dict[str, int]:
    """索引整个目录。"""
    return index_files(list_files(root), user_id=user_id)


# ---------------------------------------------------------------- 问答

def ask(
    question: str,
    history: list[dict] | None = None,
    user_id: str = "default",
    k: int = 4,
) -> tuple[str, list[Document]]:
    """检索后生成回答。

    history 是 [{"role": "user"/"assistant", "content": ...}]，由 memory.Memory 提供。
    返回 (回答文本, 命中的文档)，后者用于展示引用。
    """
    docs = retrieve(question, k=k, user_id=user_id)
    system = RAG_SYSTEM.format(context=format_context(docs)) if docs else NO_CONTEXT_SYSTEM

    messages: list = [SystemMessage(content=system)]
    for item in history or []:
        cls = HumanMessage if item["role"] == "user" else AIMessage
        messages.append(cls(content=item["content"]))
    messages.append(HumanMessage(content=question))

    answer = chat_model().invoke(messages).content
    return answer, docs
