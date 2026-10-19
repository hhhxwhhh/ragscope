# 面试题与参考要点

按 RAGScope 的 15 个核心模块整理：v0.5 新增的解析、页码、多查询、缓存和反馈闭环放在第 13 节，Agent 与自动路由在第 14 节，LangChain / LCEL / LangGraph 的集成细节在第 15 节。每题先给结论，再给能落到代码和数字上的细节。面试时不要背全文，挑 2~3 个点讲清楚即可。

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

**Q1：为什么默认只做 retrieval-only？生成评测怎么放进去？**

- 检索指标是确定性的、零 API 成本、可重复，适合做回归基线。
- 忠实度、相关性这类生成指标需要 LLM judge 或人工标注，成本高且本身不稳定；小项目引入后容易把噪声当结论。
- 所以默认只回答"该找的资料有没有找到"；需要观察 Prompt 时，用 `include_generation` 显式开启生成评测，只加确定性信号（拒答、引用格式、越界编号、耗时、token），不引入第二个模型打分。
- 生成失败只记录在单条用例上，检索指标和整次运行不受影响；检索延迟与生成延迟分开统计，否则慢模型会污染配置对比。

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

**Q4：路由策略怎么评测？为什么不能只看准确率？**

- 用例增加可选的 `expected_mode`（`rag` / `agent`），报告现场调用纯函数路由器，算 accuracy、Agent precision / recall / F1、混淆矩阵和预测 Agent 占比；不检索、不调模型、不落 run，所以可以随手刷新。
- 未标注用例的 `correct=null`，只展示不评分；标注必须来自人工判断，不能拿路由器输出回填，否则指标恒为 100%。
- 只看准确率会掩盖错误方向：演示集 96.6% 里没有 `agent→rag`，说明多跳问题没被漏掉；唯一的 `rag→agent` 是 `导致` 信号把单段政策问题升级成了 Agent。两类错误的成本不对称——漏掉多跳只是回答不完整，过度升级是多出若干次模型调用，所以 precision 和预测 Agent 比例要单独盯。
- 报告也能防止「改规则改到过拟合」：任何正则调整都应重跑，确认不是把真正的多跳问题一起降级，同时看 Dashboard 的 Agent 运行数和 token 变化。

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
- `langchain-huggingface` 默认不会安装 `sentence-transformers`，所以把它放在 `[rag]` extra；`HuggingFaceEmbeddings` 延迟到 `_get_embeddings()` 才构造，生产镜像装 `.[rag]`，CI 装 `.[dev]`。
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

## 14. Agentic RAG 与自动路由

**Q1：为什么用 LangChain / LangGraph，而不是继续手写工具循环？**

- 最开始确实是手写的：`chat.completions.create(tools=..., tool_choice="auto")` + `while` 循环。它能跑，但工具 schema 序列化、消息拼接、并行 tool call、递归上限、超时和事件流都要自己维护，代码里一半是在重写框架已经解决的东西。
- 迁移后 `create_agent` 编译一张 LangGraph 状态图，模型节点与工具节点由框架调度；`astream_events(version="v2")` 直接给出 `on_tool_start/on_tool_end/on_tool_error`，我们只负责翻译成既有的 `AgentStepRecord`。存储格式、SSE 协议、前端时间线都不用改。
- 但边界保留在项目里：步数预算、单工具超时、`AsyncSession` 串行化、证据去重、`recursion_limit`、fail-open 和 `stopped_reason` 都是自己的代码。框架负责编排，可观测性和业务约束不外包。
- 代价是版本耦合和抽象层变厚；所以 `langchain*` 依赖锁了大版本，测试用脚本化 fake model 跑真实图，避免把框架行为当成黑盒。

**Q2：为什么把"规划"和"最终作答"分成两段？**

