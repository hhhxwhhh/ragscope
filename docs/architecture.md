# 架构说明

这份文档按请求链路说明 RAGScope 的分层、数据一致性策略和关键取舍。目标读者是第一次读这个仓库的人，以及准备面试时想快速回忆"为什么这么写"的作者本人。

## 总览

```text
Browser (Vue 3 + Element Plus)
   │  axios/fetch, Bearer access token, HttpOnly refresh cookie
   ▼
Nginx (生产) ── /api/ ──> FastAPI (uvicorn)
                              ├── PostgreSQL 16  (业务数据 / 评测 / trace)
                              ├── Redis 7        (refresh token、登录限流、检索结果缓存)
                              ├── Chroma         (每 KB 一个 collection)
                              └── 本地进程内任务   (文档入库、评测运行)
                                   ├── BGE embedding        (langchain-huggingface)
                                   ├── Chroma               (langchain-chroma)
                                   ├── BM25 + jieba         (自研 BaseRetriever)
                                   ├── bge-reranker         (自研 CrossEncoder)
                                   └── DeepSeek API         (LangChain ChatOpenAI，流式)
```

RAG 层分两类组件：LangChain 提供 `ChatOpenAI`、`HuggingFaceEmbeddings`、`Chroma` 适配器和 LCEL 链，LangGraph 提供 ReAct 图；BM25、RRF、重排和 trace 由项目自己实现，因为这些地方需要原始的排名、分数和耗时，不能只拿最终答案。Agent 的编排在 `app/rag/agent/loop.py`，工具的权限边界和观测格式在 `agent/tools.py`；`app/rag/router.py` 是自动模式的纯函数路由，不依赖数据库和模型。

后端只有一层进程：API、后台任务、模型推理都在同一个容器里。这是作品集项目的刻意选择，不是没来得及拆。代价和扩展路径写在最后一节。

## 目录分层

| 目录 | 职责 | 不做什么 |
|---|---|---|
| `app/api/v1` | HTTP 参数、状态码、依赖注入 | 不写业务规则 |
| `app/services` | 事务边界、业务规则、ownership 校验 | 不碰请求对象 |
| `app/rag` | 切块、embedding、BM25、融合、重排、Prompt、LCEL 链与 LangGraph 图 | 不直接写数据库 |
| `app/models` | SQLAlchemy 表结构 | 不含查询逻辑 |
| `app/schemas` | Pydantic 输入输出与校验 | 不引用 ORM 对象做副作用 |
| `frontend/src/api` | axios 封装、401 单飞刷新、SSE 解析 | 不含页面状态 |

依赖方向是单向的：`api -> services -> rag/models`。`rag` 不依赖 `api`，因此评测执行器可以在没有 HTTP 请求的情况下复用同一条检索链路。

## 请求链路

### 1. 登录与令牌

```text
POST /auth/login
  ├─ Redis 失败计数：email + IP 各 5 次 / 10 分钟
  ├─ argon2 校验密码
  ├─ access JWT  (30 min, localStorage)
  └─ refresh JWT (7 d, HttpOnly + SameSite=Lax cookie, Redis 存 jti)
```

刷新时先 `GETDEL refresh:{jti}`，同一个 refresh token 只能用一次；成功后签发新的 access + refresh，实现轮换。Redis 不可用时登录直接失败，而不是降级放行。

### 2. 文档入库

```text
POST /kbs/{id}/documents
  ├─ 扩展名 / 大小校验，流式落盘并计算 sha256
  ├─ 同 KB 内 sha256 去重 -> 409
  ├─ documents(status=pending) -> 202
  └─ BackgroundTasks: parse -> chunk -> embed -> Chroma upsert + PG chunks
       ├─ 任一步失败: failed + error_stage/error_message
       ├─ 重试: 删除旧 chunks/vectors 后重跑，保证幂等
       └─ 启动时: pending/parsing/indexing 超过 15 分钟 -> failed
```

`parsers` 统一返回 `ParsedDocument(text, page_starts, page_count)`。Markdown / TXT 没有页码；DOCX 把 Heading 样式转成 Markdown 标题、表格转成行文本；PDF 逐页提取并记录每页正文的字符起点。切块时用 `bisect_right` 把 `char_start` 映射回页码，`chunks.page_number` 由此得到，之后一路带到 Chroma metadata、引用和 trace。

