# Study Helper Agent

基于 LangChain + 阿里云百炼的学习助手。CLI 与本地 RAG/记忆默认落盘；Web 的聊天、Vision 和模型调用是否发送到供应商由部署配置决定，原始图片不会自动写入 RAG。

**当前进度：Phase 5 P0 服务闭环** —— 在 Phase 4 checkpointer、长期记忆和 RAG 基础上，已加入 FastAPI、SSE 对话、显式文件入库任务、用户隔离、Vision 确认、独立 HTML/CSS/JS 三页客户端、删除/导出、限流、审计、指标和 Docker 开发编排。生产多实例仍需接入 PostgreSQL、Redis、共享队列和正式 OIDC。

## 环境要求

- Python 3.11+
- 一个阿里云百炼的 API Key

## 安装

```bash
pip install -r requirements.txt
```

## 配置

配置全部在 `.env`，已加入 `.gitignore`，不会进版本库。

| 变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | 百炼 API Key |
| `DASHSCOPE_BASE_URL` | 对话模型的 OpenAI 兼容端点 |
| `LLM_MODEL` | 对话模型名 |
| `EMBEDDING_API_KEY` | Embedding 的 Key（可与对话不同） |
| `EMBEDDING_BASE_URL` | Embedding 端点 |
| `EMBEDDING_MODEL` | 默认 `text-embedding-v4` |
| `EMBEDDING_DIMENSION` | 向量维度，默认 1024 |
| `CHROMA_PERSIST_DIR` | 向量库落盘位置 |
| `COLLECTION_NAME` | 集合名 |
| `DATA_DIR` | 资料目录 |
| `INDEX_MANIFEST` | 入库清单路径，默认 `./data/index_manifest.json` |
| `LANGSMITH_*` | 追踪上报配置 |

对话和 Embedding 可以指向不同端点。专属部署端点通常只挂你购买的模型，但**实测你的端点同时也提供 `text-embedding-v4`**，所以当前两者共用同一个端点和 Key 是可行的。

### Phase 2 新增

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TAVILY_API_KEY` | 空 | **需要你去申请**，见下 |
| `TAVILY_MAX_RESULTS` | `5` | 每次搜索返回条数 |
| `TAVILY_SEARCH_DEPTH` | `basic` | `basic` 便宜，`advanced` 更准但更贵 |
| `TOOL_TIMEOUT` | `15` | 单个工具超时秒数 |
| `TOOL_MAX_RETRIES` | `2` | 失败重试次数（最多执行 3 次） |
| `MAX_TOOL_CALLS` | `6` | 单轮对话的工具调用总次数上限 |
| `FLASHCARD_DIR` | `./data/flashcards` | 复习卡片存放目录 |
| `TRACE_DIR` | `./data/traces` | 工具调用轨迹目录 |

### Phase 3 新增

| 变量 | 默认值 | 说明 |
|---|---|---|
| `REVIEW_MAX_ROUNDS` | `2` | Reviewer 的审核总轮数（含首次）。第 N 次仍不通过就降级到澄清节点反问用户 |

**只有 `TAVILY_API_KEY` 需要你操作。** 到 https://app.tavily.com 注册，免费额度每月 1000 次调用，拿到 key 填进 `.env` 即可。

不填也能正常跑：`TAVILY_API_KEY` 为空时，`web_search` 这个工具**根本不会被装配**，模型看不到它，也就不会去调用一个注定失败的工具。启动横幅里会显示「联网搜索：未配置」。

其余变量都有可用默认值，不改也行。

## 快速开始

```bash
# 1. 把资料放进 data/uploads（支持 pdf / md / txt）
# 2. 入库（增量，只处理新增和变动的文件，重复跑不会重复烧 embedding）
python main.py --ingest

