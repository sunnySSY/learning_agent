# 开发进度说明

本文记录 Study Helper Agent **做到哪一步了**：已完成的部分逐项写明「做了什么、怎么用、怎么实现的、验证到什么程度」，未完成的部分写明清「缺什么、入口在哪、依赖什么」。

三份文档的分工：

| 文档 | 回答什么问题 |
|---|---|
| `PROJECT_PLAN.md` | 要做成什么样（设计、契约、路线） |
| `README.md` | 怎么装、怎么跑、各模块怎么设计（面向使用者） |
| **本文（PROGRESS.md）** | 哪些做完了、哪些没做、验证到哪、还剩哪些坑 |

**更新日期**：2026-09-13　**当前阶段**：Phase 3 已完成，Phase 4 / Phase 5 未开始

---

## 一、总览

| 能力 | 状态 | 怎么用 | 主要代码 |
|---|---|---|---|
| 工程基线（配置、本地存储） | ✅ 完成 | 复制 `.env` 填 key | `config.py`、`llm.py` |
| 资料加载 / 切分 / 向量化 / 入库 | ✅ 完成 | `python main.py --ingest` | `rag/loader.py` `splitter.py` `embedder.py` `store.py` |
| 增量入库（哈希幂等 + 孤儿清理） | ✅ 完成 | 同上（默认即增量） | `rag/manifest.py` `pipeline.py` |
| 向量检索与页码/标题/行号引用 | ✅ 完成 | 问答时自动 | `rag/retriever.py` |
| 工具调用（计算 / 联网 / 卡片）+ 四重护栏 | ✅ 完成 | 问答时自动，`/tools` 查看 | `tools/registry.py` 等 |
| 工具调用轨迹落盘 | ✅ 完成 | 自动写 `data/traces/*.jsonl` | `tools/registry.py:ToolTracer` |
| 多 Agent 主图（6 节点 + 4 路由） | ✅ 完成 | 问答时自动 | `graph/builder.py` `nodes.py` |
| 流式节点事件 | ✅ 完成 | 终端逐行显示 `[节点] 说明` | `graph/builder.py:run_stream` |
| 会话记忆（短期，JSON 文件） | ⚠️ 部分 | `--session` / `/new` | `memory.py` |
| 回归集与跑分脚本 | ✅ 完成 | `python evaluation/run.py` | `evaluation/` |
| 向量库内容查看工具 | ✅ 完成 | `python inspect_store.py ...` | `inspect_store.py` |
| 混合检索（BM25）+ 重排 + 相关度阈值 | ❌ 未做 | — | 待补 `rag/retriever.py` |
| 沙箱代码执行工具 | ❌ 未做 | — | 待补 `tools/code_runner.py` |
| 长期记忆 / 学习档案 | ❌ 未做 | — | Phase 4 |
| 多模态读图 | ❌ 未做 | — | Phase 5（`rag/loader.py` 曾有过图片分支，改包结构时移除） |
| 界面 / API / 鉴权 / 部署 | ❌ 未做 | — | Phase 5 |
| 单元测试 / CI / `.env.example` | ❌ 未做 | — | 工程项 |

**当前库内实际数据**：`data/uploads/` 7 个文件 → 向量库 145 个分片（`rag.stats()` 实测）；会话记忆 1 个（`default.json`）；工具轨迹 6 个 JSONL；复习卡片 1 个（`特征值.json`）。

---

## 二、已完成部分

### 2.1 工程基线（Phase 0）

**做了什么**

- 全局唯一配置来源 `config.py`：`@dataclass(frozen=True) Config`，启动时从 `.env` 读一次，全局单例 `cfg`。
- 必填项缺失直接抛错（`_req`），可选项有默认值（`_opt`）；`.env` 里 `${XXX}` 这种解析失败的引用会被当作没填，而不是当成字符串。
- 对话模型工厂 `llm.py`（`lru_cache` 单例，`ChatOpenAI` 指向百炼的 OpenAI 兼容端点）。
- 依赖清单 `requirements.txt`。

**怎么用**

