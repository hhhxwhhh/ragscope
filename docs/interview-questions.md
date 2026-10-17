# 面试题与参考要点

按 RAGScope 的 12 个核心模块整理，v0.5 新增的解析、页码、多查询、缓存和反馈闭环放在第 13 节。每题先给结论，再给能落到代码和数字上的细节。面试时不要背全文，挑 2~3 个点讲清楚即可。

---

## 1. 架构与取舍

**Q1：为什么用单体 FastAPI，而不是把检索、评测拆成微服务？**

- 当前只有一个部署单元，模型推理、后台任务、API 共享进程内缓存（embedding/reranker 单例），拆开会让每次检索多一次网络调用，还要解决模型加载和显存复制。
- 后台任务用 FastAPI `BackgroundTasks`，没有 broker/worker 的运维成本。评测和入库都通过数据库状态机表达，接口不依赖任务在哪里跑。
- 拆分的触发条件写清楚了：需要多副本 API、任务需要重试/延迟/优先级、或单机内存放不下模型时，再把任务迁到 Celery/RQ，把 Chroma 换成独立向量服务。迁移时状态机和轮询接口不需要改。

**Q2：为什么越权访问返回 404 而不是 403？**

- 403 等于告诉对方"这个 ID 存在，只是你没权限"，可被用来枚举资源。
- 所有子资源（文档、会话、评测数据集、运行）都 join 父知识库校验 `owner_id`，不存在和无权限走同一个 `NotFoundError`。
- 副作用是排障时不能从状态码区分两种情况，但这是有意的安全取舍。

**Q3：一次请求里哪些地方可能阻塞事件循环？怎么处理？**

- 同步库：Chroma 客户端、sentence-transformers 推理、文件解析、jieba/BM25 构建。
- 统一用 `anyio.to_thread.run_sync` 丢到线程池；异步 Redis/SQLAlchemy 本身不阻塞。
- 注意不要在事件循环里调用 `model.encode()` 或 `collection.query()`；也不要在 SSE 流式期间持有数据库 session。

---

## 2. 鉴权与登录安全

**Q1：为什么 access token 放 localStorage，refresh token 放 HttpOnly cookie？**

- access token 生命周期短（30 分钟），需要被前端读出来放到 `Authorization` 头，放 localStorage 的 XSS 风险被时间窗限制。
- refresh token 生命周期长（7 天），放 HttpOnly + SameSite=Lax cookie，JS 读不到，降低 XSS 直接窃取长期凭证的风险。
- 更严格的做法是 access 也放内存、refresh 走 cookie，页面刷新时静默续期；当前实现优先简单和可调试。

**Q2：refresh token 轮换怎么防止重放？**

- 签发时生成 `jti`，在 Redis 存 `refresh:{jti} -> user_id`，TTL 与 token 一致。
- 刷新时用 `GETDEL` 原子地取出并删除；同一个 token 第二次使用会拿不到值，直接 401。
- 成功后签发新的 access + refresh，旧 token 立即失效。并发刷新时只有一个能成功，前端用单飞（single-flight）Promise 合并并发 401。

**Q3：登录限流为什么按 email 和 IP 两个维度？**

- 只按 email：攻击者换邮箱就能绕过，且可以恶意锁死别人的账号。
- 只按 IP：同一 NAT 后的正常用户会互相影响，分布式代理也能绕过。
- 当前实现用 Redis 计数（默认 5 次 / 10 分钟），每次失败同时递增 email 和 IP 两个 key 并刷新过期时间，任意一个超限就 429；登录成功只清 email 计数，不清 IP，避免攻击者用已知正确密码重置 IP 配额。
- 计数窗口跟着最后一次失败滑动，成本低但精度有限；生产可换滑动窗口或令牌桶。

---

## 3. 异步 SQLAlchemy

**Q1：`expire_on_commit=False` 解决什么问题？**

- 默认提交后 ORM 对象过期，下一次访问属性会触发懒加载；在 async 场景中懒加载会抛 `MissingGreenlet`。
- 设置为 False 后，提交后仍可读取已加载字段，适合"提交后马上序列化响应"的模式。
- 代价是对象可能持有旧值，需要显式 `refresh()` 获取数据库最新状态。

**Q2：为什么后台任务要开自己的 session？**