# 3. 开始对话
python main.py
```

### Phase 5 Web/API 快速开始

```bash
# 复制配置并填写 DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL
copy .env.example .env       # PowerShell；Linux/macOS 使用 cp
python api_server.py         # API: http://127.0.0.1:8000
# 浏览器打开 http://127.0.0.1:8000       # 独立 HTML/CSS/JS 前端（默认 dev-token）
```

API 除 `/health/live`、`/health/ready` 外都要求 `Authorization: Bearer <token>`。开发模式只接受 `dev-token`，生产环境必须使用 `AUTH_MODE=jwt`（OIDC JWKS）或受控 token 映射，服务端不会信任请求体中的 `user_id`。

文件上传和入库是两个动作：

```bash
curl -H "Authorization: Bearer dev-token" -F "file=@notes.pdf" http://127.0.0.1:8000/v1/files
curl -X POST -H "Authorization: Bearer dev-token" -H "Idempotency-Key: notes-index-1" \
  http://127.0.0.1:8000/v1/files/<file_id>/index-jobs
curl -H "Authorization: Bearer dev-token" http://127.0.0.1:8000/v1/files
```

第一条只保存文件，embedding 调用次数和向量数量不会增加；第二条才创建异步入库任务。任务失败会给出稳定错误码，可用新的幂等键重试。对话接口是 `multipart/form-data` 的 SSE：`payload` 为 `{"thread_id": ..., "message": ...}`，图片放在 `images` 字段；低置信度识别会发出 `confirmation.required`，通过 `/v1/turns/{turn_id}/vision-confirmation` 确认后才进入 Solver。

Docker 演示（需要先准备 `.env` 中的模型密钥）：

```bash
docker compose up --build
```

Compose 启动 `api`、`worker`、PostgreSQL 和 Redis，浏览器前端由 API 在 8000 端口提供。当前本地实现的 SQLite + Chroma 只支持单 API/单 worker 开发模式；生产多实例的数据库、checkpoint、队列、限流和向量后端替换边界见 [PHASE5_PRODUCT_PRD.md](PHASE5_PRODUCT_PRD.md)，代码与验收说明见 [PHASE5_IMPLEMENTATION.md](PHASE5_IMPLEMENTATION.md)。

对话中的命令：

| 命令 | 作用 |
|---|---|
| `/ingest [路径]` | **增量**入库：只处理新增和变动的文件，默认扫 `data/uploads` |
| `/ingest --all` | 忽略清单，全部重新分片 |
| `/tools` | 查看当前装配了哪些工具及约束参数 |
| `/graph` | 打印主图的 Mermaid 源码 |
| `/new` | 清空当前会话记忆 |
| `/resume` | 从 checkpoint 恢复中断轮次 |
| `/threads` | 列出当前用户的会话 |
| `/session 名称` | 创建或切换会话 |
| `/stats` | 查看向量库文件数与分片数 |
| `/exit` | 退出 |

指定会话（使用 SQLite `SqliteSaver`，默认存到 `data/memory/checkpoints.sqlite`）：

```bash
python main.py --session 线性代数
python main.py --user alice --session 线性代数
```

相同用户与会话名对应稳定 UUID，重启后继续原会话。`--session` 不再作为 RAG 用户过滤条件；入库和提问都使用 `--user`，默认 `default`。用户参数仅用于本地命名空间，不是鉴权。

默认用户旧 JSON 会话在首次访问时校验并导入一次；损坏或不完整问答会明确报错。成功迁移后旧文件移动到 `data/memory/migration_backup/`，实时入口不再读取。`/new` 删除该线程所有 checkpoints 和 pending writes，并禁止旧历史再导入。

每轮清空临时证据、工具结果和审核状态，只把最终答案写入历史；默认向模型提供最近 20 条历史及本轮输入，按完整问答边界取舍。可通过 `CHECKPOINT_DB` 和 `MEMORY_HISTORY_MAX_MESSAGES` 配置。

中断轮次会阻止新提问，可用 `/resume` 继续或 `/new` 放弃。恢复失败工具节点可能重复外部副作用（如追加卡片），工具重放去重仍待后续实现。当前只支持单进程 CLI。长期记忆、知识点状态和 interval_v1 复习计划现保存在独立的 `LEARNING_DB`（默认 `data/memory/learning.sqlite`）；显式目标/偏好/学习困难会提取为有来源事实，不确定候选通过 `/memory confirm` 或 `/memory reject` 处理。`/memory`、`/knowledge`、`/review today`、`/review feedback`、`/data export` 和 `/data delete` 提供管理入口。

离线测试：`python -m pytest -q`（当前 37 项）。程序调用需先通过 `memory.thread_store().get_thread(user_id, name)` 获取线程，再传给 `graph.run(..., thread_id=...)`；省略 thread_id 则为不持久化的兼容模式，支持旧 history 参数。

## 目录结构

```
learning_agent/
├─ .env                    配置（不进版本库）
├─ config.py               读 .env，全局唯一配置来源
├─ llm.py                  对话模型工厂
├─ memory/                 记忆包（与 rag/ 的模块划分方式一致）
│  ├─ __init__.py          对外接口统一导出
│  ├─ short_term/          短期会话记忆
│  │  ├─ checkpoint.py      SQLite saver 初始化、thread 配置
│  │  ├─ store.py           会话注册、用户归属、查询与清理
│  │  ├─ legacy.py          旧 JSON 兼容器、校验与一次性迁移
│  │  └─ pipeline.py        两类存储的生命周期编排
│  ├─ long_term/           长期学习记忆
│  │  ├─ database.py        learning.sqlite schema
│  │  ├─ repository.py      事实、知识点、事件与复习任务
│  │  ├─ extraction.py      有来源的长期记忆候选提取
│  │  ├─ knowledge.py       知识点服务门面
│  │  ├─ scheduler.py       interval_v1 复习调度门面
│  │  ├─ schemas.py         长期记忆数据契约
│  │  ├─ service.py         每轮提交与自动提取门面
│  │  └─ privacy.py         跨短期/长期数据的导出与删除
├─ main.py                 主程序（CLI）
├─ graph/                  Agent 层：LangGraph 主图（Phase 3）
│  ├─ state.py             TaskState 与对外的 AgentResult
│  ├─ nodes.py             六个节点与各自的提示词
│  └─ builder.py           路由函数、装图、run / run_stream
├─ rag/                    RAG 包
│  ├─ loader.py            加载 PDF / Markdown / TXT
│  ├─ splitter.py          切分，保留标题路径与行号
│  ├─ embedder.py          阿里云 embedding 封装
│  ├─ store.py             本地 Chroma 向量库
│  ├─ manifest.py          入库清单，内容哈希做幂等键
│  ├─ retriever.py         检索与引用格式化
│  └─ pipeline.py          编排入口 index / ask
├─ app/                    Phase 5 服务包
│  ├─ api.py               FastAPI 路由、SSE 和统一错误
│  ├─ repository.py        文件/任务/turn/审计元数据
│  ├─ jobs.py              显式入库、删除和图片 TTL worker
│  ├─ auth.py              Bearer/JWT、限流和请求身份
│  ├─ vision.py            图片签名、EXIF 清理和结构化 Vision
│  └─ observability.py     JSON 日志、Prometheus、OTel
├─ frontend/               独立 HTML/CSS/JS 前端
│  ├─ index.html           页面结构
│  ├─ css/styles.css       视觉样式与响应式布局
│  └─ js/app.js            API 调用、会话和档案交互
├─ api_server.py           Uvicorn API 入口
├─ worker.py               开发 worker 入口
├─ tools/                  工具包
│  ├─ registry.py          执行护栏：schema、超时、重试、次数上限、trace
│  ├─ calculator.py        安全数学计算（AST 求值，不用 eval）
│  ├─ web_search.py        Tavily 联网搜索
│  └─ flashcard.py         复习卡片生成与保存
├─ evaluation/             回归集与跑分脚本
└─ data/
   ├─ uploads/             资料放这里
   ├─ chroma/              向量库
   ├─ memory/              会话记忆
   ├─ flashcards/          复习卡片
   └─ traces/              工具调用轨迹（JSONL）