入库过程中 `index_version` 只在完全成功后递增。BM25 缓存、Chroma metadata 和 Redis 检索缓存都以它作为键，所以新索引不会读到旧缓存。

### 3. 问答（SSE）

```text
POST /kbs/{id}/chat/stream
  ├─ 落用户消息（短 session，立即提交）
  ├─ 检索: rewrite -> cache lookup -> （multi-query）-> embed -> vector + BM25
  │        -> 跨 query 加权 RRF -> rerank -> cache store
  ├─ 先发 meta / retrieval 事件，再发 token
  ├─ 流式期间不持有 session
  └─ 结束或断连: 落 assistant 消息 + citations + citation_stats
                  + retrieval_trace（含 sub_queries / cache_hit），
                  断连标记 cancelled 并保留部分内容
```

回答由 LCEL 链生成：`ANSWER_PROMPT | ChatOpenAI`，`astream()` 逐块返回文本和最终的 `usage_metadata`。查询改写和多查询扩展也是同构的短链，各自 `bind(temperature=..., max_completion_tokens=...)`，避免在 Prompt 里放采样参数。SSE 用 POST + `fetch` 而不是 `EventSource`，因为需要请求体和 `Authorization` 头。前端自定义按 `\n\n` 分帧，并用 `TextDecoder({stream: true})` 处理跨 chunk 的中文多字节字符。

检索缓存 key 是 `kb_id + index_version + asdict(RetrievalConfig) + 改写后 query + 历史消息哈希` 的 sha256。历史消息参与指纹，是因为指代消解已经发生在改写阶段：同一个追问在不同上下文中含义不同。缓存 TTL 默认 300 秒，读写失败都按 miss 处理（fail-open），不影响主链路；评测运行强制 `use_cache=False`，保证跑的是真实检索。

多查询扩展让 LLM 生成 2–3 条不同表述的 query，每条分别走向量和 BM25，最后把所有排名一起做加权 RRF。它是一次额外的 LLM 调用，所以默认关闭、有超时（默认 8 秒），失败时回退到单查询，并在 trace 里记录 `multi_query_error`。

### 3a. 问答入口：自动路由

`ChatRequest.mode` 支持 `rag` / `agent` / `auto`。`auto` 在 API 层调用 `rag/router.py` 的纯函数：比较/选择词、两个型号用「和/与」连接、多部分/条件词、多个问号等显式信号会把问题升级到 Agent，其余留在单轮 RAG。规则不调用 LLM，理由是自动判断发生在每次提问前，额外一次模型调用会给最简单的问题也增加延迟和失败点。

偏向性也是设计的一部分：误判成 RAG 只多跑一次检索，误判成 Agent 会多出若干次模型决策和工具调用，所以拿不准时走便宜路径。显式指定模式时规则不生效；`auto` 且 `agent_enabled=false` 时回退到 RAG，并在原因里注明。SSE `meta` 返回实际模式、请求模式和路由原因，前端据此显示「自动 → Agent / 快速 RAG」。

### 3b. Agentic RAG（手动或自动命中）

```text
POST /kbs/{id}/chat/stream  { mode: "agent" }
  ├─ 落用户消息（短 session，立即提交）
  ├─ LangGraph ReAct 图（同一短 session，最多 agent_max_steps 次工具）
  │   ├─ create_agent(model, tools, system_prompt) 编译状态图
  │   ├─ model 节点: 决定 tool_calls（工具 schema 由 LangChain 生成）
  │   ├─ tools 节点: 并行调度同一轮的多个调用，闭包执行下列工具
  │   │   ├─ search_knowledge_base -> 复用 retrieve()，强制关闭 multi-query
  │   │   ├─ read_chunk_neighbors -> 同文档相邻 chunk（按 kb_id 校验归属）
  │   │   └─ get_document_outline -> 标题分段、chunk 序号、页码
  │   ├─ Pydantic 校验参数；未知工具/非法参数/工具异常都变成结构化 observation
  │   └─ astream_events: on_tool_start/on_tool_end -> agent_step 落库 + SSE
  ├─ 证据按 chunk_id 去重，按 (最好排名, 首次出现) 排序，最多 10 条
  ├─ 关闭 session -> 发 retrieval trace -> 原 grounded Prompt 流式回答
  └─ 落 assistant 消息 + citations + agent_run（stopped_reason 等）
```

