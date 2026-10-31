# RAGScope

通用文档知识库问答平台（RAG + Agent）：把产品手册、技术文档、制度流程、课程资料或研究报告变成可检索、可引用、可评测的知识库。简单问题走单轮混合检索，比较和多跳问题交给 Agent 多步检索；聊天页的「自动」模式会按问题复杂度在两者之间路由。每次回答都能看清检索到底发生了什么，并用固定评测集判断改配置是不是真的有用。

这是一个面向 Python 后端实习的作品集项目（v0.5 起补齐了可观测、接入协议、多智能体与部署层）。上传、切块、向量/BM25 混合检索、流式回答是 RAG 的常规链路；真正想讲清楚的是后半部分：**检索可观测性 + 评测工作台**。每次回答都记录改写后的 query、四个检索阶段的候选和耗时；任意一次检索可以固化成评测用例；不同检索配置在同一个数据集上跑，对比 recall@k、MRR、拒答率和延迟。

检索与生成基于 **LangChain 1.x / LangGraph 1.x**：`ChatOpenAI` 接 DeepSeek，`HuggingFaceEmbeddings` + `Chroma` 适配现有索引，查询改写、多查询扩展和最终作答都是 LCEL 链，Agent 模式用 `create_agent` 编译 ReAct 状态图。BM25、RRF、cross-encoder 重排和检索 trace 仍由项目实现——这些正是需要精确拿到排名、分数和耗时的部分。产品形态参考了 [RAGFlow](https://github.com/infiniflow/ragflow) 的检索测试台、[Dify](https://github.com/langgenius/dify) 的知识库设计，指标定义参考 [RAGAS](https://github.com/explodinggradients/ragas)，但没有引入 LLM-as-judge。

## 应用场景

这不是某个垂直行业的固定模板，而是一套通用的文档问答底座：换一组文档，就是新的场景。

| 场景 | 语料 | 典型问题 | 主要能力 |
|---|---|---|---|
| 产品 / 技术文档 | 手册、API 文档、发布说明 | 参数是什么、两个型号差在哪、版本是否兼容 | 单跳 RAG、型号对比 Agent |
| 售后 / 客服 | 售后政策、FAQ、故障排查 | 进水还能保修吗、这个故障怎么处理 | 政策 + 参数多跳、引用溯源、拒答 |
| 制度 / 流程 | 员工手册、报销规范、入职指引 | 差旅怎么报、审批要几级、试用期规定 | 跨文档条件检索、版本追溯 |
| 学习 / 研究 | 课件、论文、调研报告 | 两种方法有什么区别、这份报告的核心结论 | 比较型问答、多步检索、来源定位 |
| 个人知识库 | 笔记、剪藏、阅读摘录 | 我之前记过什么、这个观点出自哪里 | 混合检索、引用抽屉、检索 trace |

仓库自带一套「星河科技」消费电子演示语料（产品手册、售后政策、FAQ 和 29 条评测用例），用来跑通截图和指标。它只是其中一个语料样例，不是产品边界。完整的场景矩阵、角色链路、演示剧本、指标口径和简历表述见 **[docs/use-case.md](docs/use-case.md)**；
v0.5 新增的企业级子系统（指标/追踪、API Key、MCP、多智能体、GraphRAG、Celery、K8s）见
**[docs/platform.md](docs/platform.md)**；面试讲法与追问应答见 **[docs/interview-guide.md](docs/interview-guide.md)**。

## 现在能做什么

- 注册 / 登录：access JWT 放内存和 localStorage，refresh token 走 HttpOnly cookie、一次性轮换，前端单飞刷新；登录按 email + IP 做 Redis 失败计数限流
- 知识库 CRUD；.md / .txt / .pdf / .docx 上传，后台解析、结构感知切块、embedding、入库，失败可重试；进程重启会回收 stale 入库任务和评测运行
- 文档解析保留结构：Markdown 标题路径、DOCX 标题与表格、PDF 按页提取并记录页码；chunk、引用、trace 都能追溯到第几页
- 混合检索：BGE 中文向量 + jieba/BM25，RRF 融合，`bge-reranker-base` 可选重排；每个知识库用 `index_version` 让 BM25 与向量缓存失效
- 多查询扩展：可选用 LLM 生成 2–3 个改写问题一起召回，跨越不同表述提高召回；失败或超时会自动回退到单查询
- Redis 检索缓存：按知识库、索引版本、完整检索配置、会话上下文和 query 做指纹，命中时跳过向量化、召回与重排；评测运行强制绕过缓存
- 多轮问答：LangChain `ChatOpenAI` 接 DeepSeek，SSE 流式输出、`[n]` 引用、来源抽屉、可中断、回答带检索 trace
- 自动路由：`mode=auto` 时由确定性规则判断问题是否包含比较、多实体或多跳信号；有信号交给 Agent，没有则走单轮 RAG。规则不额外调用模型，Agent 未启用时自动回退，实际模式和路由原因随 SSE `meta` 返回
- Agentic RAG（手动 + 自动）：基于 LangGraph ReAct 图（`create_agent`），单 Agent 在预算内自主调用 `search_knowledge_base` / `read_chunk_neighbors` / `get_document_outline`，把多跳、对比问题拆成多次检索；每一步的工具、参数、观测、耗时都通过 SSE 实时展示并落库，回答仍走原来的编号引用链路
- 引用质量：每次回答统计引用标记数、有效/无效标记、命中来源数与引用率，写入 `messages.citation_stats`
- 反馈闭环：对回答点赞 / 点踩并填写原因；点踩可一键转成评测用例（自动带入被引用 chunk 作为标注），拒答回答转成不可答案例
- 人工抽检队列：按知识库列出待抽检 / 已抽检回答，抽检时看到完整回答、引用来源和引用率；结论为通过 / 需修改 / 不通过，不通过必须选择闭集问题标签。抽检不通过可一键转入「人工抽检回归集」，形成质量闭环
- 会话管理：按标题搜索、重命名、导出 Markdown
- 运维接口：`/healthz` 存活检查，`/readyz` 同时检查 PostgreSQL / Redis / Chroma；请求带 `X-Request-ID` 和耗时头
- 检索调试台：向量 / BM25 / RRF / 重排四组候选，分阶段耗时瀑布，单次请求覆盖检索参数，显示多查询扩展与缓存命中情况，一键保存为评测用例
- 评测工作台：数据集与 chunk 级标注、配置预设、后台执行与进度、运行取消、逐条结果、两次运行对比
- Prompt Lab（v1）：`grounded` / `strict_citation` / `structured` 三个版本化 Prompt 写在代码里，聊天页可切换；每条 assistant 消息和每次生成评测都记录实际使用的变体。Prompt 不做数据库热编辑，保证任何一次回答都能被 git 历史复现
- 生成评测（可选）：在检索评测之后逐条调用 LLM，用确定性规则统计生成拒答准确率、误拒答率、引用合规率、生成耗时与 token；不引入 LLM-as-judge，生成失败不污染已有的检索指标
- 路由评测：用例可标注期望路径（`rag` / `agent`），按确定性路由器的实际输出计算准确率、Agent 类别的 precision / recall / F1 和混淆矩阵；未标注用例只展示预测，不污染指标
- Dashboard：知识库 / 文档 / chunk / 会话 / 评测 KPI，Agent 运行成功率、平均步数 / 工具调用 / 耗时 / token，用户满意度、引用有效率、人工抽检覆盖率与通过率，14 天活动趋势，最近评测结果
- 深度研究（多智能体）：LangGraph 状态机 `planner → researcher → critic`，critic 判定证据不足时带补充查询回到 researcher，最多 2 轮；子问题拆解通过 SSE `plan` 事件实时展示，最终回答仍走 grounded Prompt，引用口径与单轮一致
- GraphRAG：LLM 抽取实体与关系落 PostgreSQL，查询期用「词表匹配 + 一跳邻居」扩展候选，并记录 `graph_score` / `graph_via`；构建是离线后台任务，索引期间用 Redis `SET NX` 做单飞，前端 ECharts 力导向图可直接查看
- 机器接入：API Key（`rk_` 前缀、只存 SHA-256、scope 分为 `kb:read` / `kb:write` / `chat` / `admin`、可设过期与吊销）+ 审计日志；`/public/*` 提供无状态检索与问答，按 key 做固定窗口限流
- MCP Server：手写 JSON-RPC 2.0 协议层，stdio 与 HTTP 两种传输共用同一个 dispatcher，把知识库暴露为 `list_knowledge_bases` / `search_knowledge_base` / `ask_knowledge_base` / `get_document_outline` 四个只读工具
- 可观测性：自研 Prometheus 指标层（HTTP、检索分阶段、缓存命中、拒答、token、Agent 步数、入库、图谱），`/metrics` 抓取端点 + `/api/v1/ops/summary` 运维快照；OpenTelemetry 可选接入 OTLP，响应头返回 `X-Trace-ID`；日志可切 JSON 并自动注入 `request_id` / `trace_id`
- 异步任务：`TASK_BACKEND=inline|celery` 一键切换，Celery worker 接管入库与评测运行，broker 不可用时自动降级为进程内执行；`/ops/summary` 暴露队列积压
- 数据源与定期同步：web（有界 BFS）/ sitemap / RSS / GitHub 四种连接器，抓取 → HTML→Markdown → 去重 → 增量入库；按 URL 认身份、按正文 sha256 判版本，内容没变完全不触发 embedding；robots.txt、请求间隔、页数/深度上限可配；inline 与 Celery 两种调度后端，run 记录 added/updated/unchanged/failed
- 语义缓存：对首轮问题做 embedding 相似度匹配（默认阈值 0.93），命中后复用「证据集 + 答案」，引用编号仍然正确；按 index_version 与 prompt_variant 失效，每 KB 上限 200 条 LRU 淘汰
- 用量与预算：按天 / 按知识库聚合 `messages.token_usage`，`DAILY_TOKEN_BUDGET` 超限时流式接口 429、JSON 接口降级为拒答；运维页展示今日预算与近 14 天 token 柱状图
- Webhook：订阅 `sync.completed` / `sync.failed` / `eval.completed` / `ingest.failed`，HMAC-SHA256 签名、3 次指数退避重试、投递结果可回查，支持测试投递

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

检索阶段的候选、分数、耗时都会写进 `retrieval_traces`；评测默认只跑这条链路到重排为止，不调用 LLM，需要时再显式开启生成评测。

### Agentic RAG 与自动路由

聊天页右上角有三档：**自动 / 快速 / Agent**。「快速」只跑一次单轮混合检索；「Agent」强制走多步工具循环；「自动」在请求进入时做一次确定性判断：

```text
问题 -> rag/router.py
          ├─ 比较 / 多实体 / 多部分 / 多问句信号 -> agent
          └─ 其他 ---------------------------------> rag
```

规则只看显式信号（`区别`、`对比`、`分别`、两个型号用「和/与」连接、`导致`、`还要`、多个问号等），不额外调用 LLM。这样最简单的问答不会被路由成本拖慢；拿不准时宁可走 RAG，因为误判成 RAG 只多一次检索，误判成 Agent 会多出若干次模型决策。显式指定 `mode=rag|agent` 时规则不生效；`mode=auto` 且 Agent 未启用时回退到 RAG。实际模式、请求模式和路由原因都会写进 SSE `meta`，前端会显示「自动 → Agent」这样的标签。

Agent 路径用 `langchain.agents.create_agent` 编译一张 LangGraph ReAct 状态图（model → tools 循环），模型在预算内自主决定是否继续检索：

```text
问题 -> LangGraph ReAct 图（model 节点决定 tool_calls，tools 节点执行）
          ├─ search_knowledge_base(query, top_k)   # 复用 retrieve()，关闭多查询扩展
          ├─ read_chunk_neighbors(chunk_id, window) # 同一文档的相邻 chunk
          └─ get_document_outline(document_id)      # 标题结构 / chunk / 页码
      -> astream_events 转成 agent_step + 落库 -> 证据按 chunk_id 去重
      -> 图停止 -> 原 grounding Prompt -> 流式回答 + [n] 引用
```

三个工具是每次请求创建的闭包，捕获当前 `ToolContext`（DB session、KB、证据表）；LangGraph 可能并行执行同一轮的多个 tool call，而它们共享一个 `AsyncSession`，所以工具内部用 `asyncio.Lock` 串行化数据库访问。`recursion_limit` 按 `agent_max_steps` 推导，作为图级别的兜底。

几个刻意的取舍：

- **有界**：`agent_max_steps=5`、单轮决策 30s、单工具 60s（照顾 CPU 冷启动）、工具白名单和 Pydantic 参数校验；LangGraph 的 `GraphRecursionError`、工具超时或模型失败都 fail-open，用已收集的证据继续回答，并把 `stopped_reason` 记为 `llm_error` / `max_steps` / `turn_timeout`。
- **规划与作答分离**：图只决定还要不要检索，不写最终答案；最终答案仍由原来的 grounded Prompt 基于去重后的证据流式生成，所以引用编号、`citation_stats`、检索 trace 与单轮模式完全一致。
- **可观测而不是可幻觉**：消费 `astream_events` 的 `on_tool_start/on_tool_end`，落库工具名、参数、结构化观测（结果条数、chunk_id、预览、错误）和耗时，不保存模型思维链；前端可以展开每一步。
- **框架负责编排，状态留在本地**：LangGraph 管循环和工具调度，步骤记录、证据去重、预算和引用仍由项目代码掌握，换框架不会影响存储格式和前端事件协议。
- **成本可见**：自动模式只把明确的多跳问题升级到 Agent，也可以手动选「快速」或「Agent」，选择按浏览器记住；每条 meta 事件都会带回实际模式与路由原因，方便观察一次问题到底走了哪条路。

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

### 路由评测

同一份 29 条用例还标注了「期望走哪条路」：27 条标 `rag`（其中 3 条是不可答案例），2 条需要跨文档比较或多步检索的问题标 `agent`。在「路由评测」页：

| 指标 | 数值 |
|---|---:|
| 路由准确率 | 96.6%（28 / 29） |
| Agent precision | 66.7% |
| Agent recall | 100% |
| Agent F1 | 80.0% |
| 预测 Agent 比例 | 10.3%（3 / 29） |

混淆矩阵是 `rag→rag 26`、`rag→agent 1`、`agent→rag 0`、`agent→agent 2`。唯一的误判是「手表进水导致的故障能保修吗？」：规则里的 `导致` 本来是给多步故障排查准备的多跳信号，但这句话只需要查一段售后政策，因此被过度升级到了 Agent。把这条留在数据集里而不是调规则把它藏掉，是因为路由评测的价值就是暴露这种偏差；如果改规则，也应该重跑这份报告确认没有把真正的多跳问题一起降级。

### Prompt 与生成评测

同一份检索结果可以换 Prompt 再答一遍，这就是 Prompt 工程的对照组。项目内置三个版本：

| 变体 | 约束 | 适合观察什么 |
|---|---|---|
| `grounded` | 先结论后细节，事实尽量标注 `[n]` | 默认回答风格；引用是“尽量”，最接近线上体验 |
| `strict_citation` | 每个事实句必须有 `[n]`，无来源的句子删除 | 引用合规率是否上升，回答是否变短、变生硬 |
| `structured` | 固定「结论 / 依据 / 下一步」 | 操作类、售后类问题是否更容易执行，拒答是否更稳定 |

在聊天页顶部切换变体，回答下方会显示实际使用的版本；在评测运行弹窗打开「同时评测生成」并选择变体，得到：

| 指标 | 口径 |
|---|---|
| 生成拒答准确率 | 不可答案例中，生成答案被判为拒答的比例 |
| 生成误拒答率 | 可答案例中，生成答案被判为拒答的比例 |
| 回答引用合规率 | 未拒答的可答案例中，至少含一个有效 `[n]` 且没有越界编号的比例 |
| 平均生成耗时 / token | 只统计生成阶段，和检索延迟分开 |

拒答判定不调用第二个模型：固定拒答句和少量常见措辞（“资料不足”“无法确认”等）做前缀匹配，引用编号用正则校验是否落在本轮来源范围内。这是一种**便宜的确定性信号**，能发现 Prompt 改动造成的激进拒答或引用退化，但不能替代人工抽检和忠实度评估。生成评测会真实消耗 token，默认关闭；检索-only 运行仍然零成本。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2.0 async + asyncpg |
| 存储 | PostgreSQL 16、Redis 7、ChromaDB（PersistentClient 嵌入式） |
| RAG 框架 | LangChain 1.x（LCEL、`ChatOpenAI`、`HuggingFaceEmbeddings`、`langchain-chroma`）、LangGraph 1.x（`create_agent` ReAct 图） |
| 检索 | BGE 中文向量（`BAAI/bge-small-zh-v1.5`）、rank_bm25 + jieba、`BAAI/bge-reranker-base` |
| 生成 | LangChain `ChatOpenAI` 对接 DeepSeek（OpenAI 兼容接口），SSE 流式 |
| Agent / 编排 | LangGraph 1.x：`create_agent` ReAct 图、`StateGraph` 多智能体（planner / researcher / critic） |
| 协议 | MCP（JSON-RPC 2.0，2025-06-18 版本协商）、OpenAI 兼容接口、SSE、REST |
| 可观测 | Prometheus 指标（自研暴露层）、OpenTelemetry + OTLP/Jaeger、结构化 JSON 日志 |
| 异步 | Celery 5 + Redis broker、`acks_late`、CronJob/beat 级 stale 回收 |
| 数据采集 | httpx 异步抓取、robots.txt + 限速、标准库 HTMLParser 的 readability-lite、feedparser、GitHub contents API |
| 前端 | Vue 3 + TypeScript + Vite、Element Plus、Pinia、Vue Router、ECharts（折线 + 力导向图）、markdown-it + DOMPurify |
| 测试 | pytest + pytest-asyncio、httpx ASGITransport、真实 PostgreSQL/Redis、fake retriever |
| 基础设施 | 多阶段 Dockerfile、Docker Compose（api / worker / beat / prometheus / jaeger）、Nginx（SSE 不缓冲）、K8s 清单（Deployment / HPA / PDB / Ingress / CronJob）、Alembic、ruff、pre-commit、GitHub Actions、Locust |

## 目录

```text
rag_learn/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # auth, kbs, documents, chat, retrieval, eval, prompts,
│   │   │                    # dashboard, api_keys, public, mcp, graph, ops
│   │   ├── core/            # 配置、鉴权(Principal/scope)、metrics、tracing、
│   │   │                    # ratelimit、结构化日志、错误处理
│   │   ├── mcp/             # 手写 MCP：protocol / tools / server(stdio)
│   │   ├── models/          # SQLAlchemy 模型：evaluation、api_key、graph …
│   │   ├── rag/             # llm, embedder, vector_store, bm25, fusion,
│   │   │                    # reranker, query_rewriter, multi_query, prompt,
│   │   │                    # answer (LCEL), retrieval_cache, pipeline,
│   │   │                    # router (auto mode), agent/ (tools + ReAct loop
│   │   │                    # + graph 多智能体)
│   │   ├── schemas/         # Pydantic 请求/响应
│   │   ├── services/        # 入库、检索、会话、反馈、抽检、评测、看板、
│   │   │                    # API Key/审计、图谱、数据源与同步、用量、Webhook
│   │   │   └── sources/     # web / sitemap / rss / github 连接器 + HTML 抽取
│   │   └── workers/         # 入库后台任务、celery_app、tasks、dispatch
│   ├── alembic/versions/
│   ├── scripts/
│   │   ├── seed_demo.py     # 一键创建演示账号、知识库、文档和 29 条用例
│   │   ├── seed_eval.py     # 只重建评测数据集
│   │   ├── ocr_pdf.py       # 扫描件 PDF -> 文本（离线预处理）
│   │   ├── build_graph.py   # 构建/清空 GraphRAG 实体图谱
│   │   └── reindex_vectors.py
│   ├── tests/{unit,integration}/
│   └── Dockerfile
├── frontend/src/
│   ├── api/                 # axios 封装、SSE 解析、keys/ops/graph 客户端
│   ├── components/          # RetrievalDebugPanel, EvalPanel, ReviewQueuePanel
│   ├── views/               # Dashboard, KbDetail, Chat, Graph, Ops, ApiKeys,
│   │                        # EvalRun, EvalCompare
│   └── types/
├── data/                    # 5 篇虚构的星河科技产品文档
├── docs/                    # 应用场景、架构、评测设计、MCP、平台层、
│                            # 数据源同步、语义缓存、面试手册
├── notebooks/learning/      # 最开始的手写 RAG notebook
├── deploy/
│   ├── nginx.conf           # SPA + /api 代理 + SSE 不缓冲
│   ├── prometheus.yml       # 抓取配置
│   ├── alerts.yml           # 错误率/延迟/入库失败/拒答率告警规则
│   ├── k8s/                 # Deployment / HPA / PDB / Ingress / CronJob
│   └── locust/              # 读路径压测脚本
├── .pre-commit-config.yaml  # ruff + YAML 校验 + 导入冒烟 + 前端类型检查
├── Makefile                 # make help 查看全部命令
├── .github/workflows/ci.yml # 后端 ruff/pytest + 前端 typecheck/build
├── docker-compose.yml       # 开发：只起 Postgres + Redis
└── docker-compose.prod.yml  # 生产：nginx + api + worker + beat + prometheus + jaeger
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

### 扫描版 PDF

运行时解析器只接受带文字层的 PDF；扫描件会明确报错，而不是索引一堆空文本。扫描件可以先用离线脚本转成 `.txt`，再走正常上传：

```bash
cd backend
pip install -e ".[ocr]"
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/ocr_pdf.py \
  ../data/book.pdf ../data/book_ocr.txt --dpi 180
```

`ocr_pdf.py` 用 PyMuPDF 逐页渲染、RapidOCR 本地识别，输出里保留 `【第 N 页】` 标记，后续可以按页引用。342 页的书在 CPU 上大约 2–3 秒/页。OCR 是预处理步骤，不进入 API 运行链路，也不需要 GPU；`.pdf` 和 OCR 中间产物已在 `.gitignore` 中忽略。

### Docker 一键启动

```bash
cp .env.example .env                 # 填 SECRET_KEY 和 DEEPSEEK_API_KEY
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml exec api python scripts/seed_demo.py
```

打开 http://localhost:8080。生产 Compose 包含 nginx、API、PostgreSQL、Redis，API 容器启动时自动跑 Alembic；Chroma、上传文件和 Hugging Face 模型都有独立 volume。API 的 `/api/v1/readyz` 被配置成容器健康检查。

> 镜像需要安装 CPU 版 torch，首次构建下载量较大；本地开发仍建议用上面的 uvicorn + Vite 组合。

## 企业级能力怎么跑起来

这一层不是概念演示，每条命令都能在本机复现。设计说明见 **[docs/platform.md](docs/platform.md)**。

### 接入外部数据源（定期补充真实内容）

```bash
# 建一个 3 页、1 跳、1.2 秒间隔的网页数据源，并立即同步一次
cd backend
PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_sources.py \
  --kb "星河科技" --type web --url https://fastapi.tiangolo.com/tutorial/ --sync

# GitHub 文档源（私有仓库需要 GITHUB_TOKEN）
PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_sources.py \
  --kb "星河科技" --type github --repo tiangolo/fastapi --path docs/en/docs/tutorial --limit 5
```

也可以在知识库详情页「数据源」标签里配置、预检、手动同步或设置每 N 分钟自动同步。
设计说明（增量策略、调度、抓取合规）见 **[docs/sources.md](docs/sources.md)**。

### API Key 与机器接入

```bash
# 1. 在 Web 端「API Key」页创建，或直接调接口（需要登录态）
curl -s -X POST localhost:8000/api/v1/api-keys \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"smoke","scopes":["kb:read","chat"],"expires_in_days":30}'