```

### Phase 5 已知限制

- 本地服务默认使用 SQLite + Chroma 和进程内 sliding-window 限流，必须单 API、单 worker；生产部署需实现 PRD 中的 PostgreSQL、Redis/队列、共享 checkpoint 和向量后端适配。
- Vision 适配器复用配置的多模态 chat 模型；如果供应商没有图片模型或凭证不可用，接口会返回 `provider_unavailable`，不会把原图送入 RAG，也不会绕过低置信度确认。
- P0 通过完整答案 SSE，不承诺逐 token 续传；断线后用相同幂等键重读 turn 终态。
- Compose 含 PostgreSQL/Redis 作为生产兼容依赖，但当前开发 repository 仍写 SQLite；正式迁移和对象存储/病毒扫描属于后续部署工作。
- 「删除全部」只覆盖应用管理的文件、向量、checkpoint、记忆和本地产物；已发送给模型供应商或 LangSmith 的外部日志受其保留策略控制，服务端不会伪称可以撤回。

## 引用标注规则

三种格式各有各的标法，因为只有 PDF 才有真正的页码：

| 格式 | 定位方式 | 展示效果 |
|---|---|---|
| PDF | 页码 | `讲义.pdf 第42页` |
| Markdown | 标题路径 | `笔记.md > 第3章 > 3.2 特征值` |
| TXT | 行号区间 | `notes.txt 第120-168行` |

Markdown 的标题路径由 `splitter.py` 用 `MarkdownHeaderTextSplitter` 解析标题层级得到；TXT 的行号是在切分后反查原文位置算出来的。

## 入库（`/ingest`）

**索引是离线动作，不是对话能力。** agent 在对话里只能查已入库的分片，它读不到 `data/uploads/` 里的文件本身。所以新资料放进目录后，必须执行一次入库：

```bash
python main.py --ingest            # 或在对话里敲 /ingest
```

默认是**增量**的，靠 `rag/manifest.py` 的入库清单判断每个文件该不该重做：

| 情况 | 判定依据 | 行为 |
|---|---|---|
| 新增 | 清单里没有这个路径 | 分片入库，记进清单 |
| 更新 | 清单里有，但 sha256 变了 | 先删旧分片再入新的（文件改短不会留孤儿） |
| 跳过 | 清单里有，sha256 一致 | 直接跳过，**不调 embedding** |
| 清理 | 清单里有，磁盘上没有了 | 删掉它的分片，否则会变成孤儿被检索到 |
| 无文本 | 解析出 0 个分片 | 单独列出——典型是扫描版 PDF（只有图片层没有文字层） |

清单落在 `data/index_manifest.json`。它是**缓存不是真相**，真相永远是 `data/uploads/` 里的文件：清单丢了或写坏了，重跑一次全量入库即可，不影响正确性。

实测增量入库的效果：

```
$ python main.py --ingest          # 第二次跑，什么都没变
  [跳过] 7 个文件内容未变：AGENTS.md、ELEC6036-…、literature.txt、…