图由 `langchain.agents.create_agent`（LangChain 1.x 的 LangGraph 实现）构建，不再手写 `while` 循环。项目保留的控制点是：每次请求创建工具闭包并注入 `ToolContext`；在工具内部用 `asyncio.Lock` 串行化共享 `AsyncSession` 的访问；通过 `astream_events(version="v2")` 把 `on_tool_start/on_tool_end/on_tool_error` 翻译成既有的 `AgentStepRecord`；根据预算推导 `recursion_limit`，作为图级别死循环兜底。

规划图和最终作答是两段：图只决定"还要不要检索"，不产出用户可见答案。这样最终回答仍然经过原来的编号引用 Prompt，`[n]`、`citation_stats`、引用抽屉和 trace 与单轮模式一致，也能避免让规划模型自由发挥导致引用编号错位。

边界都在服务端：步数预算、单轮决策超时、单工具超时、工具白名单、参数校验、每 KB 的 ownership 过滤。任何一次 LLM 调用或工具失败都不直接抛给用户，而是 fail-open：带着已收集的证据继续作答，把原因写进 `agent_runs.stopped_reason`（`max_steps` / `turn_timeout` / `llm_error`）。`GraphRecursionError` 同样归入 `max_steps`，防止模型反复请求同一个工具把请求拖死。前端右上角是「自动 / 快速 / Agent」三档，选择按浏览器记住，默认「自动」。

落库的是结构化步骤，而不是思维链：工具名、经过校验的参数、观测摘要、状态、耗时。这既够前端还原"它查了什么、为什么查不到"，又不会把模型的内部推理写进数据库。

### 4. 评测运行

```text
POST /eval/datasets/{id}/runs
  ├─ 校验: 数据集非空、KB 切块参数未变、基线 run 属于同一数据集
  ├─ 快照 RetrievalConfig + index_version -> 202
  └─ BackgroundTasks: execute_run(run_id)
       ├─ 独立 SessionLocal，每条用例提交一次
       ├─ 逐条 retrieve -> score_case -> eval_results
       ├─ 取消: 下一次循环检查 status=cancelled
       └─ 进程重启: 超过 15 分钟的 pending/running -> failed
```

评测只跑到重排为止，不调用 LLM，所以是确定性的、零 API 成本的。运行进度存在 `eval_runs.progress`，前端轮询 `GET /eval/runs/{id}`。

路由评测是另一条更轻的路径：`GET /eval/datasets/{id}/routing-report` 读取用例的 `expected_mode`，对每个问题调用纯函数 `choose_mode`，现场计算准确率、Agent P/R/F1 和混淆矩阵。它不写 run、不排队、不检索、不加载模型；未标注用例只返回预测，不进入指标。`PATCH /eval/cases/{id}` 用 `model_fields_set` 区分「没传」和「显式传 null」，因此可以单独清除路径标注而不影响其他字段。

Dashboard 的 Agent 指标直接聚合 `agent_runs`：运行数、完成率、平均步数、平均工具调用、平均耗时和 token 总量。查询通过知识库 ownership 子查询限制在当前用户，不新增汇总表；数据量增长后可以再考虑定时物化。

### 5. 反馈闭环

```text
PUT /messages/{id}/feedback
  ├─ 只有会话所有者能评价自己的 assistant 消息
  ├─ rating = 1 或 -1；同一消息唯一，重复提交即更新
  └─ 写入 message_feedbacks

POST /messages/{id}/feedback/eval-case
  ├─ 找到该回答前一条用户消息作为 question
  ├─ 被引用的 citations -> relevant_refs（无引用则取全部来源）
  ├─ 没有引用或回答是拒答 -> answerable = false
  └─ 写入（或复用）数据集「用户反馈回归集」
```

assistant 消息落库时会计算 `citation_stats`：解析回答里的 `[n]`，对照实际写入的引用条数，得到有效标记、无效标记、来源数和引用率。Dashboard 的用户满意度与引用有效率都来自这里。反馈不是"点个赞就结束"：点踩可以一键变成回归用例，下一次改检索配置时就能用评测台验证有没有把这个问题修好。

