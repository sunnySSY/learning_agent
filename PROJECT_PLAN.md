# Study Helper Agent 项目规划

> 基于 LangChain 的多模态、多智能体学习助手。本文是项目的设计与实施规划，适用于从空仓库开始搭建 MVP，并逐步扩展到可用于作品集和简历展示的版本。

## 1. 项目定位与目标

### 1.1 解决的问题

学生通常需要在多个学习资料、搜索引擎和笔记应用之间切换。本项目提供一个统一入口，能够理解自然语言问题，检索用户上传的本地资料，调用外部工具补充信息，结合对话上下文给出分层讲解，并持续记录学习进度。

### 1.2 核心目标

- **资料问答**：对 PDF、Markdown、Word、TXT 等本地资料建立向量索引，回答时优先引用原文。
- **工具调用**：在需要最新信息、计算或代码执行时，通过结构化 tools calling 调用相应工具。
- **短期记忆**：在当前会话内记住用户的目标、已知程度和上下文，不重复询问。
- **多智能体协作**：将检索、解题、讲解、复习规划等职责拆分为可测试的 Agent。
- **多模态输入**：识别用户上传的公式、题目截图、图表和手写内容，并转为可推理文本。
- **长期记忆**：跨会话保存学科、知识点掌握度、错题和学习计划，支持个性化复习。

### 1.3 非目标（第一版暂不做）

- 不替代专业教师，不对医疗、法律等高风险问题给出权威结论。
- 不默认永久保存原始文件和图片；用户可删除索引、记忆和会话。
- 不在第一版实现复杂的实时协同编辑、自动批改整套试卷或自研大模型。

## 2. 整体架构

### 2.1 分层架构

```text
客户端（CLI / Streamlit / Web API）
        |
API 层：会话、文件上传、索引管理、记忆管理、流式回答
        |
编排层：LangGraph 状态图（路由、循环、重试、人工确认）
        |
Agent 层：Planner / Retriever / Solver / Tutor / Reviewer
        |
能力层：RAG、Tools、Vision、Short-term Memory、Long-term Memory
        |
基础设施：LLM、Embedding、Vector Store、SQL/Redis、对象存储、日志与评估
```

### 2.2 推荐目录结构

```text
study-helper-agent/
├─ app/
│  ├─ main.py                 # FastAPI 或 Streamlit 入口
│  ├─ api/                    # HTTP 路由、请求/响应模型
│  └─ dependencies.py         # 配置、依赖注入、生命周期
├─ src/study_agent/
│  ├─ config.py               # pydantic-settings 配置与环境变量
│  ├─ schemas.py              # Message、Citation、StudyProfile、TaskState
│  ├─ graph/
│  │  ├─ state.py             # LangGraph 共享状态
│  │  ├─ builder.py           # 编译主图和子图
│  │  └─ nodes.py             # 路由、检查、降级节点
│  ├─ agents/
│  │  ├─ planner.py           # 拆解任务并选择工作流
│  │  ├─ retriever.py         # 本地资料检索与重排
│  │  ├─ solver.py            # 题目/代码/数学推理
│  │  ├─ tutor.py             # 按用户水平生成教学式回答
│  │  └─ reviewer.py          # 事实、引用、格式和安全检查
│  ├─ rag/
│  │  ├─ loaders.py           # 文档加载与元数据提取
│  │  ├─ splitter.py          # 按标题/语义切分
│  │  ├─ indexer.py           # embedding、批量入库、删除
│  │  └─ retriever.py         # hybrid search、过滤、rerank
│  ├─ tools/
│  │  ├─ registry.py          # 工具注册与权限策略
│  │  ├─ web_search.py        # 网络搜索
│  │  ├─ calculator.py        # 安全数学计算
│  │  ├─ code_runner.py       # 沙箱代码执行
│  │  └─ flashcard.py         # 生成/保存复习卡片
│  ├─ memory/
│  │  ├─ short_term.py        # checkpointer + thread_id
│  │  ├─ long_term.py         # 学习档案、知识点和事件
│  │  └─ extraction.py        # 从对话提取可持久化事实
│  ├─ multimodal/
│  │  ├─ image_parser.py      # 图片预处理、Vision 模型调用
│  │  └─ prompt.py             # 图像题目结构化提示词
│  ├─ prompts/                # 版本化系统提示词
│  └─ observability/           # tracing、指标、成本统计
├─ tests/                     # 单元、集成、评估数据集
├─ scripts/                   # ingest、清理、离线评估
├─ data/                      # 本地开发资料（加入 .gitignore）
├─ .env.example
├─ docker-compose.yml         # 可选：Postgres、Redis、向量库
├─ pyproject.toml
└─ README.md
```