没有需要入库的新内容                # 零 embedding 调用
```

`/ingest --all` 忽略清单全部重建，会重新调一遍 embedding——只在怀疑索引坏了、或改过切分参数时才需要。

传单个文件也支持（`/ingest data/uploads/新讲义.pdf`）。这种情况下**不做**删除清理：否则会把「只扫到这一个文件」误判成「其他文件都被删了」，连带清掉它们的分片。

## 工具（Phase 2）

三个工具，描述写在各自的模块里，模型靠这些描述判断什么时候该用：

| 工具 | 用途 | 参数 |
|---|---|---|
| `web_search` | 查资料里没有的、有时效性的内容 | `query` |
| `calculate` | 所有数字计算，不让模型心算 | `expression` |
| `make_flashcards` | 把内容整理成问答卡片存到本地 | `topic` / `content` / `count` |

`calculate` 用 AST 遍历求值，不走 `eval`，只放行白名单里的运算符和函数（`_BIN_OPS` / `_UNARY_OPS` / `_FUNCS`），幂运算指数上限 1000，防止 `9**9**9` 把内存打满。

### 四重护栏

全部实现在 `tools/registry.py`，各工具自己不重复实现：

| 护栏 | 行为 |
|---|---|
| schema | 由函数签名推断，模型只能按声明的参数调用 |
| 超时 | `TOOL_TIMEOUT`，线程池包一层（Windows 没有 `signal.alarm`） |
| 重试 | `TOOL_MAX_RETRIES`，最多执行 3 次 |
| 次数上限 | `MAX_TOOL_CALLS`，超了拒绝并把「换个思路」的提示交回模型 |

两个设计上的选择：

**工具失败不抛异常，而是把错误信息当返回值交给模型。** 这样模型有机会换一种问法，而不是整个对话崩掉；实在不行它会说明工具失败了，不会编一个结果出来。

**认证类错误不重试。** `web_search` 遇到 401/403/432 抛的是 `ToolError(retryable=False)`——key 无效重试多少次都一样，实测让它在无谓的重试上从 4.95s 降到 1.72s。429 和 5xx 则相反，是暂时性的，值得重试。

超时有个已知局限：超时后线程不会被强杀，只是调用方不再等待。对联网搜索影响不大，但将来如果有工具会改文件，要知道它可能仍在后台跑完。

### Tool Trace

每次调用都记录，落到 `data/traces/<会话名>-<时间戳>.jsonl`，一行一条：

```json
{"name": "calculate", "args": {"expression": "47*3*12"}, "status": "ok",
 "duration": 0.002, "attempts": 1, "result_chars": 10,
 "preview": "47*3*12 = 1692", "error": "", "at": "2026-09-12T14:31:07"}