复制/编辑 `.env` 后即可。变量清单见 `README.md`，必填只有 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`。

**实现上的两个关键点**

1. **`import config` 必须是每个入口文件的第一行**（`main.py:13`、`evaluation/run.py:24` 都有注释提醒）。因为 LangSmith 只在创建 tracer 的那一刻读 `LANGSMITH_*` 环境变量，而 `config.py` 里的 `load_dotenv()` 是唯一把它们灌进进程环境的地方；晚一步就收不到 trace。
2. **偏离计划书**：Phase 0 要求配 `VECTOR_STORE_URL` 和 `DATABASE_URL`，本项目改成**纯本地**——向量库落 `./data/chroma`，入库清单落 `./data/index_manifest.json`，会话记忆落 `./data/memory/*.json`。因此开发时不需要起 Postgres / Redis / 外部向量库。

**未覆盖**：无 `.env.example`（`.gitignore` 里已经留了 `!.env.example` 的白名单）、依赖未锁版本、没有 logging 框架（全程 `print`）、没有统一异常模型（只有工具侧的 `ToolError`）、没有 CI。

---

### 2.2 资料入库与检索（Phase 1）

链路：`loader → splitter → embedder → store`，`manifest` 负责幂等，`pipeline.py` 把它们串成对外入口。

**怎么用**

```bash
python main.py --ingest                 # 增量入库，扫 ./data/uploads
python main.py --ingest --all           # 忽略清单，全部重新分片
python main.py --ingest --path 某文件.pdf  # 只处理一个文件
```

对话中：`/ingest [路径] [--all]`、`/stats`（看文件数与分片数）。

查看库里到底存了什么（绕过 Chroma 客户端直接读 SQLite，所以能看到正文和向量）：

```bash
python inspect_store.py --files          # 按文件汇总分片数
python inspect_store.py --file SPEC.md   # 看某文件的分片（正文 + 位置信息）
python inspect_store.py --search 特征值   # 按关键词搜正文
python inspect_store.py --vector 1       # 看某条向量（前 8 维 + 统计量）
python inspect_store.py --raw            # 数据库表结构
```

**实现方式（逐层）**

| 层 | 文件 | 关键实现 | 关键常量 |
|---|---|---|---|
| 加载 | `rag/loader.py` | PDF 用 `pypdf` **逐页**产出（`page` 为 0 基，展示时 +1）；Markdown / TXT 整体读入。不支持的格式返回空列表 | 支持 `.pdf` `.md` `.markdown` `.txt` |
| 切分 | `rag/splitter.py` | Markdown 先按标题结构切（`MarkdownHeaderTextSplitter`，`strip_headers=False` 让标题留在正文里、更容易被检索命中），再按长度细分，把标题层级拼成 `heading_path`；PDF / TXT 按长度切，再用 `str.find` 顺序反查原文位置算出 `line_start` / `line_end`（切分器保留了分隔符，所以每个分片都是原文子串）。最后按文件各自编 `chunk_index` | `CHUNK_SIZE=800` `CHUNK_OVERLAP=120`（对应计划书 500–800 token）；分隔符从粗到细含中文标点 |
| 向量化 | `rag/embedder.py` | 阿里云 `text-embedding-v4`。手工分批、维度校验、非对称检索（文档 `text_type=document`、查询 `text_type=query`） | `MAX_BATCH=10`（第 11 条直接 400）；合法维度 `64…3072`（按 **API 实测**，比官方文档多出 3072） |
| 存储 | `rag/store.py` | 本地 Chroma，`hnsw:space=cosine`，`lru_cache` 单例。`drop_source` 按 `source` 删旧分片（文件改短后不删会留孤儿）；`reset()` 清空集合 | `CHROMA_PERSIST_DIR=./data/chroma` |
| 幂等 | `rag/manifest.py` | 每个文件记 `sha256 + size + mtime`。先比 size/mtime 快筛，不一致才算哈希。**清单是缓存不是真相**：写坏了当作空清单全量重建；入库失败或解析为空时 `forget`，避免留下"已入库"的假记录 | `VERSION=1` |
| 编排 | `rag/pipeline.py` | `sync_dir` 产出 `SyncResult`，分六类汇报：`added / updated / skipped / empty / removed / failed`。传**单个文件**时不做删除清理（否则会把"只扫到这一个"误判成"其他都被删了"） | — |
| 检索 | `rag/retriever.py` | `similarity_search`，按 `user_id` 过滤。三种引用格式分开渲染，因为只有 PDF 有真页码 | `DEFAULT_K=4` |

**引用标注规则**（用户选定的方案，实现在 `format_locator`）

| 格式 | 定位方式 | 展示效果 |
|---|---|---|
| PDF | 页码 | `讲义.pdf 第42页` |
| Markdown | 标题路径 | `笔记.md > 第3章 > 3.2 特征值` |
| TXT | 行号区间 | `notes.txt 第120-168行` |

**验证到什么程度**

- ✅ 对 `data/uploads/` 里 7 份真实资料（PDF + Markdown + TXT）跑通，落盘 145 个分片，`inspect_store.py` 能读出正文、元数据、向量（1024 维）。
- ✅ 增量路径实际被用过：清单里留了一条 `_sync_probe.txt` 的残留记录（磁盘上已无此文件、库里也没有它的分片），下次 `/ingest` 会把它当"已删除"清掉并打印一行 `[清理]`——正好是删除清理路径的现场证据。
- ✅ 回归集基线：38 题、`top_k=4`、`--no-llm` 模式 Recall@4 = **74.3%**（29/38 通过），引用准确率 18.6%，平均 0.80s。
- ⚠️ 纯向量召回，没有相关度阈值 → **"没找到就反问"这条保护网目前是死代码**（详见 4.1）。
- ⚠️ 失败集中在两类，非随机：**跨语言检索**（题目中文、资料英文，同一问题换英文就能召回）和**多主题分片稀释**（`AGENTS.md` 的 `Rules` 一节把缩进、var、框架规范混在一个分片里，embedding 被平均成"什么都沾一点"）。
- ⚠️ 基线是在"5 份资料"的语料上跑的，而当前 `data/uploads/` 是 7 份、库里 145 个分片，**两者不是同一批数据**，复跑一次才能确认基线还有效。

---

### 2.3 工具调用与护栏（Phase 2）

**做了什么**

三个工具，描述写在各自模块里，模型靠描述判断什么时候用：

| 工具 | 文件 | 用途 | 参数 |
|---|---|---|---|
| `calculate` | `tools/calculator.py` | 所有数值计算，不让模型心算 | `expression` |
| `web_search` | `tools/web_search.py` | 资料里没有的、有时效性的内容（Tavily） | `query` |
| `make_flashcards` | `tools/flashcard.py` | 把内容整理成问答卡片存到本地 | `topic` / `content` / `count` |

**怎么用**：问答时自动触发；`/tools` 查看当前装配了哪些工具及约束参数；卡片落在 `data/flashcards/<主题>.json`（同主题追加、不覆盖）。

**四重护栏**（全部集中在 `tools/registry.py`，各工具自己不重复实现）

| 护栏 | 行为 | 配置项 |
|---|---|---|
| schema | 由函数签名推断（靠 `@wraps` 保住 `inspect.signature`，少了它签名只剩 `**kwargs`，工具等于废掉），模型只能按声明的参数调用 | — |
| 超时 | 线程池包一层（Windows 没有 `signal.alarm`，只能用这个办法） | `TOOL_TIMEOUT=15` |
| 重试 | 失败重试，最多执行 3 次 | `TOOL_MAX_RETRIES=2` |
| 次数上限 | 单轮对话的**全局**额度（不区分工具），超了记为 `rejected` 并把"换个思路"的提示交回模型 | `MAX_TOOL_CALLS=6` |

**三个设计上的选择**

1. **工具失败不抛异常，而是把错误信息当返回值交给模型。** 模型有机会换一种问法，而不是整个对话崩掉；实在不行它会说明工具失败了，不会编一个结果出来。
2. **认证类错误不重试。** `web_search` 遇 401/403/432 抛 `ToolError(retryable=False)`——key 无效重试多少次都一样，实测让无谓重试从 4.95s 降到 1.72s。429 和 5xx 则相反，是暂时性的，值得重试。
3. **`calculate` 用 AST 遍历求值，不走 `eval`。** 只放行白名单运算符和函数（`_BIN_OPS` / `_UNARY_OPS` / `_FUNCS`），常量只认 `pi` / `e`，幂运算指数上限 1000，防 `9**9**9` 把内存打满。

**工具轨迹**：每次调用写 `data/traces/<会话名>-<时间戳>.jsonl`，一行一条，字段含 `name / args / status / duration / attempts / result_chars / preview / error`。`status` 四种：`ok` / `error` / `timeout` / `rejected`；`attempts > 1` 说明重试过。终端里显示成 `✓ calculate(expression=47*3*12) 0.00s`。

**验证到什么程度**

- ✅ `calculate` 有成功记录，也有 `rejected` 记录（额度上限确实生效过）。
- ✅ 无 key 不装配、假 key 报 401 两条错误路径验证过。
- ✅ `make_flashcards` 实跑过一次（`data/flashcards/特征值.json`）。
- ⚠️ `.env` 里**已经填入 Tavily key**（`README.md` 里"需要你去申请"那句已过时），`web_search` 会被正常装配；但仓库里没有留下真实搜索成功的轨迹，**搜索结果质量仍未评估**。

---

### 2.4 多 Agent 主图（Phase 3）

`planner.py` 里那个单 Agent 大循环换成了 LangGraph 主图，对应计划书 3.4 的职责表。

**怎么用**：`python main.py` 后正常提问即可，终端会逐行打印节点事件。

```
你 > Hong Kong Live 项目里前端用什么技术栈？

  [planner] 意图=qa，需工具=False
  [retriever] 召回 4 个分片
  [tutor] 草稿完成
  [reviewer] 第 1 轮通过

助手 > **结论：……**
```

`/graph` 可随时打印这张图的 Mermaid 源码（不调模型）。

**六个节点**（`graph/nodes.py`）

| 节点 | 职责 | 输入 → 输出 | 调模型？ |
|---|---|---|---|
| `planner` | 识别意图、拆解步骤 | 问题、记忆 → `plan` | 是（结构化输出） |
| `retriever` | 查本地知识库 | 问题、`user_id` → `evidence` | **否**，就是三行 `rag.retrieve` |
| `solver` | 分步推理、调工具 | 证据、题目 → `draft_answer`、`tool_results` | 是（绑工具） |
| `tutor` | 教学化改写、控难度 | 证据、草稿、审核意见 → 最终答案 | 是 |
| `reviewer` | 核对引用、计算、幻觉 | 草稿、证据 → `review` | 是（结构化输出） |
| `clarify` | 答不了时反问用户 | 原因 → 反问 | 是 |

**四条路由**（`graph/builder.py`，都是纯函数——只读 state、不碰 IO，可直接单测）

| 路由 | 判断 |
|---|---|
| `route_by_intent` | `unclear` → 澄清；`chat` → 短路到 tutor；`calc` → 直接走 solver（跳过检索）；其余 → 检索 |
| `route_by_evidence` | 无证据且要工具 → solver（用 web_search 兜底）；无证据 → 澄清；有证据按是否要工具分流 |
| `route_after_tutor` | 有证据或调过工具才进 reviewer，纯寒暄跳过 |
| `route_after_review` | 通过 → 结束；打回且还有轮数 → tutor 重写；用尽 → 澄清 |

**四个设计上的选择**

1. **闲聊短路。** Planner 判为 `chat`（打招呼、道谢、问助手能力）时直接到 tutor，跳过 Retriever / Solver / Reviewer。实测"你好，你能做什么"只走 2 个节点。
2. **`calc` 跳过检索。** 纯计算不依赖资料。早先让它走检索的理由是"万一讲义里就有标准解法"，实测后果是：算术题被塞进 4 条无关分片，Tutor 把算出来的结果标上假引用 `[1]`，Reviewer 尽职地发现引用对不上并打回，两轮用尽后降级反问——**一个正确的算术题最后答不出来**。现在 `route_by_intent` 直接送 solver，`evidence` 为空，最终结果里不出现任何引用。
3. **引用只列正文真正引用过的编号。** `builder._cited_only` 按正文里的 `[n]` 过滤，没被引用就不显示。原先一律把检索到的全部列成引用，会出现"正文一个 `[n]` 都没标，下面却列了 4 条引用"。**编号必须沿用 `format_context` 的检索顺序编号**，不能把过滤后的列表交给 `format_citations`——那会从 1 重新编号，正文的 `[3]` 被标成 `[1]`。
4. **计算结果要标来源类别。** `TUTOR_SYSTEM` 里有「（模型常识）」「（来源：联网搜索）」「（计算所得）」三类，`_tool_notes()` 把工具实际执行结果喂给 Tutor，让它分得清哪些数值是算出来的。缺这一类时 Tutor 只能给计算结论瞎标 `[1]`。
5. **澄清节点一个顶三个。** 三种原因（`ambiguous` 问题太笼统 / `no_evidence` 资料里没找到 / `unverified` 审核不通过）共用 `clarify_node`，靠 `clarify_reason` 分派。图里用三个极小的标记节点（`mark_ambiguous` 等）把原因写进状态，比让澄清节点去猜"我是被谁叫来的"清楚。
6. **审核打回不是死循环。** `REVIEW_MAX_ROUNDS` 限死总轮数，用尽后按计划书 2.3 第 6 步降级为反问用户，而不是把没核实的答案硬塞出去。
7. **结构化输出只能用 `json_schema`。** 本项目的端点是 `qwen3.8-flash`，thinking 模式下 `tool_choice` 被禁，`method="function_calling"` 会直接 400；`json_mode` 又要求消息里出现 "json" 字样。三个里只有 `json_schema` 能用（2026-09 实测，见 `nodes.py` 的 `_structured`）。

**Reviewer 提示词的松紧调过两轮**，两个方向都翻过车，记录下来免得来回折腾：

- 第一版没给计算类内容任何来源类别 → Tutor 瞎标 `[1]` → Reviewer 正确地打回 → 算术题答不出来。修法是加「（计算所得）」。
- 加了之后 Reviewer 把"缺少标注"当成了**硬性要求**，连 `根号49=7` 这种完全正确的答案也被毙掉两轮（判词："请明确标注（计算所得）"）。修法是在提示词里明确列出「一律不算问题」的清单（缺标注、没标编号、措辞格式），并把判不通过收敛成三种情况：数值算错、引用编号支撑不住那句话、资料和工具都没有依据的幻觉。

**状态与对外契约**（`graph/state.py`）

- `TaskState` 只放**可序列化的纯数据**（工具轨迹存 `asdict` 后的 dict 而不是 `ToolTrace` 对象），这样 Phase 4 接 thread checkpointer 时不用返工。
- 对计划书 4.1 有三处有意偏离，都写在字段注释里：`evidence` 存 `list[Document]` 而非 `list[dict]`（为了零改动复用 rag 的三个 format 函数）；`history` 是 `list[dict]` 而非 `list[BaseMessage]`（直接对接 `memory.Memory`）；不加 `thread_id` / `image_context`（分属 Phase 4 / 5，现在加上去就是死代码）。
- `AgentResult` 字段与改造前的 `planner.AgentResult` 完全一致，所以 `main.py` 的渲染逻辑一行没改。

**流式事件**：`stream_mode=["custom", "values"]`。每个节点末尾发一条人类可读的 custom 事件（`get_stream_writer()`，节点被单独调用时抛 `RuntimeError` 会被吞掉——流式只是显示层，不该影响节点正确性）；`values` 每步回传全量状态，只取最后一条组装 `AgentResult`。`run()` 是非流式薄封装。

**验证到什么程度**

- ✅ 四条路径（闲聊短路 / 资料问答 / 工具调用 / 澄清）实测分叉正确。
- ✅ 审核打回重试用桩验证过。
- ⚠️ 多 Agent 后的**实际延迟没测**：资料问答路径从 1–2 次模型往返涨到 3 次（Planner + Tutor + Reviewer），计划书 7 的目标是 P95 ≤ 8s，目前无数据。
- ⚠️ 无单元测试（路由函数已是纯函数，补起来成本很低）。

---

### 2.5 会话记忆（Phase 4 的一小部分）

`memory.py`：一个会话一个 JSON 文件，存 `./data/memory/<会话名>.json`，重启后能接上。`window=20` 表示只把最近 20 条消息喂给模型。`--session 线性代数` 换文件，`/new` 清空。

**注意这不是计划书 4.1 设计的短期记忆**：不是 LangGraph checkpointer，`TaskState` 里没有 `thread_id` / `messages`，节点级中间状态不会被保存和恢复。它只是"整份历史按窗口裁剪后拼进 prompt"，没有摘要、没有压缩。真正的 checkpointer 归 Phase 4。

---

### 2.6 回归集与跑分（评估）

```bash
python evaluation/run.py            # 完整跑（检索 + 生成，消耗 token）
python evaluation/run.py --no-llm   # 只测检索，快且免费
python evaluation/run.py --top-k 6  # 覆盖题目文件里的 top_k
python evaluation/run.py --limit 10 # 只跑前 10 题
```

题目在 `evaluation/dataset.json`，38 题、`top_k=4`，其中 3 题标记 `should_refuse: true`（资料里本就没有答案，用来测模型会不会硬编）。覆盖面：`S2 - From Idea to Specification v20260906.pdf` 19 题、`AGENTS.md` 6 题、`literature.txt` 6 题、`SPEC.md` 4 题。

每题结构：

```json
{
  "id": "q001",
  "question": "一份规格说明书必须回答哪四个问题？",
  "expected_sources": [{"file_name": "S2 - From Idea to Specification v20260906.pdf", "page": 18}],
  "answer_keywords": ["WHO", "WHAT", "HOW", "breaks"],
  "should_refuse": false
}
```

输出四项指标：**Recall@k**（期望来源被检索命中的比例）、**引用准确率**（召回分片落在期望来源内的比例）、**拒答正确率**（关键词匹配口径见 `run.py:REFUSAL_MARKERS`）、**平均延迟**。跑完列出失败样例，用来定位是检索问题还是生成问题。退出码非 0 表示有失败题。

**当前基线**：`Recall@4 74.3%` / `引用准确率 18.6%` / `平均延迟 0.80s`（`--no-llm`，故拒答率与关键词命中两项无意义）。计划书 7 定的目标是引用准确率 ≥ 90%、核心回归题正确率 ≥ 80%——**引用准确率差距最大**，是下一步的主要着力点。

---

### 2.7 辅助工具：向量库查看器

`inspect_store.py` 绕过 Chroma 客户端直接读 `chroma.sqlite3`，把二进制内容翻成可读文本。它复用了线上的 `rag.retriever.format_locator` 渲染位置，所以**看到的就是检索时喂给模型的那份数据**。用途：排查"文件放进去了但检索不到"、核对切分粒度、确认元数据对不对。

---

### 2.8 可观测性（部分）

- ✅ **工具轨迹**：本地 JSONL，见 2.3。
- ⚠️ **LangSmith**：`.env` 里 `LANGSMITH_TRACING=true` 且已配 key 与 endpoint（`apac.api.smith.langchain.com`），靠 `config.py` 的 `load_dotenv()` 把变量灌进进程环境，由 langchain 自己读取上报。`config.py` 里的 `langsmith_project` / `langsmith_tracing` 两个字段**没有任何代码消费**，只是记录。是否真的有 trace 上报需要去 LangSmith 项目页面确认。
- ❌ 节点耗时统计、token/成本统计、prompt 脱敏保存都还没有。

---

## 三、未完成部分

### 3.1 Phase 1 收尾（RAG 质量）

| 缺什么 | 为什么需要 | 入口在哪 |
|---|---|---|
| **混合检索**：BM25/关键词 + 向量召回，再 rerank | 计划书 3.2 的核心设计。当前失败样例的两类原因（跨语言、多主题分片稀释）都是纯向量召回的固有弱点，靠重排能缓解 | `rag/retriever.py:retrieve` |
| **检索相关度阈值** | 没有阈值时 `k=4` 且库里够 4 条就必定返回 4 条（哪怕分数只有 0.38），于是"没找到就反问"分支**实际不可达**（见 4.1）。修法是改用 `similarity_search_with_relevance_scores` 加下限，**阈值必须拿回归集调**，否则会误伤真正相关的弱匹配 | 同上 |
| **Word / docx 加载** | 计划书 1.2 承诺支持 PDF、Markdown、**Word**、TXT，目前只做了三种 | `rag/loader.py` |
| **表格 / 代码的专用切分策略** | 计划书 3.2 明确要求，目前一律走通用切分 | `rag/splitter.py` |
| **更细的分片粒度或按条目切** | 直接针对"多主题分片稀释" | `rag/splitter.py` |
| **重排后的引用编号一致性** | 引用准确率 18.6% 说明 top_k 里通常只有 1 条真正相关 | `rag/retriever.py` |

### 3.2 Phase 2 收尾（工具）

| 缺什么 | 说明 |
|---|---|
| **`code_runner` 沙箱** | 计划书 3.3 与目录结构里都列了这个工具（代码交给沙箱执行），**完全没有实现**。它的安全要求最高（沙箱逃逸），需要单独设计 |
| **`web_search` 真实结果质量验证** | key 已配、工具已装配，但没有留下成功搜索的验证记录与质量评估 |
| **按工具分别限额** | 当前 `ToolBudget` 是全局额度，模型连调 10 次 `calculate` 会把 flashcard 的额度也耗光（代码注释里已记这点） |

### 3.3 Phase 3 收尾（多 Agent）

| 缺什么 | 说明 |
|---|---|
| **多 Agent 实际延迟测量** | 资料问答路径 3 次模型往返，计划书 7 的 P95 目标是 8s，无数据。若超标，最省事的缓解是给 Reviewer 单独绑更小的模型 |
| **路由函数单元测试** | 四个路由已是纯函数，`PROJECT_PLAN 7` 要求的"状态路由"单测可以直接 import，成本很低 |
| **引用/编号的程序化校验** | 引用**列表**已能按正文实际引用过滤（`_cited_only`），但正文里的 `[n]` 是否指向正确的片段，仍完全靠 Reviewer 的模型判断，没有规则兜底 |

### 3.4 Phase 4：记忆（整块未开始）

| 缺什么 | 对应计划书 |
|---|---|
| **LangGraph checkpointer + `thread_id` 的短期记忆** | 3.5。替代或共存于现在的 `memory.py` 方案；`TaskState` 已按可序列化准备（工具轨迹存 `asdict`），接的时候不用返工 |
| **长期学习档案**（以 `user_id` 为键：目标考试、掌握度、错题、偏好、最近复习时间） | 3.5 |
| **记忆提取器 + 置信度阈值** | 3.5。防止把模型猜测当成事实写进长期记忆 |
| **查看 / 修改 / 删除 / 导出接口、TTL 与隐私策略** | 3.5、4.2、检查清单 |
| **对话上下文摘要/压缩** | 3.5。现在只有"截最近 20 条"这一种策略，长对话会直接丢早期信息 |

### 3.5 Phase 5：多模态与产品化（整块未开始）

| 缺什么 | 说明 |
|---|---|
| **Vision 多模态读图** | 计划书 3.6：图片预处理 → Vision 模型抽取题目/公式/图表文字 → 标注"来自图片的识别内容"→ 低置信度要用户确认。`rag/loader.py` 里曾有过图片分支，改成包结构时移除了 |
| **界面 + 显式的「入库（RAG）」按钮** | `PROJECT_PLAN.md` Phase 5 新增的硬要求：上传只落盘，**点一下才分片入库**；按钮旁显示每个文件的索引状态（未入库 / 入库中 / 已入库 N 分片 / 错误原因）。这是为了解决"文件放进去了但检索不到"的第一屏困惑 |
| **API 层** | 计划书 4.2 的 6 个端点（`/v1/chat/stream`、`/v1/files` 等），含 SSE 流式 |
| **鉴权、限流、用户级隔离** | 检查清单要求"文件和记忆具备用户级隔离、删除和错误恢复" |
| **容器化与部署** | Docker Compose（开发）、API/worker/DB/向量库分离（生产） |
| **成本控制** | 相同查询的 embedding / 搜索结果缓存、长对话摘要、简单问题用小模型、token 与并发上限 |
| **在线可观测性** | 节点耗时、工具错误、用户反馈、prompt 脱敏 |

### 3.6 工程项

| 缺什么 | 说明 |
|---|---|
| **`tests/`**（单元 + 集成 + 安全测试） | 计划书 7 列了一整套；现在一个测试目录都没有 |
| **CI** | Phase 0 要求，未做 |
| **`.env.example`** | `README.md` 让用户"复制 `.env.example` 为 `.env`"，但这个文件不存在。`.gitignore` 里已留白名单 |
| **依赖锁定** | Phase 0 要求"依赖锁定"，`requirements.txt` 目前一个版本号都没有，踩到上游 breaking change 时不可复现 |
| **`pyproject.toml`** | 计划书目录结构里有；现在只能靠 `pip install -r` |
| **`scripts/`** | 计划书 6.1 写的是 `python scripts/ingest.py --path ./data`，实际实现改成了 CLI 的 `--ingest`（更省事），但文档没对齐 |
| **logging 框架** | 全程 `print`，没有日志级别、没有落盘日志 |

---

## 四、已知问题清单

按"用户能感知的程度"排序。

### 4.1 检索没有相关度阈值，"没找到就反问"仍是死代码 ⚠️ 中

`retrieve` 用 `similarity_search` 且无分数下限，`k=4` 时只要库里够 4 条就必定返回 4 条。于是拿资料里没有的问题去问，agent 收到 4 条无关分片后靠自己知识兜底（标「模型常识」），**不会**走 `mark_no_evidence → clarify` 说"资料里没找到"。计划书 2.3 第 6 步设计的降级路径在 `qa` 这条路上仍失效。

**已用两个确定性办法缓解，不动召回率**：

1. `calc` 跳过检索（`builder.route_by_intent`）。纯计算不依赖资料，走检索只会召回到与题目毫不相关的分片，让 Tutor 把算出来的结果标上假引用 `[1]`、再被 Reviewer 正确地打回。跳过之后 `evidence` 为空，最终结果里不会出现任何引用。
2. 引用只列正文真正引用过的编号（`builder._cited_only`）。原先一律把检索到的全部列成引用，会出现"正文一个 `[n]` 都没标，下面却列了 4 条引用"。注意编号必须沿用 `format_context` 的检索顺序编号，不能把过滤后的列表交给 `format_citations`——那会从 1 重新编号，正文的 `[3]` 被标成 `[1]`。

**阈值方案已实测否决**（2026-09，38 题回归集，`k=10` 打分后按阈值过滤取前 4）：

| 阈值 | Recall@4 | 过滤后无结果的题目 |
|---|---|---|
| 0.00（现状） | 74.3% | 0/35 |
| 0.45 | 71.4% | 1/35 |
| 0.50 | 65.7% | 4/35 |
| 0.55 | 62.9% | 6/35 |
| 0.60 | 51.4% | 12/35 |

原因是两类分数的分布重叠严重：相关分片 `n=29` 最低 0.415 / 中位 0.640；无关分片 `n=321` 最低 0.321 / 中位 0.506 / **最高 0.753**（比相关分片的 25% 分位还高）。没有干净的分界点——0.60 能挡掉 86% 的无关分片，但要牺牲 23 个百分点的召回。

**结论：绝对分数阈值是个钝器，不建议启用。** 要让"没找到就反问"真正生效，正确方向是混合检索 + reranker（`PROJECT_PLAN 3.2`），让相关与无关在统一打分下拉开距离。

### 4.2 `--session` 会让检索召回为空 ⚠️ 高（用户可见）

`main.py:154` 把会话名当 `user_id` 传给检索做过滤，但 `do_ingest()` 建索引时用的是默认 `user_id="default"`，两者对不上。所以 `python main.py --session 数学` 会**一条资料都召不到**（记忆文件倒是正常）。

两条修法，取决于想要什么：按用户隔离 → 让 `do_ingest` 也带上 `user_id`；只是给记忆换个文件名 → 停止用 `--session` 过滤检索。Phase 3 之前就存在的老问题。

### 4.3 引用准确率 18.6%，远低于 90% 目标 ⚠️ 高

top_k=4 里通常只有 1 条真正相关，说明召回精度低。是计划书 7 的硬指标差距所在，也是 3.1 那几个待办要解决的核心问题。

### 4.4 上传后必须手动 `/ingest`，且界面不讲清楚 ⚠️ 中

agent 在对话里读不到 `data/uploads/` 的文件本身。这是**有意的设计**（索引离线、对话在线），但容易让人以为"把文件放进去 agent 就该能读"。Phase 5 的入库按钮与索引状态显示就是为了消掉这个困惑。

### 4.5 `make_flashcards` 一次工具调用 = 两次模型往返 ⚠️ 中（估算失真）

它内部会调一次模型生成卡片，但这不计入 `ToolBudget`，也不体现在 `MAX_TOOL_CALLS` 上。做延迟与成本估算时要按"两次往返"算。

### 4.6 工具超时不强杀线程 ⚠️ 低（将来会变高）

超时后线程不会被终止，只是调用方不再等待。对联网搜索影响不大；**将来若加入会改文件的工具（如 `code_runner`），要知道它可能仍在后台跑完。**

### 4.7 `ToolTracer.save()` 追加写且不清空 `records` ⚠️ 低

同一 `run_id` 重复保存会写出重复行。目前只有 `solver` 调它一次、`run_id` 带时间戳，不会重复；以后若有节点重跑 solver 要注意。

### 4.8 基线语料与当前库不一致 ⚠️ 低

见 2.2 末。README 记的基线是 5 份资料，当前是 7 份 / 145 分片。复跑一次即可对齐。

### 4.9 `steps` 计数口径 ⚠️ 很低（仅显示）

`run_stream` 用"收到的 custom 事件数"当步骤数，而三个标记节点不发事件。所以走澄清路径时显示的步骤数比实际经过的图节点少 1。只影响终端显示。

### 4.10 `rag.reset()` 没有入口 ⚠️ 很低

`store.reset()` 清空向量库的函数存在并已导出，但**没有任何 CLI 命令或代码调用它**。想清空索引目前只能手动删 `data/chroma/`（并注意清单也要一起删，否则下次入库会因哈希相同而跳过）。

### 4.11 结构化输出被截断会丢掉整轮对话 ⚠️ 中（已知会真实发生）

`planner` 和 `reviewer` 用 `with_structured_output(..., method="json_schema")` 拿 JSON。模型是逐 token 生成的，一旦中途停下，JSON 就是残缺的——大括号没闭合、字符串没收尾，解析必然失败：

```
Invalid JSON: EOF while parsing a string at line 1 column 5724
input_value='{"passed": false, "issue...uggestion_simplified_s}'
```

关键在于**已写好的字段也全部作废**：`"passed": false` 明明完整出现了，解析器仍不会给你半个对象。异常从节点经 `run_stream` 冒到 `main.py` 的 `except`，打印 `[出错]` 后丢掉整轮——**而那时 Tutor 已经产出答案了**，只是 Reviewer 在审它。

已实测发生过两次：一次是 Reviewer 的 `issues` 列表写得太长（上游 Tutor 编了假引用，Reviewer 逐条驳斥），另一次是某个节点 `APITimeoutError`。

**触发条件没有完全定位。** 曾怀疑是输出 token 打满，但实测端点默认上限至少有 8840 token（13979 字符）才 `stop`，而那次只截断在 5724 字符，远未到顶。可能是 thinking 模式的推理 token 额外吃掉了额度，也可能另有机制。

修法（未实施）：`with_structured_output(..., include_raw=True)` 拿原始响应，解析失败时降级为"审核不可用，放行"而不是让异常吃掉整轮；同时给 `issues` 限条数、给模型设 `max_tokens`。更彻底的是在图外层兜底：任何节点抛异常都用已有 `draft_answer` 作答，并标明哪一步失败了。

---

## 五、建议的下一步（按性价比排序）

1. **给 `planner` / `reviewer` 的结构化输出加容错**（半天）。见 4.11——这是当前唯一会**丢掉整轮对话**的缺陷，实测已发生两次，代价最高。解析失败时降级放行 + 图外层兜底，两处都不大。
2. **补路由函数的单元测试 + 一个 `tests/` 目录**（半天）。四个路由是纯函数，`pytest` 直接 import 就能测；顺手把 CI 搭起来，Phase 0 就算真正收口。
3. **混合检索 + 重排**（投入最大）。它同时解决两个问题：召回质量（4.3 引用准确率 18.6%）和 4.1 那条失效的降级路径——**单纯加绝对分数阈值已被实测否决**（见 4.1 的表），因为相关与无关的分数分布重叠严重，没有干净分界点。只有让两者在统一打分下拉开距离，"没找到"才判断得准。
4. **修 `--session` 的 `user_id` 不一致**（很便宜）。哪怕决定"暂时不做用户隔离"，也应先把检索过滤这条路径修对，否则这个功能会持续误导人。
5. **测一次多 Agent 的真实延迟**，对齐计划书 P95 ≤ 8s 的目标；超标就先给 Reviewer 换小模型。
6. 补 `.env.example` + 锁依赖版本 + `pyproject.toml`（一小时级），把新环境启动这条路走顺。
7. 之后再进 Phase 4（记忆）或 Phase 5（多模态 + 界面）——两者的取舍建议先做 Phase 4：长期记忆是"个性化学习助手"这个定位的核心卖点，而界面的主要收益是消除 4.4 的困惑，可以稍后。