### 2.3 端到端请求流程

1. 客户端提交文本、图片或文件，并携带 `user_id` 与 `thread_id`。
2. API 层校验文件类型、大小和权限；图片进入 Vision 解析，文件进入异步索引队列。
3. 主图读取短期状态和用户长期学习档案，由 Planner 判断任务类型：资料问答、图片解题、开放搜索、复习规划等。
4. Retriever 对本地资料执行 metadata filter、向量/关键词混合检索和重排，返回带来源的 `Document`。
5. Solver 必要时调用计算器、代码运行器或 Web Search；Tutor 将证据转成符合用户水平的解释。
6. Reviewer 检查引用是否支持结论、是否存在计算错误、是否泄露不应输出的内容；失败则重试或降级为澄清问题。
7. 以流式事件返回答案、引用、使用的工具和建议的下一步；同时异步提取长期记忆。

## 3. 设计思路与原因

### 3.1 为什么选择 LangChain + LangGraph

LangChain 负责模型、提示词、Retriever 和工具的标准化接口；LangGraph 负责有状态编排、条件路由、循环重试和 checkpoint。相比一个超长 Agent prompt，图结构更容易观察、测试和限制成本，也能明确每个节点的输入输出。

### 3.2 RAG 设计

- **摄取**：读取文件并保留 `user_id`、`file_id`、页码、标题、章节等元数据。
- **切分**：优先按标题和段落切分，控制约 500–800 tokens，保留 80–120 tokens overlap；表格和代码使用专用策略。
- **检索**：先用向量检索召回，再用 BM25/关键词补召回，最后用 reranker 排序；按用户和文件权限过滤。
- **生成**：提示词要求“只根据证据回答；无法确认时明确说明”，输出 `answer + citations[]`。
- **更新**：文件哈希作为幂等键；重新上传只更新变化部分，删除文件同时删除对应向量。（已实现，见 `rag/manifest.py` 与 `/ingest`）

这样可以减少幻觉并让答案可追溯；纯微调不能及时反映用户新上传的资料，也不适合频繁更新知识。

### 3.3 Tools calling 设计

工具使用 Pydantic 输入 schema、明确的描述和超时。Planner 通过意图和新鲜度判断是否调用工具：本地资料优先回答，时效性问题使用 Web Search，数学计算交给 Calculator，代码交给沙箱。所有工具调用记录名称、参数摘要、耗时和结果状态；网络和代码工具默认需要安全策略或用户确认。

### 3.4 Multi-Agent 设计

采用“监督式路由 + 专家子 Agent”而不是多个 Agent 自由对话：

| Agent | 职责 | 输入 | 输出 |
|---|---|---|---|
| Planner | 识别意图、拆解步骤、选择工具 | 用户请求、记忆 | `plan[]` |
| Retriever | 查询本地知识库、返回证据 | 查询、过滤条件 | 文档片段和引用 |
| Solver | 进行分步推理或代码/计算 | 题目、证据、工具结果 | 解题过程、结论 |
| Tutor | 教学化改写、控制难度 | 结论、用户水平 | 最终草稿 |
| Reviewer | 验证正确性、引用和安全 | 草稿、证据 | 通过/修改意见 |

