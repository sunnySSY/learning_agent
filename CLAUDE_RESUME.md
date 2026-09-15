# 简历项目描述（依据 2026-09-13 的 PROGRESS.md / PROJECT_PLAN.md / README.md 与代码实测）

日期占位 `2026.0X` 请替换为实际起始月份。所有数字均可在仓库或文档中找到出处（见文末核对表）。

---

## English (primary)

**Study Helper Agent | Multi-Agent RAG Study Assistant | End-to-End Agent Development | 2026.0X – Present**
Python · LangChain · LangGraph · ChromaDB · Qwen (DashScope) · text-embedding-v4 · Tavily · Pydantic · LangSmith

Designed and built a conversational study agent over users' own course materials, solving three core problems — cited Q&A over local documents, tool-assisted problem solving, and review-material (flashcard) generation — via a Planner / Retriever / Solver / Tutor / Reviewer LangGraph workflow; validated with a 38-question regression suite (Recall@4 74.3%).

- **Multi-agent orchestration (LangGraph):** Replaced a hand-written single-agent loop with a state graph of 6 role-specific nodes + 4 pure-function conditional routers (intent / evidence / post-tutor / post-review), fixing prompt bloat and unobservable control flow. Chat intents short-circuit to a 2-node Planner→Tutor path; Reviewer rejections are capped by `REVIEW_MAX_ROUNDS=2` and degrade to a clarification turn instead of emitting unverified answers; one Clarify node serves 3 failure reasons (ambiguous / no evidence / unverified) via marker nodes. All 4 execution paths verified end-to-end; routers are IO-free and unit-testable.
- **Structured output under provider constraints:** Qwen thinking mode disables `tool_choice` (function-calling method returns HTTP 400) and `json_mode` requires the literal word "json" in the prompt; standardized Planner/Reviewer on `json_schema` structured output backed by Pydantic models so plans and verdicts are typed, not parsed from free text.
- **Traceable RAG pipeline:** PDF/Markdown/TXT loader → structure-aware chunking (Markdown header splitter preserving heading paths; 800/120-char windows with line-offset back-mapping for TXT) → text-embedding-v4 (1,024-dim, asymmetric query/document encoding, batch ≤10 per measured API limit) → ChromaDB cosine search. Citations render as PDF page / Markdown heading path / TXT line range so every claim is verifiable against the source; current corpus 8 documents → 164 chunks.
- **Incremental, idempotent indexing:** SHA-256 + size/mtime manifest classifies each file into added / updated / skipped / empty / removed / failed; re-ingesting an unchanged corpus makes 0 embedding calls; updated files drop stale chunks before re-insert and deleted files' vectors are purged to prevent orphan hits. Manifest is treated as a cache, not truth — a corrupt manifest triggers a full rebuild rather than silent skips.
- **Tool harness with 4 guardrails:** Centralized in a registry so no tool re-implements them — signature-inferred schema, thread-pool timeout (15 s, Windows-safe), retry with retryable/non-retryable classification (401/403 fail fast, 429/5xx retry; cut wasted time on auth failures from 4.95 s → 1.72 s), and a per-turn call budget (6) that hands a "rejected" result back to the model instead of crashing. Tool failures are returned as data so the model recovers or truthfully reports, rather than fabricating. Calculator evaluates via AST with an operator whitelist and exponent cap (no `eval`).
- **Observability:** Per-call JSONL tool traces (name / args / status / duration / attempts / preview / error), streamed per-node progress events via LangGraph custom stream mode, LangSmith tracing, and a SQLite-level vector-store inspector that reuses production citation formatting so what you inspect is exactly what the model sees.
- **Evaluation & failure analysis:** Built a 38-question regression set (3 should-refuse) scoring Recall@k, citation precision, refusal accuracy, and latency; retrieval-only baseline Recall@4 = 74.3%, mean retrieval latency 0.80 s. Root-caused misses into two non-random classes — cross-lingual retrieval (zh queries over en docs) and multi-topic chunk dilution — which set the roadmap for hybrid BM25 + rerank.
- **Session memory:** JSON-persisted per-session history with a 20-message window; `TaskState` kept fully serializable (tool traces stored as dicts) so a LangGraph checkpointer can be added without refactoring.

---

## 中文版（对应）

**学习助手智能体 Study Helper Agent｜多 Agent RAG 学习助手｜Agent 全链路开发｜2026.0X – 至今**
Python · LangChain · LangGraph · ChromaDB · Qwen（百炼）· text-embedding-v4 · Tavily · Pydantic · LangSmith

设计并实现面向个人课程资料的对话式学习 Agent，解决「本地资料带引用问答 + 工具辅助解题 + 复习卡片生成」三层核心问题，采用 Planner / Retriever / Solver / Tutor / Reviewer 的 LangGraph 工作流；以 38 题回归集验证（Recall@4 74.3%）。