- 请求级 session 在响应结束后会被关闭；后台任务的生命周期可能更长，复用会拿到已关闭的 session。
- 执行器用 `SessionLocal()` 新建 session，每条用例提交一次，崩溃/取消最多损失当前一条。
- 同时避免长事务持有连接，减少连接池耗尽。

**Q3：N+1 查询在这个项目里怎么避免？**

- 列表接口显式 `selectinload` 需要的关联（如会话消息），或者直接写聚合查询（Dashboard 的 count/group by）。
- 评测列表用相关子查询一次性算 case_count/run_count，避免逐行查询。
- 原则：API 返回结构决定查询形状，不在 Pydantic 序列化阶段触发懒加载。

---

## 4. 文档入库与切块

**Q1：结构感知切块比固定长度切块好在哪？**

- 先按 Markdown 标题切块，保留 `heading_path`（如「产品手册 / 续航」），embedding 文本加上标题前缀，检索时标题也参与语义匹配。
- 块内再按句子边界回退切分，避免把一句话劈成两半；找不到句号才硬切。
- 短于 30 字的块直接丢弃，减少无信息 chunk。
- 代价是实现比 `text[i:i+400]` 复杂，且切块参数变化会让已有的 chunk 级评测标注失效。

**Q2：入库任务怎么保证幂等和可恢复？**

- 每个文档有 `(kb_id, sha256)` 唯一约束，重复上传直接 409。
- 重试前删除该文档的旧 chunks 和 Chroma 向量，再从头跑，重复执行不会产生重复数据。
- 状态机 pending -> parsing -> chunking -> embedding -> indexing -> indexed；失败落 `failed + error_stage`。
- 进程重启会留下非终态文档，启动时把超过 15 分钟的标记为 failed，前端可重试。

**Q3：为什么 embedding 在写事务之外做？**

- embedding 是整条链路最慢的部分（CPU/GPU 推理），放在事务里会长时间占用数据库连接和行锁。
- 先算完向量，再开短事务写 chunks 和状态；失败时最多留下未提交的中间状态，由状态机恢复。
- 也便于以后把 embedding 换成外部批处理服务。

---

## 5. Embedding 与 Chroma

**Q1：为什么每个知识库一个 collection？**

- 检索天然按 KB 隔离，查询不用额外过滤 `kb_id`，删除 KB 时直接 drop collection。
- chunk id 直接使用 PostgreSQL UUID，upsert/delete 天然幂等，检索结果可以回表拿完整信息。
- metadata 里保留 `document_id/chunk_index/filename/index_version`，用于过滤和展示。
- 局限：跨 KB 检索需要查多个 collection；collection 数量很多时管理成本上升。

**Q2：`index_version` 是干什么的？**

- 每次文档入库/删除/重试成功都会递增，代表"这个 KB 的索引内容变了"。
- Chroma 查询带 `index_version` 过滤，BM25 缓存以 `(kb_id, index_version)` 为 key，旧缓存自然失效。
- 评测运行也快照 `index_version`，不同版本的运行不允许对比，避免拿不同 chunk 的排名做比较。

**Q3：为什么向量要先归一化？余弦距离怎么转相似度？**

- 归一化后内积等于余弦相似度，Chroma 的 cosine 空间计算更直接。
- 查询返回的是 cosine distance `d`，相似度用 `1 - d`，范围大约 `[-1, 1]`；拒答阈值 0.45 是在这个尺度上调出来的。
- 换模型或换距离度量必须重新标定阈值，不能直接复用。

---

## 6. BM25、jieba 与 RRF

**Q1：BM25 分数为什么不能直接和向量分数加权平均？**

- 两者尺度完全不同：余弦相似度在 `[-1,1]`，BM25 是无上界的词频/文档频率打分，且随语料变化。
- 直接相加需要归一化，而归一化又会引入新的超参数和不稳定性。
- RRF 只用排名，不用分数：`score = Σ weight / (k + rank)`，对尺度不敏感，实现简单，是工业界常用的融合基线。

**Q2：RRF 的 k 有什么作用？**

- k 越大，头部排名的优势越平缓，融合更"民主"；k 越小，越偏向各列表的第一名。
- 常用 60，来自原论文经验值；项目里可以通过评测台把 `rrf_k` 改成 10/60/200 做对比。
- 要注意：RRF 丢掉了分数差距信息，两个列表里"断层第一"和"险胜第一"被同等对待。

**Q3：为什么 BM25 要受向量门槛控制？**