### 6. 启动与健康检查

- `lifespan`：恢复 stale 文档和 stale 评测运行；启动阶段不加载模型。
- `/api/v1/healthz`：进程存活。
- `/api/v1/readyz`：PostgreSQL、Redis、Chroma 三项都可用才返回 200，任一失败返回 503。
- 请求中间件：注入 `X-Request-ID` 和 `X-Process-Time-Ms`，记录方法、路径、状态码、耗时；健康检查不刷日志。

## 数据模型

| 表 | 关键点 |
|---|---|
| `users` | email 唯一、小写归一；argon2 哈希 |
| `knowledge_bases` | `owner_id`；`index_version` 驱动缓存失效；`retrieval_config` JSONB |
| `documents` | `(kb_id, sha256)` 唯一；状态机 + `error_stage`；`page_count` |
| `chunks` | `(document_id, chunk_index)` 唯一；`heading_path`、`page_number`、字符区间 |
| `conversations` / `messages` | 消息状态 `completed/cancelled/failed`，含延迟与 token 用量；`citation_stats` |
| `citations` | 引用排名、分数、片段、页码；chunk 删除后置空 |
| `message_feedbacks` | 一条消息最多一条反馈；`rating ∈ {-1, 1}` + 原因 + 备注 |
| `retrieval_traces` | 改写 query、`sub_queries`、`cache_hit`、四阶段候选、分阶段耗时、配置快照 |
| `agent_runs` / `agent_steps` | 一条 assistant 消息对应一次 run；`stopped_reason` 记录终止原因；step 存取工具名、参数、结构化观测、状态与耗时，按 `(run_id, step_index)` 唯一 |
| `eval_datasets` / `eval_cases` | 数据集快照切块参数；用例标注到 chunk 级 |
| `eval_runs` / `eval_results` | 运行快照配置与索引版本；结果快照问题与标注 |

JSONB 只用于配置、候选、指标、标注这类"整体读写"的数据，不参与高频过滤和 join。

## 关键取舍

### 越权返回 404

所有子资源都先 join 父知识库校验 `owner_id`。不存在和无权限返回同一个 404，避免通过 403/404 的差异探测资源是否存在。

### 后台任务用 BackgroundTasks

当前只有两类后台任务（入库、评测），都不需要跨进程调度。用 BackgroundTasks 省掉了 broker、worker、结果后端和一整套部署。代价：任务跟着 API 进程生命周期走，进程重启会中断；所以启动时要把 stale 状态改成 failed，并由前端提供重试。

扩展路径：任务写 `jobs` 表 -> Celery/RQ worker 消费 -> 任务状态仍存 PostgreSQL。现有状态机和轮询接口不需要改。

### 用 LangChain，但不把可观测性交给它

- **用框架的部分**：`ChatOpenAI` 统一模型接口，换供应商只改 `app/rag/llm.py`；`HuggingFaceEmbeddings` 管 BGE 推理与批处理；`langchain-chroma.Chroma` 提供读写和余弦相关性分数；LCEL 把 Prompt、模型和输出解析器组合成可复用的短链；`create_agent` + `astream_events` 负责 Agent 的循环与事件流。
- **不交给框架的部分**：BM25 检索器（自研 `BaseRetriever`，保留绝对分数）、RRF 融合、cross-encoder 重排、`retrieval_traces` 和 `agent_steps`。这些是项目的差异化，需要拿到每个阶段完整的候选、排名、分数和耗时；如果只调用框架的 `Retriever.invoke()`，这些细节会被抽象掉。
- **写入路径绕过高层 API**：入库时已经算好向量，`add_texts()` 会再次调用 embedding，所以直接使用 `Chroma._collection.upsert(ids, embeddings, documents, metadatas)`。读取路径仍走公开的 `similarity_search_by_vector_with_relevance_scores`，返回的 relevance 等于 `1 - cosine_distance`，与迁移前的分数尺度一致。
- **模型惰性构造**：`HuggingFaceEmbeddings` 在构造函数里就会加载 SentenceTransformer，因此 `_get_embeddings()` 延迟到第一次真正 embedding 时才创建，并用锁保护；`/readyz` 和后台任务不会因为导入模块就吃掉几百 MB 内存。
- **版本边界**：LangChain 1.x 的包拆分较细，`langchain-huggingface` 默认不安装 `sentence-transformers`，所以 torch 仍放在 `[rag]` extra 里，CI 只装 `[dev]`。