- 图只负责决定"还要不要检索、检索什么"，system prompt 明确要求不要输出最终答案；最终答案仍由原来的 grounded LCEL 链流式生成。用户看到的是逐 token 输出，而不是等 Agent 跑完再一次性返回。
- 引用编号是最重要的约束：最终 Prompt 基于去重后的证据列表重新编号，`[n]` 与 `citations` 表、`citation_stats`、来源抽屉完全对齐；如果让规划模型直接写答案，引用编号很容易和实际证据错位。
- 两段式也让测试更容易：Agent 图只断言工具轨迹和证据，生成阶段复用已有的 Prompt/引用测试，不需要一次测试同时覆盖规划与写作。

**Q3：工具参数来自 LLM，怎么防止它乱调、越权或把服务打挂？**

- 工具集合是固定的三个 `@tool`，没有动态导入或任意函数调用；模型只能从 LangChain 暴露的 schema 里选。参数先由 LangChain 按 `args_schema` 校验，非法参数变成 `on_tool_error`，循环仍能继续。
- 我们自己的 `_run_tool` 再做一次预算、Pydantic 校验、`asyncio.Lock` 串行化和 `asyncio.wait_for` 超时。即使框架行为变化，也不会绕过这些约束。
- `read_chunk_neighbors` / `get_document_outline` 不只按 id 查，还要求 `chunk.kb_id == 当前 kb.id`、`document.kb_id == 当前 kb.id`；拿别的知识库的 id 只会得到"不存在"。
- 工具返回内容被当作不可信数据，system prompt 明确要求不要执行其中的指令；最终回答仍然受 grounded Prompt 约束。

**Q4：LangGraph 可能并行调用工具，而它们共享一个 AsyncSession，怎么处理？**

- 同一轮模型可能返回多个 `tool_calls`，LangGraph 会并发执行工具节点；SQLAlchemy 的 `AsyncSession` 不是并发安全的，两个协程同时查询会触发 `InvalidRequestError` 或连接状态错乱。
- `ToolContext` 里放一把 `asyncio.Lock`，所有工具在锁内访问数据库和模型推理。代价是并行退化为串行，但这与旧的手写循环语义一致，也避免了为每个工具单独建 session。
- 更激进的方案是每个工具开独立 session 或连接池，但那会把事务边界和 ownership 校验拆散；当前优先正确性和可解释性。注意锁是请求级的，不会跨用户共享。

**Q5：多次检索的证据怎么去重和排序？跨查询的 rank 能直接比吗？**

- 以 `chunk_id` 为键去重（同一 chunk 的向量/BM25 候选本来就共享 id），保留 `_agent_best_rank`、`_agent_first_seen` 和来源列表。
- 不同 query 的 rank 语义不同，不能当作绝对分数。实现上只把"该 chunk 在所有查询里见过的最好名次"当作排序提示，名次相同时按首次出现顺序稳定排序；相邻 chunk 用 `父 rank + offset/100` 插入，避免把上下文块排到直接命中之前。
- 最终证据上限 10 条，交给 grounded Prompt 时重新按顺序编号，所以引用编号只依赖最终列表，不依赖某一次检索的原始 rank。

**Q6：Agent 的每一步怎么观测？为什么存结构化步骤而不是思维链？**

- `agent_runs` 一条记录对应一条 assistant 消息：状态、`stopped_reason`、步数、工具调用数、耗时、token 用量、错误。`agent_steps` 存每一步的工具名、参数、结构化观测、状态和耗时，`(run_id, step_index)` 唯一。
- `astream_events` 的 `on_tool_start` 拿参数、`on_tool_end` 拿 ToolMessage、`on_tool_error` 拿校验异常；超预算的调用只给模型看，不落用户可见步骤。SSE 在规划阶段实时发 `agent_step`，最终回答前再发一次兼容原有格式的 `retrieval` trace。
- 不存思维链是刻意的：CoT 不可验证、可能包含敏感内容、对排查也没有结构化观测有用；面试里可以把它讲成"可观测性优先于可解释性叙事"。
- 失败是数据而不是异常：模型失败、工具超时、达到步数预算、触达 `recursion_limit` 都写 `stopped_reason`，并继续用已有证据回答。实测一次冷启动查询触发 30 秒工具超时后，第二步重新检索成功，最终仍基于 7 条引用回答了问题——fail-open 让一次慢工具没有毁掉整轮对话。
- Dashboard 直接把 `agent_runs` 聚合成运行数、完成率、平均步数、平均工具调用、平均耗时和 token 总量，不额外维护汇总表。这些成本和成功率指标和回答质量一起看，才能判断「升级到 Agent」是否值得。