职责分离降低单个 prompt 的复杂度，并允许对高风险节点单独评估；统一状态和有限循环则避免 Agent 之间无限调用。

### 3.5 记忆设计

- **Short-term memory**：以 `thread_id` 为键，使用 LangGraph checkpointer 保存消息、计划和中间结果；设置窗口或摘要，避免上下文无限增长。
- **Long-term memory**：以 `user_id` 为键保存结构化事实，例如目标考试、掌握度、错题、偏好和最近复习时间。长期记忆必须经过提取器和置信度阈值，避免把模型猜测当成事实。
- **遗忘与隐私**：提供查看、修改、删除接口；敏感信息不写入长期记忆，设置 TTL 和数据导出能力。

短期记忆服务于当前任务连贯性，长期记忆服务于跨会话个性化，两者存储和生命周期分离可以降低隐私与上下文成本。

### 3.6 多模态设计

图片进入统一消息协议：先做尺寸、方向、压缩和敏感信息检查，再由 Vision 模型提取题目、公式、图表文字和不确定区域。解析结果必须标注“来自图片的识别内容”，Solver 仍可结合 RAG 和 Calculator；低置信度时要求用户确认，而不是静默猜题。

## 4. 数据与接口契约

### 4.1 核心状态

```python
class TaskState(TypedDict):
    user_id: str
    thread_id: str
    messages: list[BaseMessage]
    query: str
    image_context: dict | None
    plan: list[dict]
    evidence: list[dict]
    tool_results: list[dict]
    draft_answer: str
    citations: list[dict]
    review: dict | None
```

### 4.2 建议 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/v1/chat/stream` | 文本/图片对话，SSE 返回事件 |
| `POST` | `/v1/files` | 上传文件。**只落盘，不自动入库**——入库由界面上的「入库（RAG）」按钮触发，见 Phase 5 |
| `GET` | `/v1/files` | 查看用户文件和索引状态 |
| `DELETE` | `/v1/files/{id}` | 删除文件及向量 |
| `GET/DELETE` | `/v1/memory` | 查看或删除长期学习档案 |
| `GET` | `/v1/threads/{id}` | 查看会话摘要和消息 |

## 5. 实施路线

### Phase 0：工程基线

创建 Python 项目、依赖锁定、`.env.example`、日志、异常模型和基础 CI。至少配置 `LLM_API_KEY`、`EMBEDDING_MODEL`、`VECTOR_STORE_URL`、`DATABASE_URL`。

### Phase 1：RAG MVP

实现文件上传、加载、切分、embedding、向量检索和带页码引用的问答。先支持 PDF/Markdown/TXT；写入 20–50 个固定问题作为回归集。

### Phase 2：单 Agent + Tools

增加 Planner、Web Search、Calculator 和 Flashcard 工具；限定工具 schema、超时、重试和调用次数，记录每次 tool trace。

### Phase 3：LangGraph Multi-Agent

把 Planner、Retriever、Solver、Tutor、Reviewer 编成主图；加入条件分支、审核失败重试、无法检索时的澄清节点和流式事件。

### Phase 4：记忆

先接入 thread checkpointer，再实现长期记忆提取、知识点状态和复习计划。补充用户删除和导出能力。

### Phase 5：多模态与产品化

接入 Vision 模型、图片题目流程、Web/Streamlit 界面、鉴权、限流、Docker 部署和可观测性。

界面必须提供**显式的「入库（RAG）」按钮**：用户上传文件后，由他点一下才执行分片与向量化，而不是上传即自动入库。上传和入库要分成两个动作——入库耗时且有成本（调用 embedding），自动触发会让用户在上传大文件时莫名等待，也说不清"为什么刚传的资料查不到"。按钮旁要能显示每个文件的索引状态（未入库 / 入库中 / 已入库 N 个分片）和错误原因，让"文件放进去了但检索不到"这类困惑在第一屏就能自解。对应 4.2 的 `POST /v1/files` + `GET /v1/files` 两个端点。