- BM25 没有绝对阈值，语料外问题也会因为单字/常用词命中而返回候选，导致拒答失效。
- 当前策略：先看向量 top-k 是否有候选过 `min_vector_score`；没有就直接拒答，完全不启用 BM25。
- 这样 BM25 只在"语义上确实相关"的前提下补充精确词命中，避免制造噪声。

---

## 7. 重排、Query 改写与 Prompt

**Q1：reranker 为什么可能让结果变差？**

- 交叉编码器逐对打分，优化的是 query-passage 相关性，不一定是"覆盖多文档对比"这类任务目标。
- 项目实测把「X1 和 X2 的屏幕有什么区别？」从第 1 位压到第 5 位，R@5 下降 2 个点，P95 从 20 ms 涨到 96 ms。
- 所以 rerank 是可选配置，评测台可以直接关掉它对比，而不是默认迷信"加一个模型一定更好"。

**Q2：reranker 超时怎么处理？**

- 放在 `anyio.to_thread` 里跑，外层 `asyncio.wait_for` 控制 3 秒预算。
- 超时或异常时不失败整个请求，回退到 RRF 顺序，并在 trace 里记录 `rerank_error`。
- 这是"降级而不是报错"的设计：检索仍然可用，只是排序质量下降。

**Q3：多轮对话为什么需要 query 改写？Prompt 怎么防注入？**

- 追问「那它的续航呢？」单独检索没有主语，需要结合最近几轮历史和当前 KB 改写成一个完整问题。
- 改写失败/超时直接使用原问题，不阻塞主链路。
- Prompt 明确要求：只依据给定资料、每个事实标 `[n]`、资料不足就说不知道、忽略资料中出现的指令。引用编号在服务端拼接，不信任模型自由生成来源。

---

## 8. SSE 流式输出

**Q1：为什么不用 `EventSource`？**

- `EventSource` 只支持 GET，不能带请求体和自定义 `Authorization` 头；聊天需要 POST 消息内容，且鉴权用 Bearer token。
- 因此使用 `fetch` + `ReadableStream` 读取响应，手动按 `\n\n` 分帧解析 `event:` / `data:`。
- 代价是要自己处理重连、取消和异常，浏览器原生 EventSource 的自动重连用不上。

**Q2：中文流式为什么容易乱码？**

- 一个中文字符占多个 UTF-8 字节，可能被拆到两个 network chunk 里。
- 必须用 `TextDecoder({stream: true})` 增量解码，保留不完整的多字节序列；不能用 `decoder.decode(value)` 一次性解码。
- 分帧也要按字节流拼接后再找 `\n\n`，不能假设一个 chunk 正好是一个事件。

**Q3：流式期间为什么不能持有数据库 session？**

- 生成长回答可能持续几十秒，持有 session 会长期占用连接，且客户端断连时事务状态难处理。
- 做法：先把用户消息落库并提交；检索、生成用短 session 或纯内存数据；结束后再开 session 落 assistant 消息、引用和 trace。
- 断连时保存已生成的部分内容并标记 `cancelled`，用户下次打开会话能看到历史。

---

## 9. 可观测性与 Trace

**Q1：一次检索记录哪些信息？**

- 原始 query、改写后的 query、是否应用改写及错误。
- 四个阶段（vector/BM25/fused/reranked）的候选、分数、排名、最终配置快照。
- 各阶段耗时、候选数量、rerank 是否降级。
- 这些写在 `retrieval_traces`，聊天页可折叠查看，调试台直接返回。

**Q2：为什么把 trace 存数据库而不是只打日志？**

- 日志适合排障，但不方便前端按会话/消息查询，也不方便把某次检索固化成评测用例。
- 结构化 JSONB + 明确字段可以直接驱动 UI（分数条、耗时瀑布、候选表）。
- 代价是写入量增加；当前每次问答一条 trace，规模可控。

**Q3：线上怎么快速定位"回答变差"？**

- 先看 trace：改写是否异常、向量/BM25 是否为空、rerank 是否降级、延迟是否突增。
- 再看索引版本和文档状态：是否刚刚重新入库、是否有失败文档。
- 最后看评测：用同一数据集跑当前配置，与历史 run 对比，确认是检索退化还是生成问题。

---

## 10. 评测系统

**Q1：为什么 v1 只做 retrieval-only？**