**Q7：自动模式怎么在 RAG 和 Agent 之间选？为什么不用 LLM 分类器？**

- 规则在 `app/rag/router.py`，只看显式信号：比较/选择词（`区别`、`对比`、`哪个`、`分别`）、两个型号用 `和/与/、/跟` 连接、多部分/条件词（`并且`、`导致`、`还要`）、以及多个问号。命中任意一类就升级到 Agent，否则留在单轮 RAG。
- 偏向是刻意设计的：**误判成 RAG 只多跑一次检索，误判成 Agent 会多出若干次模型决策和工具调用**。所以默认保守，拿不准时走便宜路径；用户仍然可以手动选「快速」或「Agent」覆盖。
- 不用 LLM 分类器的原因：自动模式会在每次提问前执行，额外一次模型调用会给最简单的问题也增加延迟和成本，还引入新的失败点；规则可解释、可测试、零 API 成本。代价是语义覆盖有限，遇到很隐含的多跳表达会漏判。如果以后有足够的标注数据，可以换成小分类器或 embedding 相似度，但必须先用评测集证明路由收益大于成本。
- 工程细节：`mode` 是 `rag | agent | auto`，显式值不经过规则；`auto` 且 Agent 未启用时回退到 RAG；SSE `meta` 返回 `mode`、`requested_mode`、`routing_reason`，前端显示「自动 → Agent / 快速 RAG」。路由函数本身是纯函数，单元测试直接覆盖信号和回退分支。
- 路由不是拍脑袋：29 条演示用例人工标注期望路径后，路由报告给出 96.6% 准确率、100% Agent recall、66.7% Agent precision。唯一误判来自 `导致`——它被当作故障排查信号，但「手表进水导致的故障能保修吗」只需要一次检索。保留这个错误并在报告里展示，比悄悄调规则更有说服力。

---

## 15. LangChain / LCEL / LangGraph 集成

**Q1：哪些链路用了 LCEL，为什么？**

- 查询改写、多查询扩展、最终作答都写成 `PROMPT | ChatOpenAI [| StrOutputParser]`。组合后的 `Runnable` 同时提供 `invoke/ainvoke/stream/astream`，所以非流式接口和 SSE 复用同一条链，不需要维护两套请求代码。
- 采样参数用 `model.bind(temperature=0.0, max_completion_tokens=128)` 绑定在链上，而不是写进 Prompt 或改全局模型单例；同一进程里改写、扩展、作答可以共享底层 HTTP 客户端，又有各自的生成参数。
- 最终作答不接 `StrOutputParser`，因为要读取 `AIMessage.usage_metadata`；流式时有些供应商只在最后一个 chunk 给出 usage，`stream_usage=True` 让 LangChain 把 usage 合并到最终块，再由 `usage_to_dict` 统一成 `{input_tokens, output_tokens, total_tokens}` 落库。

**Q2：`ChatOpenAI` 接 DeepSeek 有哪些兼容性细节？**