### 嵌入式 Chroma

每个知识库一个 collection，chunk id 直接用 PostgreSQL UUID，删除和 upsert 天然幂等。Chroma 是同步库，所有调用都包在 `anyio.to_thread` 里，避免阻塞事件循环。单机够用；要多副本时换成独立向量服务，并在 metadata 中保留 `document_id/chunk_index` 的映射。

### 检索配置快照

`EvalRun.config` 存 `asdict(RetrievalConfig)`，而不是只存用户改过的字段。这样即使默认值以后变了，历史运行仍然可以复现。对比两个 run 时要求 `index_version` 相同，否则 chunk 变了，排名没有可比性。

### 检索缓存按"配置 + 上下文"做指纹

缓存不能只按 query 做键：同一个问题换一套检索参数、换一个索引版本、或者出现在不同会话上下文里，结果都可能不同。所以 key 里包含 `index_version`、完整 `RetrievalConfig` 和历史消息哈希。代价是缓存命中率会低一些，但不会把旧结果发给新配置。缓存是优化而不是依赖：Redis 读写异常时直接当作 miss，继续走真实检索。

### 多查询扩展默认关闭

多查询能提高召回，但会多一次 LLM 调用，延迟和成本都上升。它被做成 `RetrievalConfig` 字段，可以在调试台单次开启、也可以作为评测配置运行，让数据决定值不值得开。失败或超时一律回退到单查询，主链路不因扩展失败而失败。

### 反馈直接进入评测集

反馈如果只存在一张表里，最后没人看。把点踩转成带 `relevant_refs` 的评测用例，等于把线上问题变成下一次可重复验证的回归样本；数据集与切块参数绑定，切块改了会拒绝写入旧标注，避免"标注漂移"。

### 长任务不在事务里做慢操作

embedding 在写事务外完成；SSE 流式期间不持有 session；评测执行器每条用例单独提交。目标是缩短事务、避免长连接，并让取消/崩溃最多损失一条用例。

## 失败与恢复

| 失败 | 表现 | 恢复 |
|---|---|---|
| embedding / 解析失败 | 文档 `failed`，记录阶段和错误 | 前端重试，重试前清理旧 chunk/vector |
| 进程在入库中重启 | 文档卡在 parsing/indexing | 启动时超过 15 分钟标记 failed |
| 进程在评测中重启 | run 卡在 pending/running | 启动时标记 failed，可重新发起 |
| reranker 超时/异常 | 回退 RRF 顺序，trace 标记降级 | 不失败，只损失排序质量 |
| 多查询扩展失败/超时 | 回退单查询，trace 记录 `multi_query_error` | 不失败；可关闭该配置 |
| Agent 工具超时/异常 | 该步记为 error，继续下一步或直接作答 | fail-open；`stopped_reason` 记录原因 |
| Agent LLM 决策失败 | 带着已收集证据生成回答 | fail-open；`stopped_reason=llm_error` |
| Agent 达到步数预算 / 图递归上限 | 停止规划，用现有证据作答 | `stopped_reason=max_steps`，可调大预算 |
| 检索缓存读写失败 | 按未命中处理，继续走真实检索 | 不失败；Redis 恢复后自动重新使用 |
| Redis 不可用（鉴权） | 登录/刷新失败 | 恢复 Redis；鉴权不降级放行 |
| Chroma 不可用 | readyz 503，检索报错 | 恢复后重试；/readyz 可被编排系统使用 |

## 扩展方向

1. **多副本 API**：BackgroundTasks 换成外部队列；Chroma 换成独立服务；SSE 通过负载均衡的 sticky session 或独立网关。
2. **更大语料**：BM25 全量进内存不现实，换 OpenSearch/Elasticsearch 或按 KB 分片；embedding 批处理与增量索引。
3. **生成侧评测**：citation validity、人工抽检、A/B 回答对比；仍然不建议直接用 LLM judge 当唯一标准。
4. **多租户与权限**：团队、角色、审计日志；所有查询增加 tenant 维度。
5. **可观测性**：结构化日志 + OpenTelemetry trace，把检索阶段耗时接到 APM。