- 检索指标是确定性的、零 API 成本、可重复，适合做回归基线。
- 生成指标（忠实度、相关性）需要 LLM judge 或人工标注，成本高且本身不稳定；小项目引入后容易把噪声当结论。
- 先用 retrieval 指标回答"该找的资料有没有找到"，生成质量用引用和人工抽检兜底。

**Q2：Recall@k 和 MRR 分别适合什么场景？**

- Recall@k 关心"相关信息有没有进入上下文窗口"，适合 RAG 这种把 top-k 全部喂给 LLM 的场景。
- MRR 只关心第一个相关结果排多靠前，适合只有一个正确答案、且越靠前越好的场景。
- 多相关 chunk 时 Recall 更合理；只有一个答案且延迟敏感时 MRR 更有解释力。两个都看，避免单指标过拟合。

**Q3：怎么保证两次评测可比？**

- 同一数据集：问题与标注一致。
- 同一 `index_version`：chunk 内容没变。
- 数据集快照切块参数，KB 参数变化时拒绝运行（409）。
- run 快照完整 `RetrievalConfig`，包括默认值，避免以后改默认值影响历史结果。
- 对比接口拒绝不同索引版本的 run；逐用例给出 better/worse/same，而不只看聚合指标。

---

## 11. 前端状态与网络层

**Q1：多个请求同时 401，怎么避免刷新风暴？**

- axios 响应拦截器里维护一个模块级 `refreshPromise`；第一个 401 发起刷新，其余请求 `await` 同一个 Promise。
- 刷新成功后重放原请求；失败则清 token 并跳转登录。
- 登录/注册/刷新接口本身不参与重试，避免死循环。

**Q2：Vue 3 里 SSE 数据怎么驱动 UI 而不丢响应性？**

- 流式 token 累积到 `ref` 字符串，Vue 才能追踪更新；直接修改普通对象属性不会触发渲染。
- 避免每个 token 都触发 Markdown 全量重渲染，可以按帧/时间片批量更新。
- 卸载或点击停止时调用 `AbortController.abort()`，并处理 AbortError，不要当成失败提示。

**Q3：ECharts 怎么控制打包体积？**

- 不用 `import * as echarts from 'echarts'`，而是从 `echarts/core` 引入，按需注册 `LineChart`、`GridComponent`、`LegendComponent`、`TooltipComponent`、`CanvasRenderer`。
- Dashboard chunk 从约 1044 kB 降到 490 kB。
- 图表实例在 `onBeforeUnmount` 里 dispose，并移除 resize 监听，避免内存泄漏。

---

## 12. 测试、部署与 CI

**Q1：集成测试怎么隔离数据库和外部依赖？**

- `conftest.py` 在导入 app 之前设置环境变量，指向独立数据库 `ragscope_test` 和 Redis DB 1，并把 Chroma/存储目录指到临时目录。
- session 开始时自动创建测试库并跑 Alembic；每个集成测试前 TRUNCATE 所有表、FLUSHDB。
- 通过 `httpx.ASGITransport` 直接调用 ASGI app，不启动端口；测试事件循环设为 session 级，避免 asyncpg 连接跨 loop。
- 评测执行器测试 monkeypatch `retrieve`，不加载 embedding 模型，所以 CI 不需要安装 torch。

**Q2：为什么把 sentence-transformers 做成可选依赖？**

- torch + transformers 安装体积大、CI 慢，而单元测试和大部分集成测试不需要真实推理。
- `import` 延迟到 `_get_model()`，`pyproject.toml` 里放到 `[rag]` extra；生产镜像装 `.[rag]`，CI 装 `.[dev]`。
- 代价是如果部署忘装 extra，错误会推迟到第一次检索；Dockerfile 明确安装 `.[rag]`，并在 readiness 中暴露 Chroma/PG/Redis 状态。

**Q3：Nginx 部署 SSE 需要注意什么？**

- `proxy_buffering off` 和 `X-Accel-Buffering: no`，否则 token 会被 Nginx 攒着一起发。
- 放宽 `proxy_read_timeout`（项目设 300s），避免长回答被断开。
- `proxy_http_version 1.1` 并清空 `Connection` 头，支持 chunked/keep-alive。
- SPA 路由用 `try_files $uri $uri/ /index.html`，否则刷新 `/kbs/xxx` 会 404。

---

## 13. v0.5：解析、多查询、缓存与反馈闭环

**Q1：PDF / DOCX 解析后，怎么让 chunk 和引用知道自己在第几页？**