# 2. 无状态检索，不建会话、不写消息
curl -s -X POST localhost:8000/api/v1/public/kbs/$KB_ID/search \
  -H "Authorization: Bearer rk_xxx" -H 'Content-Type: application/json' \
  -d '{"query":"X1 的电池容量是多少","top_k":5}'

# 3. 无状态问答；没有证据时直接拒答，不消耗 token
curl -s -X POST localhost:8000/api/v1/public/kbs/$KB_ID/ask \
  -H "Authorization: Bearer rk_xxx" -H 'Content-Type: application/json' \
  -d '{"question":"进水还能保修吗"}'
```

密钥只存 SHA-256、明文只显示一次；`/public/*` 按 key 做 60 次/分钟限流；
`admin` 之外的越权调用在依赖层就被拒绝。

### MCP：让 Claude Desktop / Cursor 直接检索知识库

```bash
cd backend
RAGSCOPE_API_KEY=rk_xxx ./.venv/Scripts/python.exe -m app.mcp.server
```

客户端配置与协议设计见 **[docs/mcp.md](docs/mcp.md)**。工具是只读的：列库、混合检索、
带引用问答、文档大纲。

### 深度研究与知识图谱

```text
聊天页右上角：自动 / 快速 / Agent / 深度研究
```

深度研究走 `planner → researcher → critic` 状态机，critic 认为证据不足时会带补充查询回到
research 节点，最多 2 轮；SSE 的 `plan` 事件让前端在检索开始前就展示子问题拆解。

图谱需要先构建一次（每个 chunk 一次 LLM 调用）：

```bash
make graph           # 或：PYTHONPATH=. .venv/Scripts/python.exe scripts/build_graph.py --all
```

构建是幂等的，重复执行只会累加 `weight` / `mention_count`。建好之后检索会自动多一跳扩展，
在检索调试台的阶段瀑布里能看到 `图扩展（1 跳）` 的耗时。

### Celery worker

```bash
# 本地想验证 worker：先起 Redis，再
TASK_BACKEND=celery celery -A app.workers.celery_app:celery_app worker -Q ragscope -l info
```

`TASK_BACKEND` 默认 `inline`，本地和 CI 不需要额外进程；broker 不可用时会自动降级，
上传不会丢。

### 观测

| 入口 | 内容 |
|---|---|
| `GET /metrics` | Prometheus 抓取端点（可设 `METRICS_TOKEN` 保护） |
| `GET /api/v1/ops/summary` | 请求量 / 错误率 / P50 P95 P99 / 拒答率 / token / 队列积压 / 语义缓存命中 |
| 响应头 `X-Trace-ID` | 开启 OTel 后可贴进 Jaeger 查完整链路 |
| `docker compose -f docker-compose.prod.yml up` | 自带 Prometheus(:9090) 与 Jaeger(:16686) |

日志：`LOG_FORMAT=json` 输出单行 JSON，自动带 `request_id` / `trace_id`。

### 部署与压测

```bash
kubectl apply -k deploy/k8s            # 参考拓扑：Deployment/HPA/PDB/Ingress/CronJob
locust -f deploy/locust/locustfile.py  # 只压读路径，不烧 token
```

## 评测设计

- 用例标注到 chunk 级：`relevant_refs = [{document_id, chunk_index, filename}]`；数据集同时快照标注时的 `chunk_size / chunk_overlap`，切块参数变了就不该继续用旧标注
- 每次运行快照完整 `RetrievalConfig` 和知识库的 `index_version`；**不同索引版本的运行拒绝对比**，因为 chunk 已经变了，排名没有可比性
- 可答案例算 Recall@1/3/5/10、Hit@5、MRR；不可答案例只算拒答准确率；另有可答案例误拒答率
- 出错用例保留在结果里，但不参与质量指标的平均；延迟用 p50 / p95，避免长尾把均值带偏
- 默认 retrieval-only，跑 29 条用例零 API 成本；`include_generation=true` 时额外调用 LLM，用确定性规则统计生成拒答、引用合规、耗时和 token，不做 LLM judge
- Prompt 变体写在代码里并随消息 / 评测运行落库，便于回答级归因和跨版本复现；不在数据库里做 Prompt 热编辑
- 路由评测与检索评测共用用例，但完全独立：`expected_mode` 可选，未标注的用例不进准确率；预测只调用纯函数路由器，不检索、不调模型，报告可以随开随看
- 执行器在 FastAPI BackgroundTasks 中跑，每条用例独立提交、更新进度；取消时保留已完成部分

## 测试与 CI

```bash
cd backend
.venv/Scripts/python.exe -m ruff check app scripts tests
.venv/Scripts/python.exe -m pytest -q
```

- 120 个测试，`tests/unit` 覆盖解析器（Markdown / DOCX / 空白 PDF）、页码映射、切块、RRF、指标计算、路由报告（准确率 / precision / recall / F1 / 混淆矩阵 / 未标注处理）、Prompt 注册表（默认变体、未知变体、固定拒答句、构建缓存）、生成指标聚合与拒答识别、JWT、多查询解析、检索缓存 key、引用统计、自动路由信号与回退，以及用脚本化 fake model 跑真实 LangGraph 图验证 Agent 工具调用、参数校验、证据去重、步数预算、token 汇总与模型失败时的 fail-open；`tests/integration` 覆盖注册/登录/刷新轮换/登出、登录限流、知识库越权 404、评测数据集校验、期望路径的 PATCH 语义与路由报告、Prompt 列表接口、生成评测的答案落库 / prompt 归因 / 无 key 拒绝、后台执行器、运行对比、stale 运行恢复、反馈增删改与转评测用例、人工抽检队列的筛选 / 校验 / 越权 404 / 转回归用例、会话搜索/重命名/导出，以及未配置 LLM key 时 Agent / auto 接口的 503。
- 集成测试使用独立数据库 `ragscope_test` 和 Redis DB 1；session 开始时自动建库并跑迁移，每个测试前清空表和 Redis。
- 评测执行器测试 mock 掉 `retrieve`，CI 只装 `.[dev]`，不需要下载 torch。
- GitHub Actions 分两个 job：后端 ruff + pytest（PostgreSQL/Redis service），前端 typecheck + build。配置在 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

更细的设计说明：

- [docs/use-case.md](docs/use-case.md)：应用场景、角色工作流、演示剧本、指标口径、简历表述
- [docs/architecture.md](docs/architecture.md)：请求链路、数据模型、失败恢复、扩展路径
- [docs/evaluation.md](docs/evaluation.md)：指标定义、拒答标定、实验方法、结果解读
- [docs/interview-questions.md](docs/interview-questions.md)：15 个模块的面试题与参考要点

## 几个实现取舍

- **长期用 LangChain 标准件，可观测性不外包**：模型调用、embedding、向量库适配、Prompt 组合和 ReAct 图交给 LangChain/LangGraph，少写样板并保留换模型/换向量库的空间；BM25、RRF、重排和 trace 继续自研，因为要拿到每个阶段的原始排名、分数和耗时。入库写入时直接调用 Chroma 底层 `upsert` 传预计算向量，避免 `add_texts` 二次 embedding。
- **Chroma 嵌入式而不是独立服务**：单机作品集没必要多一个容器，直接 `PersistentClient`，代价是扩展能力有限，文档里写清楚。
- **BackgroundTasks 而不是 Celery**：当前只有一个后台任务类型，加消息队列会引入 Redis 队列、worker 进程和部署复杂度；评测运行需要可取消和进度，用数据库状态轮询就够了。
- **BM25 由向量门槛控制**：BM25 分数没有绝对意义，语料外的短问题也会匹配到单个字。先看有没有向量候选过阈值，没有就直接拒答，不再交给 BM25 制造噪声。
- **SSE 流式期间不持有数据库 session**：先落用户消息，检索和生成阶段用短 session，流结束后再落 assistant 消息；断连时把部分回答标记为 cancelled。
- **越权访问返回 404**：所有 KB 子资源都通过父 KB 做 ownership 校验，不通过就当作不存在。

## 已知局限

- 语料很小，评测数字只能说明流程跑通，不能当成通用结论
- 生成侧只有拒答与引用格式的确定性检查，没有答案忠实度 / 相关性自动评分，也没有 LLM-as-judge；拒答识别依赖固定措辞，换模型后可能需要补充匹配规则
- Prompt 变体随代码发布，新增版本需要改代码和发版；没有面向运营的 Prompt 编辑、灰度与回滚平台
- 支持 .md / .txt / .pdf / .docx，但扫描件 PDF 需要 OCR；PDF 只保留页码，不保留版式与表格结构
- 多查询扩展是串行调用 LLM，开启后会增加一次生成侧延迟；Redis 缓存不做过期淘汰以外的容量治理
- 限流用 Redis 固定窗口并在每次失败时重置窗口；没有实现滑动窗口或令牌桶
- 权限模型是「用户拥有知识库」+ API Key scope，没有 workspace / 团队成员角色；审计只覆盖密钥生命周期与写操作，不是全量审计
- Chroma 为嵌入式单机模式，K8s 里 RWO 卷实际把 API 限制在单副本；要水平扩展需换 server 模式向量库
- GraphRAG 只有一跳邻居，没有社区聚类与摘要；抽取质量取决于模型，没有做实体消歧
- 深度研究是三种模式的架构论证，还没有做「同一批多跳问题在三种模式下的对照实验」
- 没有 Playwright E2E，前端只做类型检查和构建验证

## 接下来

- 扩大「用户反馈回归集」和「人工抽检回归集」，把线上点踩、抽检失败持续转成回归用例，而不是继续堆检索组件
- 用生成评测对 Prompt 变体做 A/B：同一数据集、同一索引版本，只看拒答 / 引用 / 延迟的取舍；样本足够后再考虑接入 LLM-as-judge 做忠实度评估，并用人工抽检结果校准 judge
- 用同一份标注集对照三种模式（快速 / Agent / 深度）的 recall、引用合规率与成本，给「多智能体到底值不值」一个数字答案
- 语义缓存：当前缓存是精确 query 指纹，embedding 相似度缓存能显著提高命中率，但要处理「相似但答案不同」的风险
- Chroma 换成 server 模式向量库，补齐多副本与索引重建路径；限流升级为滑动窗口
- 权限模型引入 workspace 与成员角色（owner / editor / viewer），审计扩展为全量写操作
- 增加 Playwright 冒烟测试与前端组件测试

## License

MIT