```

`status` 有四种：`ok` / `error` / `timeout` / `rejected`。`attempts` 是实际执行次数，大于 1 说明重试过。终端里显示成这样：

```
工具调用：
  ✓ calculate(expression=47*3*12) 0.00s
  ✗ web_search(query=test) 1.72s → ToolError: Tavily 认证失败或额度用尽（HTTP 401）
```

## 多 Agent 图（Phase 3）

`planner.py` 里那个单 Agent 大循环换成了 LangGraph 主图。对应 `PROJECT_PLAN.md` 3.4 的职责表：

| 节点 | 职责 | 输入 → 输出 | 调模型？ |
|---|---|---|---|
| `planner` | 识别意图、拆解步骤 | 问题、记忆 → `plan` | 是（结构化输出） |
| `retriever` | 查本地知识库 | 问题、过滤条件 → `evidence` | **否**，就是 `rag.retrieve` |
| `solver` | 分步推理、调工具 | 证据、题目 → `draft_answer`、`tool_results` | 是（绑工具） |
| `tutor` | 教学化改写、控难度 | 证据、草稿 → 最终答案 | 是 |
| `reviewer` | 核对引用、计算、幻觉 | 草稿、证据 → `review` | 是（结构化输出） |
| `clarify` | 答不了时反问用户 | 原因 → 反问 | 是 |

```
START → planner ─┬─ unclear ───────────────────────────→ clarify → END
                 ├─ chat（短路）───────→ tutor ─┐
                 └─ 其他 → retriever ─┬─ 无证据+需工具 → solver ─┐
                                      ├─ 无证据 ──────→ clarify    │
                                      ├─ 有证据+需工具 → solver ───┤
                                      └─ 有证据 ──────→ tutor ─────┤
                                                                  ↓
                                            tutor → reviewer ─┬─ 通过 → END
                                                             ├─ 打回 → tutor
                                                             └─ 用尽 → clarify → END