- **多 Agent 编排（LangGraph）：** 将手写单 Agent 大循环重构为 6 个职责单一节点 + 4 个纯函数条件路由（意图 / 证据 / 讲解后 / 审核后）的状态图，解决单 prompt 膨胀与控制流不可观测问题；闲聊意图短路为 Planner→Tutor 两节点路径；审核打回受 `REVIEW_MAX_ROUNDS=2` 限轮，用尽后降级为反问而非输出未核实答案；一个 Clarify 节点通过标记节点服务 3 种失败原因（含糊 / 无证据 / 未通过审核）。4 条执行路径全部端到端验证；路由函数无 IO、可直接单测。
- **供应商约束下的结构化输出：** Qwen thinking 模式禁用 `tool_choice`（function-calling 方式直接 400），`json_mode` 又要求 prompt 出现 "json" 字样；将 Planner / Reviewer 统一为 `json_schema` + Pydantic 模型的结构化输出，计划与审核结论为类型化数据而非从自由文本解析。
- **可追溯的 RAG 链路：** PDF/Markdown/TXT 加载 → 结构感知切分（Markdown 按标题层级切并保留标题路径；TXT 以 800/120 字符窗口切分并反查行号）→ text-embedding-v4（1,024 维、query/document 非对称编码、按实测 API 上限每批 ≤10 条）→ ChromaDB 余弦检索。引用按 PDF 页码 / Markdown 标题路径 / TXT 行号区间三种格式渲染，每条结论可回溯原文；当前语料 8 份文档 → 164 个分片。
- **增量幂等入库：** 基于 SHA-256 + size/mtime 清单把文件分为新增 / 更新 / 跳过 / 无文本 / 已删除 / 失败六类；语料未变时重复入库 embedding 调用为 0；更新文件先删旧分片再写入，删除文件同步清理向量，避免孤儿分片被召回。清单定位为「缓存而非真相」，损坏时触发全量重建而非静默跳过。
- **工具执行四重护栏：** 集中在注册层实现，各工具不重复造轮子——签名推断 schema、线程池超时（15 s，兼容 Windows）、区分可重试/不可重试的重试策略（401/403 快速失败，429/5xx 重试；认证失败的无效等待从 4.95 s 降至 1.72 s）、单轮调用预算（6 次）超限时把 "rejected" 结果交回模型而非崩溃。工具失败以数据形式返回，让模型自愈或如实告知而非编造；计算器基于 AST 白名单求值并限制幂指数，不走 `eval`。
- **可观测性：** 每次工具调用落 JSONL 轨迹（名称 / 参数 / 状态 / 耗时 / 尝试次数 / 预览 / 错误），LangGraph custom stream 逐节点推送进度事件，接入 LangSmith trace，并提供直读 SQLite 的向量库查看器（复用线上引用格式化，所见即模型所见）。
- **评估与失败归因：** 构建 38 题回归集（含 3 道应拒答题），输出 Recall@k / 引用准确率 / 拒答正确率 / 延迟四项指标；纯检索基线 Recall@4 = 74.3%、平均检索延迟 0.80 s。将失败归因为两类非随机问题——跨语言检索（中文提问 / 英文资料）与多主题分片语义稀释——由此确定混合 BM25 + 重排的下一步路线。
- **会话记忆：** 按会话 JSON 持久化历史并取最近 20 条作为上下文窗口；`TaskState` 保持全部可序列化（工具轨迹存 dict），后续接 LangGraph checkpointer 无需返工。

---

## 核对依据（不放进简历）

| 表述 | 出处 |
|---|---|
| 6 节点 / 4 路由 / 2 节点闲聊短路 / REVIEW_MAX_ROUNDS=2 / 3 种澄清原因 | `graph/builder.py`、`graph/nodes.py`、README「多 Agent 图」 |
| json_schema 是唯一可用的结构化输出方式 | `graph/nodes.py:_structured`（2026-09 实测注释） |
| 800/120 **字符**、1,024 维、batch ≤10、非对称 text_type | `rag/splitter.py`、`rag/embedder.py`、README「阿里云 Embedding 使用规则」 |
| 8 份文档 / 164 分片 | 2026-09-13 只读核对（PROGRESS.md 记的 7/145 为更早记录） |
| 六类入库结果、0 embedding 调用 | `rag/pipeline.py:SyncResult`、README「入库」实测输出 |
| 15 s / 2 次重试 / 6 次预算、4.95 s→1.72 s | `tools/registry.py`、config 默认值、PROGRESS.md 2.3 |
| 38 题 / 3 拒答 / Recall@4 74.3% / 0.80 s | `evaluation/dataset.json`（脚本实测 38/3）、PROGRESS.md 2.6 |

**面试口径注意：**
- Recall@4 74.3% 是脚本口径 29/38，其中 3 道拒答题在 `--no-llm` 下自动记通过；严格算有来源题为 26/35。
- 0.80 s 只含查询向量化 + 检索，不含生成；多 Agent 全图 P95 **未测**，不要口头承诺 ≤8 s。
- 引用准确率 18.6% 未写入简历（远低于目标）；被问到时说明它统计的是「召回分片落在期望来源的比例」，以及混合检索/重排的改进计划。
- `web_search` 只验证过错误路径，真实搜索质量未评估。
- **`reviewer_node` 目前没有把 `draft_answer` 放进审核输入**（`_messages` 只拼 system + history + question），因此简历只写「限轮打回与降级路由」，不写「审核降低幻觉率」。修复后（把草稿拼进 system 或 messages）再补评测，才能升级这条表述。
