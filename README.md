# Study Helper Agent

A local-first study assistant built on LangChain + LangGraph and Qwen (can be replaced).
Point it at your own lecture notes and textbooks, ask questions in natural language, and get
answers that cite the exact page, heading, or line they came from.

Your documents never leave the machine: parsing, chunking, and the vector store all run locally.

## Features

- **Grounded Q&A over your own files** — PDF, Markdown, and TXT. Every claim carries a citation
  such as `lecture.pdf p.18` or `notes.md > Chapter 3 > 3.2 Eigenvalues`.
- **Multi-agent graph** — six single-responsibility nodes (Planner / Retriever / Solver / Tutor /
  Reviewer / Clarify) wired as a LangGraph `StateGraph` with conditional routing, review-and-rewrite
  retries, and a fallback that asks the user a clarifying question instead of guessing.
- **Tool calling with guardrails** — safe AST-based calculator, Tavily web search, and flashcard
  generation, all wrapped with per-call timeout, bounded retry, a per-turn call budget, and JSONL
  traces.
- **Incremental ingestion** — a content-hash manifest makes re-ingesting idempotent: only new and
  changed files are re-chunked, and chunks of deleted files are cleaned up.
- **Session memory** — conversation history is persisted per session and survives restarts.
- **Regression eval harness** — 38-question set measuring Recall@k, citation precision, refusal
  accuracy, and latency.

## How it works

```mermaid
graph LR
    START --> planner
    planner -- unclear --> mark_ambiguous --> clarify --> END
    planner -- chat --> tutor
    planner -- other --> retriever
    retriever -- no evidence, needs tools --> solver
    retriever -- no evidence --> mark_no_evidence --> clarify
    retriever -- has evidence, needs tools --> solver
    retriever -- has evidence --> tutor
    solver --> tutor
    tutor -- has evidence or tool results --> reviewer
    tutor -- nothing to verify --> END
    reviewer -- passed --> END
    reviewer -- failed --> tutor
    reviewer -- retries exhausted --> mark_unverified --> clarify
```

| Node | Responsibility |
|---|---|
| `planner` | Classify intent (`qa` / `chat` / `calc` / `search` / `flashcard` / `unclear`) and decide whether tools are needed |
| `retriever` | Vector search over the local knowledge base, returns cited evidence |
| `solver` | Step-by-step reasoning and tool use (the only node that touches tools) |
| `tutor` | Rewrites evidence into a teaching-style answer — the sole producer of the final answer |
| `reviewer` | Checks citation alignment, arithmetic, and hallucination; sends the answer back when it finds real problems |
| `clarify` | Asks the user a question when the request is vague, evidence is missing, or the answer cannot be verified |

An answer is only shipped after it passes review; if review fails twice, the graph degrades gracefully
into a clarifying question rather than returning an unverified answer.

## RAG Evaluation

Reports Recall@k, citation precision, refusal accuracy, and average latency, then lists failing
cases. Non-zero exit code means at least one question failed.

Current baseline (`--no-llm`, 38 questions, `top_k=4`):

| Metric | Value |
|---|---|
| Recall@4 | 74.3% (29/38) |
| Citation precision | 18.6% |
| Avg. latency | 0.80 s |

Citation precision is well below the 90% target — reranking and hybrid retrieval are the next
focus areas.

## Project layout