- 使用 `base_url="https://api.deepseek.com"` 和 OpenAI 兼容协议；`max_completion_tokens` 会被 DeepSeek 接受，`max_tokens` 也可以，但前者与新版 OpenAI API 一致。
- `stream_usage=True` 是流式 token 统计的关键：不加这个参数，流式响应通常没有 usage，Dashboard 和 `messages.token_usage` 会缺数据。
- 模型工厂按 `(temperature, max_tokens, timeout, max_retries)` 做 `lru_cache`，因为 `ChatOpenAI` 会缓存底层 HTTP client；测试改配置后必须 `clear_model_cache()`，否则会拿到旧 key 构造的实例。
- 缺 key 时在发送请求前抛 `ServiceUnavailableError`，接口返回 503，不会先写一条用户消息再失败。

**Q3：`HuggingFaceEmbeddings` 有什么坑？BGE 的查询指令怎么处理？**

- 构造函数会立即加载 SentenceTransformer，import 时创建实例会让 `/readyz`、后台任务、CLI 脚本都吃内存；所以 `_get_embeddings()` 延迟到第一次 embedding 才创建，并用锁避免并发重复加载。
- BGE 中文模型建议查询加指令前缀「为这个句子生成表示以用于检索相关文章：」，文档不加；`embed_texts(is_query=True)` 统一处理，避免调用方忘记。
- `HuggingFaceEmbeddings._embed` 会把换行替换为空格。迁移前后对同一批文本做了比对，余弦相似度为 1.0、最大元素差为 0，因此已有 Chroma 向量不需要重建。
- `langchain-huggingface` 默认不依赖 `sentence-transformers`，torch 仍在 `[rag]` extra；CI 不装，测试用 fake embedding。

**Q4：为什么 Chroma 的读写路径不对称？**

- 读取走公开 API `similarity_search_by_vector_with_relevance_scores()`，返回 `(Document, relevance)`，其中 relevance = `1 - cosine_distance`，与迁移前手写客户端的分数尺度一致，拒答阈值 0.45 不用重标。
- 写入时向量已经在入库阶段算好；`add_texts()` 会用 embedding_function 再算一遍，造成重复推理，所以直接 `store._collection.upsert(ids=..., embeddings=..., documents=..., metadatas=...)`。这是对底层 collection 的受控使用，注释和文档都写明了原因。
- `Chroma` 实例按 KB 缓存（`lru_cache(maxsize=8)`），读写分别缓存；删除 KB 时 drop collection 并清缓存，避免拿到已删除 collection 的句柄。
- Chroma 客户端是同步的，异步路径用 `anyio.to_thread` 包装；`/readyz` 用原始 `chromadb.PersistentClient` 心跳，不加载 embedding。

**Q5：自研 BM25 怎么接入 LangChain？**

- `JiebaBM25Retriever(BaseRetriever)` 实现 `_get_relevant_documents` 和 `_aget_relevant_documents`，让 BM25 也能作为标准 `Runnable` 被组合或调试；异步实现用 `anyio.to_thread.run_sync` 包装 CPU 计算。
- 额外提供 `scored_search()` 返回 `(Document, absolute_score)`，因为 `BaseRetriever` 的接口只返回文档，而 trace 需要保留 BM25 原始分数。
- 索引按 `(kb_id, index_version)` 缓存；入库、删除、重试成功后递增版本，旧索引自然失效。文档转成 `langchain_core.documents.Document` 时把 chunk_id 放进 `metadata`，这样检索结果还能映射回 PostgreSQL。

**Q6：怎么把 LangGraph 的事件流翻译成原有 SSE 协议？**

- `agent.astream_events({"messages": messages}, version="v2")` 会发出模型和工具的完整生命周期事件；只监听 `on_chat_model_end`（累计 turn 数和 usage）、`on_tool_start`（记参数和开始时间）、`on_tool_end`（解析 ToolMessage）、`on_tool_error`（参数校验失败）。
- `_parse_observation` 兼容 ToolMessage、字符串 JSON、普通字符串三种输出；预算拒绝的 observation 直接跳过，不作为用户可见步骤。
- `GraphRecursionError` 映射为 `stopped_reason="max_steps"`，`APITimeoutError/TimeoutError` 映射为 `turn_timeout`，其他异常映射为 `llm_error`；无论哪条路径，最后都发一个 `result` 事件，保证上层能落库并继续作答。
- 事件翻译层是独立函数，测试用 `FakeMessagesListChatModel` 重放固定消息，真实跑 LangGraph 图，不需要网络和 API key。

