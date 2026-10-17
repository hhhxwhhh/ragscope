# RAGScope

用一套中文产品文档做知识库问答，同时能看清每次检索到底发生了什么，并用固定评测集判断改配置是不是真的有用。

这是一个面向 Python 后端实习的作品集项目。上传、切块、向量/BM25 混合检索、流式回答是 RAG 的常规链路；真正想讲清楚的是后半部分：**检索可观测性 + 评测工作台**。每次回答都记录改写后的 query、四个检索阶段的候选和耗时；任意一次检索可以固化成评测用例；不同检索配置在同一个数据集上跑，对比 recall@k、MRR、拒答率和延迟。

没有用 LangChain / LlamaIndex，检索和入库都是手写的。产品形态参考了 [RAGFlow](https://github.com/infiniflow/ragflow) 的检索测试台、[Dify](https://github.com/langgenius/dify) 的知识库设计，指标定义参考 [RAGAS](https://github.com/explodinggradients/ragas)，但没有引入 LLM-as-judge。

## 现在能做什么

- 注册 / 登录：access JWT 放内存和 localStorage，refresh token 走 HttpOnly cookie、一次性轮换，前端单飞刷新；登录按 email + IP 做 Redis 失败计数限流
- 知识库 CRUD；.md / .txt / .pdf / .docx 上传，后台解析、结构感知切块、embedding、入库，失败可重试；进程重启会回收 stale 入库任务和评测运行
- 文档解析保留结构：Markdown 标题路径、DOCX 标题与表格、PDF 按页提取并记录页码；chunk、引用、trace 都能追溯到第几页
- 混合检索：BGE 中文向量 + jieba/BM25，RRF 融合，`bge-reranker-base` 可选重排；每个知识库用 `index_version` 让 BM25 与向量缓存失效
- 多查询扩展：可选用 LLM 生成 2–3 个改写问题一起召回，跨越不同表述提高召回；失败或超时会自动回退到单查询
- Redis 检索缓存：按知识库、索引版本、完整检索配置、会话上下文和 query 做指纹，命中时跳过向量化、召回与重排；评测运行强制绕过缓存
- 多轮问答：DeepSeek 流式输出（SSE over POST）、`[n]` 引用、来源抽屉、可中断、回答带检索 trace
- Agentic RAG（可选模式）：单 Agent 在预算内自主调用 `search_knowledge_base` / `read_chunk_neighbors` / `get_document_outline`，把多跳、对比问题拆成多次检索；每一步的工具、参数、观测、耗时都通过 SSE 实时展示并落库，回答仍走原来的编号引用链路
- 引用质量：每次回答统计引用标记数、有效/无效标记、命中来源数与引用率，写入 `messages.citation_stats`
- 反馈闭环：对回答点赞 / 点踩并填写原因；点踩可一键转成评测用例（自动带入被引用 chunk 作为标注），拒答回答转成不可答案例
- 会话管理：按标题搜索、重命名、导出 Markdown
- 运维接口：`/healthz` 存活检查，`/readyz` 同时检查 PostgreSQL / Redis / Chroma；请求带 `X-Request-ID` 和耗时头
- 检索调试台：向量 / BM25 / RRF / 重排四组候选，分阶段耗时瀑布，单次请求覆盖检索参数，显示多查询扩展与缓存命中情况，一键保存为评测用例
- 评测工作台：数据集与 chunk 级标注、配置预设、后台执行与进度、运行取消、逐条结果、两次运行对比
- Dashboard：知识库 / 文档 / chunk / 会话 / 评测 KPI，用户满意度、引用有效率，14 天活动趋势，最近评测结果

## 一次检索的链路

```text
问题
  └─ query 改写（多轮时，DeepSeek）
      └─ Redis 缓存查询（kb + index_version + 配置 + 历史 + query 指纹）
          ├─ 命中 ──> 直接复用候选 ────────────────┐
          └─ 未命中                                  │
              └─ 多查询扩展（可选，LLM 生成 2–3 条）  │
                  └─ 每条 query 分别：                │
                      BGE embedding ──> Chroma 向量 top-k
                      jieba + BM25 ───> 词法 top-k
                          └─ RRF 融合（跨 query 加权，k=60）
                              └─ bge-reranker-base 重排（可选，3s 超时回退）
                                  └─ 回写缓存 ────┤
                                                  └─ 带编号引用的 Prompt ──> DeepSeek（流式）
                                                      └─ 回答 + 引用 + citation_stats + retrieval_trace
```

检索阶段的候选、分数、耗时都会写进 `retrieval_traces`；评测只跑这条链路到重排为止，不调用 LLM。

### Agentic RAG 模式

聊天页右上角可以打开「Agent 检索」。它没有引入任何 Agent 框架，而是把上面的检索 pipeline 包成一个有边界的工具循环：

```text
问题 -> LLM 规划（tools + tool_choice=auto，不流式）
          ├─ search_knowledge_base(query, top_k)   # 复用 retrieve()，关闭多查询扩展
          ├─ read_chunk_neighbors(chunk_id, window) # 同一文档的相邻 chunk
          └─ get_document_outline(document_id)      # 标题结构 / chunk / 页码
      -> 每步落库 + SSE agent_step 事件 -> 证据按 chunk_id 去重
      -> 证据足够则停止 -> 原 grounding Prompt -> 流式回答 + [n] 引用
```

几个刻意的取舍：

- **有界**：`agent_max_steps=5`、单轮决策 30s、单工具 60s（照顾 CPU 冷启动）、工具白名单和 Pydantic 参数校验；LLM 或工具失败时 fail-open，用已收集的证据继续回答，并把 `stopped_reason` 记为 `llm_error` / `max_steps` / `turn_timeout`。
- **规划与作答分离**：循环只决定还要不要检索，不写最终答案；最终答案仍由原来的 grounded Prompt 基于去重后的证据流式生成，所以引用编号、`citation_stats`、检索 trace 与单轮模式完全一致。
- **可观测而不是可幻觉**：落库的是工具名、参数、结构化观测（结果条数、chunk_id、预览、错误）和耗时，不保存模型思维链；前端可以展开每一步。
- **成本可见**：默认关闭，按浏览器记住开关；每次开启会多出若干次 LLM 决策调用，适合对比/多跳问题，简单事实问题单轮 RAG 更便宜。

## 评测结果

数据集是 29 条中文用例（24 条可答、5 条语料外不可答），标注到 chunk 级，语料共 15 个 chunk。同一天、同一索引版本下跑了 5 组配置：

| 配置 | Recall@1 | Recall@3 | Recall@5 | MRR | 拒答准确率 | P95 检索耗时 |
|---|---:|---:|---:|---:|---:|---:|
| 向量基线（无 BM25、无重排） | **85.4%** | **100%** | **100%** | **92.4%** | 100% | 20 ms |
| 混合检索（无重排） | 79.2% | 97.9% | 100% | 88.2% | 100% | 8 ms |
| 混合 + 重排 | 79.2% | 95.8% | 97.9% | 88.3% | 100% | 96 ms |
| 向量 + 重排 | 79.2% | 95.8% | 97.9% | 88.3% | 100% | 96 ms |
| 向量优先 + 重排（2 : 0.5） | 79.2% | 95.8% | 97.9% | 88.3% | 100% | 88 ms |

结论有点反直觉：在这个只有 15 个 chunk 的小语料上，**纯向量检索已经很强，混合检索和重排并没有带来提升**。重排把 `X1 和 X2 的屏幕有什么区别？` 从第 1 位压到了第 5 位，Recall@5 掉了 2 个点，P95 却从 20 ms 涨到 96 ms。混合检索在 Recall@1 上还低了 6 个点，因为短问句里 BM25 的精确词命中会把另一个包含相同关键词的 chunk 顶上来。

这个结果不代表混合检索没用，只说明在 15 个 chunk、大量问题与原文高度同构的 FAQ 语料上，稠密向量几乎没有信息损失。评测台的价值正在这里：**先有数字，再决定要不要为某个组件付出延迟**。如果语料换成几千个 chunk、问题里大量出现型号和错误码，BM25 的价值会体现出来。

拒答指标在调参前只有 20%。原因是 BM25 没有绝对分数阈值，`如何申请美国旅游签证？` 会因为「申请」两个字匹配到售后文档，`杭州明天会下雨吗？` 会匹配到「天」。最后的处理是：向量分数下限从 0.35 提到 0.45，并且**只有存在过阈值的向量候选时才启用 BM25**。可答案例的最高分最低为 0.497，不可答案例的最高分为 0.382，0.45 正好落在中间。拒答准确率变成 100%，24 条可答案例没有一条被误拒。

> 样本只有 29 条，差异小于 5 个点不应该当成结论，README 里保留这些数字主要是为了说明流程，而不是证明某组参数最优。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2.0 async + asyncpg |
| 存储 | PostgreSQL 16、Redis 7、ChromaDB（PersistentClient 嵌入式） |
| 检索 | sentence-transformers（`BAAI/bge-small-zh-v1.5`）、rank_bm25 + jieba、`BAAI/bge-reranker-base` |
| 生成 | DeepSeek（openai SDK 兼容接口），SSE 流式 |
| 前端 | Vue 3 + TypeScript + Vite、Element Plus、Pinia、ECharts、markdown-it + DOMPurify |
| 测试 | pytest + pytest-asyncio、httpx ASGITransport、真实 PostgreSQL/Redis、fake retriever |
| 基础设施 | 多阶段 Dockerfile、Docker Compose、Nginx（SSE 不缓冲）、Alembic、ruff、GitHub Actions |

## 目录

```text
rag_learn/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # auth, kbs, documents, chat, retrieval, eval, dashboard
│   │   ├── core/            # 配置、鉴权、依赖、错误处理
│   │   ├── models/          # SQLAlchemy 模型，含 evaluation
│   │   ├── rag/             # parsers, chunker, embedder, chroma, bm25, fusion,
│   │   │                    # reranker, multi_query, retrieval_cache, pipeline,
│   │   │                    # agent/ (tools + bounded tool-calling loop)
│   │   ├── schemas/         # Pydantic 请求/响应
│   │   ├── services/        # 入库、检索、会话、反馈、评测、看板
│   │   └── workers/         # 文档入库后台任务
│   ├── alembic/versions/
│   ├── scripts/
│   │   ├── seed_demo.py     # 一键创建演示账号、知识库、文档和 29 条用例
│   │   ├── seed_eval.py     # 只重建评测数据集
│   │   └── reindex_vectors.py
│   ├── tests/{unit,integration}/
│   └── Dockerfile
├── frontend/src/
│   ├── api/                 # axios 封装与 SSE
│   ├── components/          # RetrievalDebugPanel, EvalPanel
│   ├── views/               # Dashboard, KbDetail, Chat, EvalRun, EvalCompare
│   └── types/
├── data/                    # 5 篇虚构的星河科技产品文档
├── docs/                    # 架构、评测设计、面试题
├── notebooks/learning/      # 最开始的手写 RAG notebook
├── deploy/nginx.conf        # SPA + /api 代理 + SSE 不缓冲
├── .github/workflows/ci.yml # 后端 ruff/pytest + 前端 typecheck/build
├── docker-compose.yml       # 开发：只起 Postgres + Redis
└── docker-compose.prod.yml  # 生产：nginx + api + db + redis
```

## 本地启动

前置：Python 3.12、Node 20、Docker Desktop。第一次启动会从 Hugging Face 镜像下载 embedding 和 reranker 模型。

```bash
cp .env.example .env
# 至少要填 SECRET_KEY 和 DEEPSEEK_API_KEY；.env 已在 .gitignore 中
docker compose up -d db redis
```

后端：

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash；PowerShell 用 .venv\Scripts\Activate.ps1
pip install -e ".[dev,rag]"          # dev = pytest/ruff；rag = torch + sentence-transformers
alembic upgrade head
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173，注册一个账号，新建知识库，把 `data/` 下的 5 篇文档拖进去。等状态变成「已入库」后就可以问答和调试。

也可以直接用种子脚本准备好全部演示数据：

```bash
cd backend
PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_demo.py
```

脚本是幂等的：创建演示账号 `demo@ragscope.dev` / `Passw0rd!123`、知识库「星河科技产品库」、5 篇文档，并重建「演示评测集 v1」。已入库的文档会跳过，没有入库成功的会重新上传。之后在页面里发起评测运行即可。

### Docker 一键启动

```bash
cp .env.example .env                 # 填 SECRET_KEY 和 DEEPSEEK_API_KEY
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml exec api python scripts/seed_demo.py
```

打开 http://localhost:8080。生产 Compose 包含 nginx、API、PostgreSQL、Redis，API 容器启动时自动跑 Alembic；Chroma、上传文件和 Hugging Face 模型都有独立 volume。API 的 `/api/v1/readyz` 被配置成容器健康检查。

> 镜像需要安装 CPU 版 torch，首次构建下载量较大；本地开发仍建议用上面的 uvicorn + Vite 组合。

## 评测设计

- 用例标注到 chunk 级：`relevant_refs = [{document_id, chunk_index, filename}]`；数据集同时快照标注时的 `chunk_size / chunk_overlap`，切块参数变了就不该继续用旧标注
- 每次运行快照完整 `RetrievalConfig` 和知识库的 `index_version`；**不同索引版本的运行拒绝对比**，因为 chunk 已经变了，排名没有可比性
- 可答案例算 Recall@1/3/5/10、Hit@5、MRR；不可答案例只算拒答准确率；另有可答案例误拒答率
- 出错用例保留在结果里，但不参与质量指标的平均；延迟用 p50 / p95，避免长尾把均值带偏
- 默认 retrieval-only，跑 29 条用例零 API 成本；以后可以加 `include_generation` 做人工抽检，但不做 LLM judge
- 执行器在 FastAPI BackgroundTasks 中跑，每条用例独立提交、更新进度；取消时保留已完成部分

## 测试与 CI

```bash
cd backend
.venv/Scripts/python.exe -m ruff check app scripts tests
.venv/Scripts/python.exe -m pytest -q
```

- 74 个测试，`tests/unit` 覆盖解析器（Markdown / DOCX / 空白 PDF）、页码映射、切块、RRF、指标计算、JWT、多查询解析、检索缓存 key、引用统计，以及 Agent 工具参数校验、证据去重、步数预算、非法参数与模型失败时的 fail-open；`tests/integration` 覆盖注册/登录/刷新轮换/登出、登录限流、知识库越权 404、评测数据集校验、后台执行器、运行对比、stale 运行恢复、反馈增删改与转评测用例、会话搜索/重命名/导出，以及未配置 LLM key 时 Agent 接口的 503。
- 集成测试使用独立数据库 `ragscope_test` 和 Redis DB 1；session 开始时自动建库并跑迁移，每个测试前清空表和 Redis。
- 评测执行器测试 mock 掉 `retrieve`，CI 只装 `.[dev]`，不需要下载 torch。
- GitHub Actions 分两个 job：后端 ruff + pytest（PostgreSQL/Redis service），前端 typecheck + build。配置在 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

更细的设计说明：

- [docs/architecture.md](docs/architecture.md)：请求链路、数据模型、失败恢复、扩展路径
- [docs/evaluation.md](docs/evaluation.md)：指标定义、拒答标定、实验方法、结果解读
- [docs/interview-questions.md](docs/interview-questions.md)：14 个模块的面试题与参考要点

## 几个实现取舍

- **Chroma 嵌入式而不是独立服务**：单机作品集没必要多一个容器，直接 `PersistentClient`，代价是扩展能力有限，文档里写清楚。
- **BackgroundTasks 而不是 Celery**：当前只有一个后台任务类型，加消息队列会引入 Redis 队列、worker 进程和部署复杂度；评测运行需要可取消和进度，用数据库状态轮询就够了。
- **BM25 由向量门槛控制**：BM25 分数没有绝对意义，语料外的短问题也会匹配到单个字。先看有没有向量候选过阈值，没有就直接拒答，不再交给 BM25 制造噪声。
- **SSE 流式期间不持有数据库 session**：先落用户消息，检索和生成阶段用短 session，流结束后再落 assistant 消息；断连时把部分回答标记为 cancelled。
- **越权访问返回 404**：所有 KB 子资源都通过父 KB 做 ownership 校验，不通过就当作不存在。

## 已知局限

- 语料很小，评测数字只能说明流程跑通，不能当成通用结论
- 生成侧只统计引用标记是否有效，没有答案忠实度自动评分，也没有 LLM-as-judge
- 支持 .md / .txt / .pdf / .docx，但扫描件 PDF 需要 OCR；PDF 只保留页码，不保留版式与表格结构
- 多查询扩展是串行调用 LLM，开启后会增加一次生成侧延迟；Redis 缓存不做过期淘汰以外的容量治理
- 后台任务和 API 同进程，只有单副本语义；进程重启会中断任务（启动时标 failed，可重跑）
- 限流用 Redis 计数并在每次失败时重置窗口；没有实现分布式滑动窗口或令牌桶
- 没有团队、RBAC 和审计日志；Chroma 为嵌入式单机模式
- 没有 Playwright E2E，前端只做类型检查和构建验证
- Agent 是单 Agent、单轮预算内的工具循环，没有多 Agent 协作、长期记忆和自动反思；GraphRAG、多模态不在范围内

## 接下来

- 优先扩大「用户反馈回归集」并补人工抽检界面，把线上点踩持续转成回归用例，而不是继续堆检索组件
- 多副本部署时把 BackgroundTasks 迁到 Celery/RQ，Chroma 换成独立向量服务
- 登录限流升级为滑动窗口/令牌桶，并增加审计日志
- 增加 Playwright 冒烟测试和前端组件测试

## License

MIT