```

路由函数在 `graph/builder.py` 里，都是纯函数（只读 state、不碰 IO），可以直接单测：

| 路由 | 判断 |
|---|---|
| `route_by_intent` | `unclear` → 澄清；`chat` → 短路到 tutor；其余 → 检索 |
| `route_by_evidence` | 无证据且要工具 → solver（用 web_search 兜底）；无证据 → 澄清；有证据按是否要工具分流 |
| `route_after_tutor` | 有证据或调过工具才进 reviewer，纯寒暄跳过 |
| `route_after_review` | 通过 → 结束；打回且还有轮数 → tutor 重写；用尽 → 澄清 |

四个设计上的选择：

**闲聊短路。** Planner 判定为 `chat`（打招呼、道谢、问助手能力）时直接到 tutor，跳过 Retriever / Solver / Reviewer——没有资料可检、没有引用可核。实测「你好，你能做什么」只走 2 个节点。

**澄清节点一个顶三个。** 三种情况共用它，靠 `clarify_reason` 分派：问题太笼统（`ambiguous`）、资料里没找到（`no_evidence`）、审核连续不通过（`unverified`）。图里用三个极小的标记节点把原因写进状态，比让 `clarify_node` 去猜「我是被谁叫来的」清楚。

**审核打回不是死循环。** `REVIEW_MAX_ROUNDS` 限死总轮数，用尽后按计划书 2.3 第 6 步降级为反问用户，而不是把没核实的答案硬塞出去。

**结构化输出只能用 `json_schema`。** 本项目的端点是 `qwen3.8-flash`，thinking 模式下 `tool_choice` 被禁，所以 `method="function_calling"` 会直接 400；`json_mode` 又要求消息里出现 "json" 字样。三个里只有 `json_schema` 能用（2026-09 实测，见 `graph/nodes.py` 的 `_structured`）。

流式事件走 LangGraph 的 `stream_mode=["custom", "values"]`：每个节点发一条人类可读的 custom 事件，`run_stream` 逐个产出，最后一条 `{"type": "done", "result": AgentResult}` 带上完整结果。对话里显示成：

```
你 > Hong Kong Live 项目里前端用什么技术栈？

  [planner] 意图=qa，需工具=False
  [retriever] 召回 4 个分片
  [tutor] 草稿完成
  [reviewer] 第 1 轮通过