## 16. Prompt 工程与生成评测

**Q1：Prompt 版本为什么要放在代码里，而不是数据库热编辑？**

- 代码里的 Prompt 有 diff、review 和 git 历史；每条 assistant 消息记录 `prompt_variant`，能回答"这条差答案当时用的是哪一版"。
- 评测运行也记录变体 id，历史指标不会被一次线上编辑悄悄改变口径，复现实验时不会出现"代码没变但结果变了"。
- 热编辑的代价是缺少发布状态、回滚和审计；如果真要做运营后台，应该新增版本表 + 发布/灰度机制，而不是原地覆盖正文。
- 当前取舍是"可复现优先"，新增变体需要改代码发版；对小团队这是可接受的成本。

**Q2：改 Prompt 后怎么判断变好还是变坏？**

- 固定数据集、检索配置和 `index_version`，只改变 `prompt_variant`，否则分不清差异来自检索还是生成。
- 先看确定性指标：不可答案例的拒答率、可答案例的误拒答率、引用合规率；再看平均生成耗时和 token。
- 指标只能筛掉明显回归，最后必须展开逐条答案人工抽检，尤其警惕"引用格式正确但内容答偏"。
- 样本只有几十条时，差异小于 5 个点应视为噪声，不做结论。

**Q3：为什么不用 LLM-as-judge 评忠实度？**

- judge 本身有随机性和位置偏置，换模型/换 Prompt 都会漂移，难以作为回归基线；成本也比确定性规则高。
- 小项目更容易把 judge 的噪声当成 Prompt 改动的效果，做出错误决策。
- 当前用固定拒答句、引用正则和 `citation_stats` 做便宜的代理信号；等样本和预算足够，再引入 judge，并且要人工标注一批数据校准它。

**Q4：拒答识别为什么是"前缀匹配 + 少量措辞"，它的风险是什么？**

- 严格变体要求模型只输出固定拒答句，匹配非常稳定；默认变体措辞自由，所以额外接受"资料不足""无法确认"等常见表达。
- 只检查前 200 个字符，避免答案中间引用规则时误判。
- 风险是换模型、换语言或模型改写措辞后漏判；因此它是回归观察指标，不是对外承诺的准确率，生成评测结果仍要人工抽检。

**Q5：生成评测为什么要在检索之后单独测延迟？**

- 两个阶段的瓶颈完全不同：检索受 embedding、BM25、rerank 影响，生成受模型吞吐和输出长度影响。
- 如果把生成耗时混进 `latency_ms`，比较不同检索配置时会引入与配置无关的方差，P95 失去意义。
- 所以每条用例的 `latency_ms` 只统计检索，生成耗时写进 `generation_latency_ms`；聚合时分别计算。

## 三个可以带数字讲的故事

1. **拒答率 20% -> 100%**：定位到 BM25 无阈值导致语料外问题误命中，改成"向量过阈值才启用 BM25"，用可答/不可答的最高最低分选 0.45，24 条可答案例零误拒。
2. **混合检索没有提升**：在 15 chunk、29 用例上跑 5 组配置，向量基线 R@1 85.4%、MRR 92.4% 最好；重排把延迟从 20ms 拉到 96ms 且 R@5 降 2 个点。结论是"先测再决定要不要付延迟"，而不是删掉混合检索。
3. **索引版本让缓存和评测都可复现**：`index_version` 同时驱动 BM25 缓存失效、Chroma 过滤和评测对比约束；跨版本的 run 直接拒绝比较，避免拿不同 chunk 的排名得出错误结论。