```
learning_agent/
├─ main.py             # CLI entry point (display only; all logic lives in the packages)
├─ config.py           # .env-backed configuration
├─ llm.py              # chat model factory
├─ memory.py           # per-session conversation memory (JSON on disk)
├─ graph/              # LangGraph orchestration
│  ├─ state.py         #   TaskState and the AgentResult contract
│  ├─ nodes.py         #   the six agent nodes and their prompts
│  └─ builder.py       #   routing functions and graph assembly
├─ rag/                # retrieval pipeline
│  ├─ loader.py        #   PDF / Markdown / TXT loading with metadata
│  ├─ splitter.py      #   format-aware chunking that preserves heading paths and line numbers
│  ├─ embedder.py      #   DashScope embedding wrapper
│  ├─ store.py         #   local Chroma vector store
│  ├─ manifest.py      #   content-hash manifest enabling incremental ingest
│  ├─ retriever.py     #   search plus citation formatting
│  └─ pipeline.py      #   index / sync / ask entry points
├─ tools/              # tool registry and implementations
│  ├─ registry.py      #   budget, timeout, retry, tracing guardrails
│  ├─ calculator.py    #   AST-based safe math evaluation
│  ├─ web_search.py    #   Tavily search
│  └─ flashcard.py     #   flashcard generation
├─ evaluation/         # regression harness and 38-question dataset
└─ data/               # local documents, vector store, memory, traces (git-ignored)
```

## Status

**Implemented:** RAG pipeline with incremental indexing (Phase 1), guarded tool calling (Phase 2),
and LangGraph multi-agent orchestration (Phase 3).

**Not yet implemented:** long-term learner profiles, multimodal input (formula and problem
screenshots), hybrid retrieval with reranking, and a web API layer.

## Requirements

- Python 3.11+
- An Alibaba Cloud DashScope (Model Studio) API key — used for both chat and embeddings. (Plan to support more providers and models in the future stage)
- Optional: a [Tavily](https://tavily.com) API key to enable web search

## Install

```bash
pip install -r requirements.txt
```

## Configure

All configuration lives in `.env` (git-ignored; see `config.py` for defaults).

| Variable | Purpose |
|---|---|
| `DASHSCOPE_API_KEY` | DashScope API key (required) |
| `DASHSCOPE_BASE_URL` | OpenAI-compatible chat endpoint (required) |
| `LLM_MODEL` | Chat model name, default `qwen-plus` |
| `LLM_TEMPERATURE` | Sampling temperature, default `0.7` |
| `EMBEDDING_API_KEY` | Separate key for embeddings (falls back to `DASHSCOPE_API_KEY`) |
| `EMBEDDING_BASE_URL` | Embedding endpoint |
| `EMBEDDING_MODEL` | Default `text-embedding-v4` |
| `EMBEDDING_DIMENSION` | Vector dimension, default `1024` |
| `DATA_DIR` | Document folder to ingest, default `./data/uploads` |
| `CHROMA_PERSIST_DIR` | Vector store location, default `./data/chroma` |
| `COLLECTION_NAME` | Chroma collection name |
| `TAVILY_API_KEY` | Enables the `web_search` tool when set |
| `MAX_TOOL_CALLS` / `TOOL_TIMEOUT` / `TOOL_MAX_RETRIES` | Tool guardrails |
| `REVIEW_MAX_ROUNDS` | Max review rounds before falling back to a clarifying question |
| `LANGSMITH_TRACING` / `LANGSMITH_PROJECT` | Optional LangSmith tracing |

## Run

```bash
python main.py                     # start chatting (documents go in ./data/uploads)
python main.py --session math      # separate memory per session
python main.py --ingest            # incremental ingest, then exit
python main.py --ingest --all      # ignore the manifest, re-chunk everything
python main.py --ingest --path notes.pdf
```

In-chat commands:

| Command | Effect |
|---|---|
| `/ingest [path]` | Incremental ingest (only new and changed files) |
| `/ingest --all` | Full re-index |
| `/tools` | List available tools and guardrail limits |
| `/graph` | Print the graph's Mermaid source |
| `/stats` | Vector store status |
| `/new` | Clear the current session memory |
| `/exit` | Quit |

## Documentation

- `PROJECT_PLAN.md` — design, contracts, and roadmap (Chinese)
- `PROGRESS.md` — what is done, what is verified, known issues (Chinese)
- `README.zh-CN.md` — Chinese version of this file