助手 > **结论：……**
```

`/graph` 命令可以随时打印这张图的 Mermaid 源码，`run()` 是非流式的薄封装（签名与改造前的 `planner.run` 一致）。

## 阿里云 Embedding 使用规则

`rag/embedder.py` 把这些约束封在一处，改动前请先读这一节。以下数值是**实测**结果，不是照抄文档：

| 约束 | 实测行为 |
|---|---|
| 单次批量 | **最多 10 条**，第 11 条直接 400 `batch size is invalid` |
| 返回顺序 | 与输入顺序一致，无需重排（代码里仍按 `index` 排一次，防接口行为变化） |
| 合法维度 | `64 / 128 / 256 / 512 / 768 / 1024 / 1536 / 2048 / 3072` |
| `text_type` | OpenAI 兼容接口**接受**，用于 query/document 非对称检索 |

注意最后两行和官方文档的出入：

- 官方文档写维度最大 2048，**实际 API 还接受 3072**。代码按 API 实测值校验。
- `text_type` 在原生协议文档里定义，兼容接口文档没列。实测传了不报错，因此默认开启（文档用 `document`、查询用 `query`）。若某天接口不再接受，把 `use_text_type` 设为 `False` 即可退回对称模式。

## 回归集

`evaluation/dataset.json` 存题目，`evaluation/run.py` 跑分：

```bash
python evaluation/run.py
```

每题的结构：

```json
{
  "id": "q001",
  "question": "什么是特征值？",
  "expected_sources": [
    {"file_name": "线性代数讲义.pdf", "page": 42}
  ],
  "answer_keywords": ["Ax = λx", "非零向量"],
  "should_refuse": false
}
```

`should_refuse: true` 表示资料里本就没有答案，用来测模型会不会硬编。比如「这份资料有没有讲傅里叶变换」——如果讲义里确实没有，正确答案是拒答而不是瞎编。

跑分输出四项指标：

- **Recall@k**：期望来源被检索命中的比例
- **引用准确率**：回答里引用的位置是否落在期望来源内
- **拒答正确率**：该拒答的题是否正确拒答
- **平均延迟**

跑完会列出失败样例，用来定位是检索问题还是生成问题。

### 当前基线

针对 `data/uploads` 里的 5 份资料（ELEC7023C Session 2 课件 + 机器人控制文献综述），38 题：

```
Recall@4      74.3%      （29/38 通过）
引用准确率     18.6%
平均延迟      0.80s
```

跑了 `--no-llm`，所以拒答率和关键词命中两项无意义。

### 失败原因分析

失败集中在两类，**都不是随机的**：

**1. 跨语言检索**。题目是中文，资料是英文，同一问题换英文问就能召回：

| 问题 | 中文 | 英文 |
|---|---|---|
| 项目里有哪些文件 | 未召回 `Project structure` | 召回 |
| 能不能用 var 声明变量 | 完全没沾边 | `Rules` 排第 1 |

**2. 多主题分片稀释**。`AGENTS.md` 的 `Rules` 一节把缩进、var、框架规范写在同一个分片里，embedding 被平均成"什么都沾一点"，中英文都问不出它。这类问题靠重排也难解决，需要更细的切分或按条目切。

引用准确率只有 18.6% 是另一个信号：top_k=4 里通常只有 1 条是真正相关的，说明召回精度低。计划书 3.2 节设计的**混合检索（BM25 + 向量）重排**正是针对这两类问题的下一步。

## 路线图

对照 `PROJECT_PLAN.md`：

- [x] **Phase 0** 工程基线 —— 配置、日志、依赖已就绪；CI 未做
- [~] **Phase 1** RAG MVP —— 主链路已通，回归集已建并跑出基线；跨语言召回与分片粒度待优化
- [x] **Phase 2** 单 Agent + Tools —— 三个工具就绪，四重护栏已实测生效；`web_search` 的真实搜索结果待填上 key 后验证
- [x] **Phase 3** LangGraph 多 Agent —— 六个节点的主图已跑通；四条路径（闲聊短路 / 资料问答 / 工具调用 / 澄清）实测分叉正确，审核打回重试用桩验证过；多 Agent 后的实际延迟还没测
- [x] **Phase 4** 记忆（短期 checkpointer + 长期学习档案 + 知识点状态 + interval_v1 复习计划 + 删除/导出）
- [x] **Phase 5 P0** 多模态与产品化 —— FastAPI/SSE、显式入库任务、Vision 确认、用户隔离、独立前端和 Docker 开发编排已实现；生产共享后端与正式 OIDC 见 `PHASE5_ISSUES.md`

## 已知限制

- 检索目前是**纯向量召回**，计划书 3.2 节设计的 BM25 混合检索和 reranker 还没做
- **检索没有相关度阈值，"没找到就反问"这条保护网实际上是死代码**：`k=4` 且无阈值，只要库里够 4 条就一定返回 4 条，哪怕分数只有 0.38。所以拿资料里没有的问题去问，agent 会收到 4 条无关分片然后靠自己知识兜底（标「模型常识」），而**不会**走 `mark_no_evidence → clarify` 说「资料里没找到」。修法是给 `retrieve` 加相关度下限（`similarity_search_with_relevance_scores`），阈值需拿回归集调，否则会误伤真正相关的弱匹配
- **CLI 上传文件后仍必须手动执行 `/ingest`**，这是 CLI 兼容行为；Web 端使用 `/v1/files` 上传后再点「入库（RAG）」按钮，列表第一屏会显示未入库状态，重复任务受幂等键保护
- **`--session` 检索过滤问题已修复（2026-09-17）**：用户与会话分离，入库与提问使用相同 `--user`；manifest 按用户与文件路径联合登记，旧 v1 清单自动兼容。
- 多 Agent 让资料问答路径从 1-2 次模型往返涨到 3 次（Planner + Tutor + Reviewer），闲聊短路能省掉其中两次，但真实 P95 延迟还没实测。若超标，最省事的缓解是给 Reviewer 单独绑一个小模型
- `ToolTracer.save()` 是追加写且不清空 `records`，同一 `run_id` 重复保存会写出重复行。目前只有 `solver` 调它一次、`run_id` 带时间戳，不会重复；以后若有节点重跑 solver 要注意
- `make_flashcards` 内部会调一次模型来生成卡片，所以它算一次工具调用但实际是两次模型往返
- `web_search` 只验证过错误路径（无 key 不装配、假 key 报 401）；真实搜索结果的质量要等你填上 key 才能评估
- 当前本地服务使用 SQLite + Chroma 和进程内限流，生产多实例需要接入 PostgreSQL、Redis/共享队列、checkpoint 和向量后端；具体边界见 `PHASE5_ISSUES.md`
- Vision 真实供应商模型、公式/图表红队样例尚未在 CI 调用，需在上线前配置并补充脱敏 fixture