## 6. 如何实现与使用

### 6.1 本地开发

1. 安装 Python 3.11+，创建虚拟环境并安装锁定依赖。
2. 复制 `.env.example` 为 `.env`，填写模型、Embedding、数据库和搜索工具密钥。
3. 启动 Postgres/Redis/向量数据库（开发阶段可用 Chroma/SQLite）。
4. 执行 `python scripts/ingest.py --path ./data` 建立资料索引。
5. 启动 `uvicorn app.main:app --reload`，或运行 Streamlit 客户端。
6. 上传资料后，在同一 `thread_id` 连续提问；跨会话使用同一 `user_id` 验证长期记忆。

### 6.2 典型用法

- “根据我上传的线性代数讲义，解释特征值，并引用页码。”
- 上传题目截图：“先识别题目，再分步骤提示，不要直接给最终答案。”
- “比较这两份资料的定义差异，并制作 10 张复习卡片。”
- “总结我本周的错题，给出下周复习计划。”

## 7. 测试、评估与可观测性

- **单元测试**：切分、权限过滤、记忆提取、工具 schema、状态路由。
- **集成测试**：上传到检索、图片到 Solver、工具失败和 Reviewer 重试。
- **离线评估**：准备带标准答案和来源页码的数据集，跟踪 retrieval recall@k、citation precision、答案正确率、拒答率、平均延迟和 token 成本。
- **在线观测**：使用 LangSmith 或 OpenTelemetry 记录 trace；脱敏保存 prompt、节点耗时、工具错误和用户反馈。
- **安全测试**：提示词注入、恶意文件、越权检索、代码沙箱逃逸、超大图片和敏感信息泄露。

建议为 MVP 设定可验证目标，例如：引用准确率 ≥ 90%、核心回归题正确率 ≥ 80%、P95 延迟 ≤ 8 秒（不含外部搜索）、单次请求工具调用 ≤ 3 次。

## 8. 部署与成本控制

开发环境使用 Docker Compose；生产环境将 API、worker、数据库、向量库和对象存储分离。对相同查询缓存 embedding 和搜索结果，长对话自动摘要，简单问题使用更小模型，设置 token、文件大小和并发上限。模型和向量库通过接口抽象，便于切换供应商和进行成本对比。

## 9. 简历中的项目表达

### 9.1 项目名称

**Study Helper Agent｜基于 LangChain/LangGraph 的多模态个性化学习助手**

### 9.2 简历描述模板

> 独立设计并实现基于 LangChain/LangGraph 的学习 Agent：构建带页码引用的本地资料 RAG，使用 Planner-Retriever-Solver-Tutor-Reviewer 多智能体工作流，接入 Web Search/Calculator/代码沙箱 tools calling；基于 thread checkpointer 实现短期记忆，并将用户掌握度、错题和复习计划持久化为长期记忆；接入 Vision 模型解析题目图片，支持 SSE 流式输出、权限过滤、失败重试与 LangSmith tracing。

### 9.3 用数据增强可信度

不要只写“支持 RAG、多 Agent、多模态”。上线评估后替换为真实数据，例如：支持多少种文件、多少条回归问题、Recall@5、引用准确率、P95 延迟、平均 token 成本、工具成功率和测试覆盖率。面试时重点讲一次完整请求的状态流转、为何选择混合检索、如何防止长期记忆污染，以及 Reviewer/评估集如何发现错误。

## 10. 交付检查清单

- [ ] README 能在新环境完成启动和一次端到端问答
- [ ] `.env`、用户资料、数据库凭据均未提交到 Git
- [ ] 每个回答可追溯到引用或明确标记为模型常识/外部搜索
- [ ] 文件和记忆具备用户级隔离、删除和错误恢复
- [ ] 工具调用有 schema、超时、权限和审计日志
- [ ] 有固定评估集、关键指标和失败样例
- [ ] 有架构图、API 示例、演示 GIF/截图和简历项目描述