- 解析器统一返回 `ParsedDocument(text, page_starts, page_count)`，而不是只返回一个字符串。
- PDF 逐页提取，记录每页正文拼接时的字符起点 `(char_offset, page_number)`；DOCX 把 Heading 样式转成 Markdown 标题、表格转成 `a | b` 行，没有页码。
- 切块只关心 `char_start`，用 `bisect_right` 在 `page_starts` 上二分就能得到页码，不需要在切块逻辑里理解 PDF。
- 页码随 chunk 写进 PostgreSQL、Chroma metadata、引用和 trace，前端来源抽屉直接显示「第 n 页」。
- 取舍：只保留页码，不还原版式、表格坐标和图片；扫描件 PDF 没有文本层，直接报错而不是返回空文档。

**Q2：多查询扩展怎么实现？为什么默认关闭？**

- LLM 生成 2–3 条不同表述的 query，和原 query 一起分别走向量 + BM25；每条 query 的向量在一次 batch 里算完。
- 所有排名一起做加权 RRF，候选去重时记录 `matched_queries`，能看出某个 chunk 是被哪几条 query 命中的。
- 整段有超时（默认 8 秒），失败、超时或没配 key 时回退单查询，并在 trace 里写 `multi_query_error`。
- 默认关闭是因为它多一次 LLM 调用，延迟和成本都增加；是否值得开由评测数据决定，调试台和评测配置都能单独打开。
- BM25 仍然受该 query 的向量命中门槛约束，避免扩展出的 query 把语料外噪声带进来。

**Q3：检索缓存的 key 为什么不能只用 query？**

- 同一句话在不同知识库、不同索引版本、不同检索参数下结果不同，所以 key 包含 `kb_id`、`index_version`、完整 `RetrievalConfig`（`asdict` 后排序序列化）。
- 多轮对话里同一个追问依赖上下文，改写后的 query 已经不同；再把历史消息的 `role:content` 做 sha256 放进 key，避免不同会话串结果。
- 缓存值存的是候选列表而不是最终答案：Prompt 或模型变了仍可复用检索结果，也不会缓存"回答"这种强上下文内容。
- 读写 Redis 失败一律按 miss 处理（fail-open）；评测运行强制 `use_cache=False` 并快照该字段，保证测的是真实检索耗时。
- TTL 默认 300 秒，`index_version` 递增会让旧 key 自然失效，不需要手动清理。

**Q4：点赞点踩之后，怎么让反馈真正影响项目？**

- 反馈存在 `message_feedbacks`，一条消息唯一、`rating ∈ {-1, 1}`，重复提交即更新，避免重复计数。
- 点踩可以转成评测用例：问题取前一条用户消息，标注取该回答实际引用的 chunk；拒答或无引用则成为不可答案例。
- 转换写入自动创建的数据集「用户反馈回归集」，同一问题复用已有用例；数据集绑定切块参数，切块变了拒绝写入旧标注。
- 同时 assistant 消息会算 `citation_stats`（标记数、有效/无效、来源数、引用率），Dashboard 展示满意度和引用有效率。
- 诚实说明边界：被引用的 chunk 不一定包含答案，自动转换的用例仍建议人工复核；引用有效率只检查编号是否存在，不判断引用是否支持结论。

## 14. Agentic RAG（可选模式）

**Q1：为什么不用 LangChain / LangGraph，而是手写一个单 Agent 循环？**

- 这里只需要一件事：让模型在预算内决定"还要不要检索、检索什么"。`chat.completions.create(tools=..., tool_choice="auto")` 加一个 `while` 循环就够了，引入框架会增加抽象层、版本耦合和排查难度，面试时也很难讲清每一步。
- 单 Agent 的边界更可控：工具白名单只有 3 个、步数上限 5、参数 Pydantic 校验、每个工具都绑定当前 `kb_id` 做 ownership 过滤。多 Agent 协作会引入角色分工、消息传递和额外 token 成本，但对"知识库问答"没有明显收益。
- 成本上，Agent 模式默认关闭；简单事实问题单轮 RAG 更便宜，多跳/对比问题才值得多花几次决策调用。这个取舍由前端开关和文档说明，而不是默认替用户做决定。

**Q2：为什么把"规划"和"最终作答"分成两段？**

- 规划循环用非流式请求，只关心 `tool_calls`；最终答案用原来的 grounded Prompt + 流式 completion。这样用户仍然能看到逐 token 输出，而不是等 Agent 全部跑完再一次性返回。
- 引用编号是最重要的约束：最终 Prompt 基于去重后的证据列表重新编号，`[n]` 与 `citations` 表、`citation_stats`、来源抽屉完全对齐；如果让规划模型直接写答案，引用编号很容易和实际证据错位。
- 规划阶段的 system prompt 明确"不要输出最终答案"，把两种职责分开，也方便单测：规划循环可以注入 fake client 只测工具轨迹，生成阶段复用已有的 Prompt 测试。

**Q3：工具参数来自 LLM，怎么防止它乱调、越权或把服务打挂？**

- 工具名先查白名单字典，未知工具直接返回 `{"ok": false, "error": "未知工具"}`，不执行任何动态导入。
- 参数用 Pydantic 校验（query 长度、`top_k ∈ [1,10]`、`window ∈ [1,3]`），非法 JSON 或非对象也会变成结构化错误观测，而不是异常。
- `read_chunk_neighbors` / `get_document_outline` 不只按 id 查，还要求 `chunk.kb_id == 当前 kb.id`、`document.kb_id == 当前 kb.id`；拿别的知识库的 id 只会得到"不存在"。
- 每次工具执行有 `asyncio.wait_for` 超时，单轮决策也有超时；工具内部异常在 `execute_tool` 边界被捕获，循环永远能拿到一个 JSON observation 继续走下去。
- 工具返回内容被当作不可信数据，system prompt 明确要求不要执行其中的指令；最终回答仍然受 grounded Prompt 约束。

**Q4：多次检索的证据怎么去重和排序？跨查询的 rank 能直接比吗？**

- 以 `chunk_id` 为键去重（同一 chunk 的向量/BM25 候选本来就共享 id），保留 `_agent_best_rank`、`_agent_first_seen` 和来源列表。
- 不同 query 的 rank 语义不同，不能当作绝对分数。实现上只把"该 chunk 在所有查询里见过的最好名次"当作排序提示，名次相同时按首次出现顺序稳定排序；相邻 chunk 用 `父 rank + offset/100` 插入，避免把上下文块排到直接命中之前。
- 最终证据上限 10 条，交给 grounded Prompt 时重新按顺序编号，所以引用编号只依赖最终列表，不依赖某一次检索的原始 rank。
- 如果以后要做更严格的融合，可以记录每次查询的分数并做加权 RRF；当前选择简单、可解释的启发式，先用 trace 看效果。

**Q5：Agent 的每一步怎么观测？为什么存结构化步骤而不是思维链？**

- `agent_runs` 一条记录对应一条 assistant 消息：状态、`stopped_reason`、步数、工具调用数、耗时、token 用量、错误。`agent_steps` 存每一步的工具名、参数、结构化观测、状态和耗时，`(run_id, step_index)` 唯一。
- SSE 在规划阶段实时发 `agent_step`，前端时间线能看到"查了什么、返回几条、是否超时"，最终回答前再发一次兼容原有格式的 `retrieval` trace。
- 不存思维链是刻意的：CoT 不可验证、可能包含敏感内容、对排查也没有结构化观测有用；面试里可以把它讲成"可观测性优先于可解释性叙事"。
- 失败是数据而不是异常：LLM 失败、工具超时、达到步数上限都写 `stopped_reason`，并继续用已有证据回答。实测一次冷启动查询触发 30 秒工具超时后，第二步重新检索成功，最终仍基于 7 条引用回答了问题——fail-open 让一次慢工具没有毁掉整轮对话。

---

## 三个可以带数字讲的故事

1. **拒答率 20% -> 100%**：定位到 BM25 无阈值导致语料外问题误命中，改成"向量过阈值才启用 BM25"，用可答/不可答的最高最低分选 0.45，24 条可答案例零误拒。
2. **混合检索没有提升**：在 15 chunk、29 用例上跑 5 组配置，向量基线 R@1 85.4%、MRR 92.4% 最好；重排把延迟从 20ms 拉到 96ms 且 R@5 降 2 个点。结论是"先测再决定要不要付延迟"，而不是删掉混合检索。
3. **索引版本让缓存和评测都可复现**：`index_version` 同时驱动 BM25 缓存失效、Chroma 过滤和评测对比约束；跨版本的 run 直接拒绝比较，避免拿不同 chunk 的排名得出错误结论。
