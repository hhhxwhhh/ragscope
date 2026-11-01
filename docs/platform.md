# 企业级改造层：可观测、可接入、可运维

这份文档对应 v0.5 新增的六个子系统。每一节都按同一结构写：**解决什么问题 → 怎么实现 →
关键取舍 → 面试时怎么讲**。代码路径都写在标题下，方便对照阅读。

---

## 1. 可观测性：指标、追踪、日志

`backend/app/core/metrics.py` · `tracing.py` · `logging.py` · `api/v1/ops.py`

### 问题

RAG 服务出问题时的第一句话通常是「答得很慢」或「答案变差了」。这两句话都无法直接行动：
慢在哪一段？是向量化、BM25、重排，还是模型？答案是检索少了，还是重排把正确 chunk 压下去？
没有分阶段指标和 trace，排查只能靠猜。

### 实现

**指标层是自研的**，`app/core/metrics.py` 实现了 counter / gauge / histogram 与 Prometheus
文本暴露格式，约 200 行，没有额外依赖。这不是为了炫技，而是为了离线环境可安装：镜像里
不需要再引入一个客户端库，暴露格式仍然是官方的 `text/plain; version=0.0.4`，Prometheus、
VictoriaMetrics、OTel Collector 都能直接抓。

指标分三层：

| 层 | 指标 | 用途 |
|---|---|---|
| HTTP | `ragscope_http_requests_total`、`..._duration_seconds`、`_in_flight` | 错误率、P95/P99、并发 |
| 检索 | `ragscope_retrieval_stage_seconds{stage}`、`_candidates{stage}`、`_refusal_total` | 定位慢在哪一段、召回是否退化 |
| 业务 | `llm_tokens_total`、`agent_steps`、`ingest_total`、`graphrag_total` | 成本与链路健康 |

**标签纪律**是这里最容易被做错的部分：HTTP 标签使用**路由模板**
（`/api/v1/kbs/{kb_id}`）而不是原始路径，否则每个知识库都会生成一条时间序列，注册表会随
数据量无限膨胀。`normalise_route()` 兜底处理 404 等没有匹配到路由的请求。

追踪用 OpenTelemetry。`OTEL_ENABLED=false` 时所有 `span()` 退化为空操作，**追踪失败永远
不会让请求失败**。开启了 W3C `traceparent` 传播，并把当前 trace id 写进响应头
`X-Trace-ID`，用户把界面上的请求 id 贴进 Jaeger 就能直接定位。

日志支持 `LOG_FORMAT=json`，通过 `ContextVar` 注入 `request_id` 和 `trace_id`，一行一条，
可以直接进 Loki/ELK。

### 取舍

- 指标放在进程内注册表，多副本时不聚合：聚合是 Prometheus 的职责，应用不该假装自己是
  时序数据库。`/ops/summary` 明确标注「进程内」。
- `/metrics` 默认不鉴权（集群内抓取），但支持 `METRICS_TOKEN`：暴露到公网时必须设。
- 直方图分位数用桶插值估算，仅供界面展示；SLO 计算仍然走 `histogram_quantile`。

### 面试讲法

> 我先定义了「一次请求的可解释性」这个目标，再倒推需要哪些指标。检索链路有 7 个阶段，
> 所以每个阶段都有独立直方图，而不是只统计总耗时。标签基数是我特意处理的：用路由模板
> 而不是原始路径。追踪做成可选的，因为观测组件不应该成为可用性的单点。

---

## 2. 接入层：API Key、scope、审计

`backend/app/models/api_key.py` · `services/api_key_service.py` · `api/v1/api_keys.py` ·
`api/v1/public.py` · `core/ratelimit.py`

### 问题

作品集项目通常只有「登录后的网页」。真实系统里还有第二类调用者：脚本、MCP 客户端、其他
服务。它们不该拿用户的密码，也不该继承用户全部权限。

### 实现

- 密钥格式 `rk_<32 字节随机>`，**只存 SHA-256 哈希**，明文只在创建响应里出现一次；
  前缀单独存一列，供界面展示 `rk_ab12cd…`。
- 为什么不用 Argon2：用户密码需要慢哈希抵御离线爆破；API Key 本身有 256 bit 熵，没有
  可爆破空间，用慢哈希只会给每次调用增加几毫秒延迟。
- `Principal` 把 JWT 用户和 API Key 统一成「调用者 + scope」，`require_scope("chat")`
  在依赖层收口，新增接口不会意外继承过高权限。
- scope 四种：`kb:read`、`kb:write`、`chat`、`admin`；`admin` 隐含全部。
- `/public/*` 是**无状态**接口：不建会话、不写消息。检索无证据直接拒答，不调用模型，
  所以「拒答」是零成本的。
- 限流是 Redis 固定窗口 `INCR + EXPIRE`，按 key 计数；**限流器故障时放行**，因为限流
  不应该把服务一起拖垮，而且故障本身已经在 `/metrics` 里可见。
- 密钥的创建/吊销写入 `audit_logs`，包含 actor、action、resource、request_id、IP。
  审计行与业务操作同事务提交：要么都成功，要么都不留痕。

### 取舍

- API Key 不删除只吊销（`revoked_at`），否则审计记录会指向不存在的对象。
- `last_used_at` 每分钟最多写一次，避免热路径每次都 `UPDATE`。
- 所有失效原因返回同一个错误文案，避免攻击者区分「密钥错」和「已吊销」。

### 面试讲法

> 我把「人类调用」和「机器调用」分成两套接口：网页走 JWT + 会话，机器走 API Key +
> 无状态接口。这样机器调用不会意外产生会话垃圾数据，权限也能收敛到最小集合。

---

## 3. MCP Server：把知识库变成工具

`backend/app/mcp/` · `api/v1/mcp.py` · `docs/mcp.md`

### 问题

每个 AI 客户端都想接知识库，如果没有统一协议，就要为每个客户端写一遍适配层。

### 实现

MCP 本质是 JSON-RPC 2.0：`initialize` 协商版本与能力，`tools/list` 描述工具，
`tools/call` 执行。`app/mcp/protocol.py` 手写了这三个方法、错误码和结果结构，**没有引入
官方 SDK**：

- 离线环境可安装（本次开发环境无法访问 PyPI，正好验证了这一点）；
- 协议边界可以被单元测试覆盖（版本协商、通知不响应、未知方法、未认证调用）；
- 面试时可以完整讲清握手流程，而不是停在「调了个库」。

传输有两种，共用同一个 dispatcher：

- **stdio**：`python -m app.mcp.server`，启动时校验一次 `RAGSCOPE_API_KEY`，
  stdout 只输出 JSON-RPC，日志强制走 stderr（否则客户端会解析失败）。
- **HTTP**：`POST /api/v1/mcp`，每请求鉴权，支持批量消息与 202 通知语义。

工具都是**只读**的：列库、检索、带引用问答、文档大纲。上传/删除/改配置仍留在 Web 端，
因为写操作的错误对 AI 来说不可逆。

---

## 4. 多智能体：Planner / Researcher / Critic

`backend/app/rag/agent/graph.py`

### 问题

单 Agent 的 ReAct 循环让一个模型同时负责「拆解问题」和「判断证据够不够」，实测有两个
问题：复杂问题容易只查一半；模型不知道自己缺什么，于是重复检索。

### 实现

LangGraph 状态机：

```text
START → planner → researcher → critic ──(不足，带补充查询)──→ researcher
                                   └──(充足 / 次数用尽)──────→ END
```

- **planner** 把问题拆成 1–4 个自包含子问题，输出严格 JSON；解析失败或超时自动降级为
  原问题，不让规划失败阻塞回答。
- **researcher** 直接复用 `retrieve()`，不写第二套检索逻辑，所以指标、trace、缓存行为
  与单轮完全一致。证据按 `chunk_id` 去重，重复命中保留高分副本。
- **critic** 只看证据覆盖度，返回 `{sufficient, missing, followup_queries}`，最多 2 条
  补充查询，并过滤掉已经检索过的表述，防止原地打转。
- 循环次数上限 `DEEP_MAX_ITERATIONS=2`，图级 `recursion_limit` 只作兜底。

**关键设计：图只决定收什么证据，不写答案。** 最终回答仍由 grounded Prompt 基于去重后的
证据流式生成。这样三种模式（快速 / Agent / 深度）的引用编号、`citation_stats`、trace
格式完全一致，评测台才能同口径比较。

流式进展用 `astream(stream_mode="values")`：每个节点结束后拿到完整状态，SSE 层只要对比
步骤列表的增量即可，不需要为每个工具写回调。新增的 `plan` 事件让前端在检索开始前就能
展示拆解结果。

---

## 5. GraphRAG：实体图谱与一跳扩展

`backend/app/models/graph.py` · `services/graph_service.py` · `api/v1/graph.py`

### 问题

向量检索擅长语义相近，但「X1 的保修政策适用于哪些配件」这种跨文档问题，正确答案可能与
问题在字面上毫无重叠。知识图谱补的正是这种「实体—关系」链接。

### 实现

三张表：`graph_entities`（按 `(kb_id, 规范化名称)` 唯一）、`graph_relations`（按
`(source, target, relation)` 唯一）、`chunk_entities`（实体到 chunk 的提及关系）。

**构建是离线的、昂贵的**：每个 chunk 一次 LLM 调用，返回结构化 JSON，每块最多 10 个实体、
12 条关系。用 PostgreSQL `ON CONFLICT DO UPDATE` 做幂等 upsert：重复构建只会增加
`weight` 和 `mention_count`，不会产生重复节点。所以「构建中断后再点一次」是安全的修复
手段，而不是灾难。

**查询期是廉价的、无模型的**：

```text
问题 → candidate_terms()（jieba 分词 + 短 n-gram + 字母数字串）
     → 一条 IN 查询匹配实体
     → 一跳关系（最多 40 条边）
     → 邻居实体提及的 chunk（排除已在候选里的）
     → 追加到重排结果之后，带 graph_score / graph_via
```

三个细节值得讲：

1. **候选词生成不能按长度截断**。一开始我按长度倒序取 48 个，结果 `x1` 被一堆无意义
   长 n-gram 挤掉了，图谱永远匹配不到型号。现在分词结果和字母数字串优先，n-gram 只取
   2–4 字。
2. **图证据排在重排结果之后**，不参与重排排序：cross-encoder 直接判断过的 chunk 是
   一手证据，图邻居是推断证据，顺序上要能看出来。
3. **每条图证据都能解释**：`graph_via` 记录它来自哪个实体，检索 trace 里有独立的
   `graph` 阶段耗时和命中实体，评测台可以直接对比开关图扩展的 recall 差异。

---

## 6. 异步任务：Celery worker

`backend/app/workers/` · `docker-compose.prod.yml`

### 问题

`BackgroundTasks` 跑在 API 进程里：部署会杀掉正在入库的文档，embedding 还会和问答抢
CPU。上传一个 40 秒的 PDF，等于让整个 API 卡 40 秒。

### 实现

- `TASK_BACKEND=inline|celery` 一个开关切换，本地/CI 保持零依赖。
- `dispatch.schedule()` 是唯一调用点，上传、重试、创建评测运行都走它；broker 不可用时
  **自动降级**为进程内执行并记日志，用户的上传不会丢。
- Celery 配置：`acks_late=True` + `prefetch_multiplier=1`，worker 被杀时任务重新入队，
  而不是让文档卡在 `parsing` 等 15 分钟的 stale 回收。
- **异步连接池与事件循环绑定**是这里最容易踩的坑：`asyncio.run` 每个任务一个新 loop，
  全局 engine 的连接来自旧 loop 就会报 `attached to a different loop`。解决方式是在
  任务结束时显式 `engine.dispose()` 和 `close_redis()`，并在注释里写清原因。
- K8s 里不用长驻 beat，而用 CronJob 每 5 分钟跑一次 stale 回收：CronJob 天然抗重启、
  无需选主、Job 历史本身就是观测。

---

## 7. 交付：K8s、压测、质量门禁

`deploy/k8s/` · `deploy/locust/` · `.pre-commit-config.yaml` · `Makefile`

- **K8s**：Deployment + Service + Ingress + HPA + PDB，`initContainer` 跑迁移，
  `preStop` sleep 5 秒给 SSE 流收尾，`terminationGracePeriodSeconds=45` 对齐 nginx 的
  `proxy-read-timeout`。Ingress 显式关闭 `proxy-buffering`，否则流式回答会被缓冲成
  「一次性吐出来」。
- **已知限制写在清单里**：Chroma 是本地持久化索引，RWO 卷意味着 API 实际只能单副本；
  要真正水平扩展，需要换 server 模式向量库或从 Postgres 重建索引。这一条写在
  `api.yaml` 的注释里，而不是藏在文档深处。
- **压测**只打读路径（检索、鉴权、`/metrics`、工具列表），不打 `/chat`：压 LLM 路径
  测的是上游供应商，还会烧真钱。脚本注释里写明了限流上限，避免把 429 当成容量。
- **pre-commit** 只放快速、确定性的检查（ruff、YAML/JSON 校验、私钥检测、导入冒烟、
  前端类型检查）。慢的东西留在 CI，否则大家会开始用 `--no-verify`。

---

## 8. 用量与预算：让「花多少钱」可回答

`backend/app/services/usage_service.py` · `api/v1/usage.py` · `views/OpsView.vue`

### 问题

RAG 的边际成本几乎全在 token 上。没有用量视图时，团队只能等账单；没有预算闸门时，
一个脚本循环能把一个月的额度在半小时内烧完。

### 实现

- **聚合在读取时做**：`messages.token_usage` 已经是每条回答的事实来源，按天 / 按知识库
  做一次 `GROUP BY`（JSONB `->> 'total_tokens'` + cast）。不维护计数器表，因为计数器会
  在崩溃、手工删数据后漂移，而漂移的账单比慢 20ms 的查询危险得多。
- **预算按知识库、按 UTC 日**：`DAILY_TOKEN_BUDGET`（0 = 不限）。超限时流式接口返回 429
  `quota_exceeded`，JSON 接口降级为「只给拒答、不调模型」——同样的额度约束，不同的失败语义。
- **缓存优先于预算**：检查顺序是「语义缓存命中 → 免费返回」，未命中才校验预算。已经付过费的
  答案不该因为今天的额度用完而不可用。
- **观测**：`/api/v1/usage/summary?days=14` 返回日序列、Top 知识库与今日预算状态，运维页
  用进度条 + 迷你柱状图展示。

### 取舍

- 预算是软闸门：并发请求可能同时通过检查，最终略超。硬闸门需要预扣 + 结算，属于计费系统
  的范畴；这里的目标是「防止失控」，不是「精确计费」。
- 不按用户维度计费：当前权限模型下一个知识库只有一个 owner，按 KB 统计已经等价。

---

## 9. Webhook：把异步结果推给外部系统

`backend/app/services/webhook_service.py` · `api/v1/webhooks.py`

### 问题

同步和评测都是异步的。用户不可能一直盯着页面，外部系统（工单、IM 机器人、数据平台）也
需要知道「这批文档更新完了」。

### 实现

- 事件：`sync.completed` / `sync.failed` / `eval.completed` / `ingest.failed`。
- 订阅表 `webhook_endpoints`：URL、订阅事件、启用状态、最近一次投递结果。
- **签名**：`X-RAGScope-Signature: sha256=<HMAC-SHA256(secret, body)>`，密钥每个端点独立，
  接收方必须校验，否则任何人都能伪造「同步完成」。
- **投递**：Celery（或 asyncio 任务）异步发送，3 次重试（1s / 3s / 9s），4xx 不重试
  （URL 错了重试只会制造噪音），超时 10s。
- **可观测**：每次投递写回 `last_status` / `last_error` / `last_delivery_at`；
  `POST /webhooks/{id}/test` 可以立刻验证接收端。

### 已知边界

没有持久化 outbox：进程在投递前崩溃会丢一次通知。要做到「至少一次」需要一张
`webhook_deliveries` 表 + 独立 worker 扫表，这是下一步而不是隐藏的现状。

## 10. CRAG：证据分级与纠正式二次召回

`backend/app/rag/crag.py` · 接入点 `rag/pipeline.py` 第 7.5 阶段

### 问题

常规 RAG 无条件信任 top-k：召回了什么就用什么生成。于是「看起来相关但其实答不了」的
邻居和真正命中答案的段落，会以同样的自信度进入 prompt。更麻烦的是主召回链路上有两处
为了拒答而设的闸门——稠密分数下限、以及「没有向量命中就不启用 BM25」——它们在挡住无关
问题的同时，也会把措辞不同的正确 chunk 一起挡掉。

### 实现

在重排之后、生成之前插一个判断点，输出 `correct` / `ambiguous` / `incorrect`：

- **启发式分级（默认）**：读链路已经算出来的分数（重排优先，其次稠密余弦）。
  `top` 决定有没有找到主题，`mean(top3)` 决定是真实证据簇还是一条幸运邻居——
  只有 `[0.91, 0.80, 0.72]` 算 correct，`[0.62, 0.05, 0.04]` 只能算 ambiguous。
  零成本、永不失败，所以是默认值也是兜底。
- **LLM 分级（可选）**：分数回答不了的问题是「这些段落里到底有没有答案」，
  尤其是比较类和多跳问题——每个 chunk 都沾边，但没有一个够用。只有模型能判断这个。
- **纠正动作**：判定为 weak 时生成 2 条二次召回查询（LLM 改写优先，失败用 jieba
  TF-IDF 关键词），把稠密下限放宽到 `min_vector_score × relax_factor`，并且
  **强制启用 BM25**——正是主链路那道闸门造成了这次「无证据」。

### 取舍

- 二次召回的候选**追加在重排序列尾部**，不参与重排。它们没有被交叉编码器判定过，
  放在尾部才能让「置信度更低」这件事在下游可见，而不是伪装成 rerank 的第一名。
- CRAG 默认关闭：每个 ambiguous 问题都要多一轮 embedding + BM25，对延迟敏感的场景
  不划算。按知识库开（配置里 `use_crag`）。
- 分级器失败一律 fail-open 到 `correct`，宁可用现有证据回答，也不丢证据。
- LLM 分级判定 incorrect、但分数很强时降级为 ambiguous——不让模型的一次误判否决
  已经很强的检索信号。

### 面试讲法

「CRAG 不是加一个组件，是给检索链路加了一个判断点。真正有价值的不是二次召回，
而是**它让我必须回答一个问题：什么叫证据够？** 我把这个定义写成了 top 与 mean(top3)
两个条件，并且把它记进了 trace——所以它是可复盘的，不是一句『模型觉得不够』。」

## 11. 上下文压缩：只把承载证据的句子放进 prompt

`backend/app/rag/compress.py` · 接入点 `rag/pipeline.py` 第 9 阶段

### 问题

一个 chunk 被召回是因为它的一部分匹配。剩下的部分——样板文字、相邻小节、一长串列表里
只有一项相关——被付了两次代价：占 prompt 预算，并且稀释了这个 chunk 在模型眼里的主题。

### 实现

- **lexical（默认）**：句子与查询有词项重叠就保留。中文用 jieba 分词
  （BM25 已经在用，不新增依赖），并叠加字符二元组——分词器在「退款」/「退款申请」上
  的切分歧义是这里最常见的失败模式。
- **llm（可选）**：让模型逐行输出它要引用的原句，**只保留与原文逐字一致的行**。
  模型造出来的句子直接丢弃；如果过滤后为空就恢复原文。压缩只能缩短文本，不能改写。
- 放在链路最后：作用于真正进 prompt 的那份列表（含图谱与 CRAG 补充），
  所以省下的 token 是真实的，不是对子集测出来的。

### 取舍

试过「按最高句分数的一定比例保留句子」并且放弃了：长度归一化之后，切分点对 chunk
长度敏感——同样的证据在短 chunk 里保留、在长 chunk 里被丢掉。二值重叠没那么优雅，
但在任何 chunk 上行为一致，这对调试的人来说更重要。

### 面试讲法

「LLM 抽取那一步的设计重点是防改写：模型只能复制原文里存在的句子，复制不出来的就丢。
因为 RAG 里任何被模型改写过的『原文』都会破坏引用可校验性——它就不再是证据了。」

## 12. 检索链路详情页：可观测要能回看

`backend/app/api/v1/traces.py` · `frontend/src/views/TracesView.vue`

### 问题

聊天页能看到刚刚那次检索的链路，但**历史回看不了**——`graph` / `crag` / `compress` /
`counts` 四个阶段载荷只存在于内存里的 trace，落库时被丢掉了。可观测性如果只能看
「刚刚那一次」，就没法复盘一个昨天发生的慢查询或误拒答。

### 实现

- 迁移 `a2c7e9f3b145`：`retrieval_traces` 增加 `stage_payload` JSONB 单字段，
  而不是四列。这些载荷会随着检索阶段增加不断加键，单 blob 让「加一个阶段」不再等于
  「再加一次迁移」——和 `config` / `candidates` 已经做过的是同一个取舍。
- `GET /kbs/{id}/traces` 列表把 `total_ms` / `final_count` 反规范化输出，
  表格排序和着色不用加载候选 blob。
- `GET /traces/{trace_id}` 详情**按知识库校验归属**，而不是按 trace id：
  单凭 trace id 不该泄露别人的记录是否存在。
- 前端：阶段耗时瀑布条（按最慢阶段归一，0.1 ms 也是可见的一条而不是零宽度）、
  CRAG 判定卡片、压缩收益卡片、候选分阶段切换（向量 / BM25 / 融合 / 最终 / CRAG / 图谱）。

## 13. 文档格式：PPTX / XLSX / CSV / HTML 用标准库解析

`backend/app/rag/parsers.py`

### 问题

企业知识库里最多的不是 Markdown：是课件、是报价表、是导出的 HTML。原来只支持
md / txt / pdf / docx。

### 实现

PPTX 和 XLSX 本质是 OOXML（zip + XML），而入库只需要可见文本。所以用标准库
`zipfile` + `ElementTree` 解析，**不引入 python-pptx / openpyxl**：部署环境 PyPI 不可达时，
一个装不上的解析器等于不存在的功能；而且只读文本场景不需要完整对象模型。

- **PPTX**：按 `presentation.xml` 的 `sldIdLst` + 关系表解析真实页序（文件名序不等于
  页序）；递归遍历 shape 以覆盖组合形状；表格按行管道分隔；备注并入正文并剔除页码
  占位符；一页一个 `page_start`，引用能定位到幻灯片。
- **XLSX**：工作表顺序取自 `workbook.xml` + 关系表，正确处理共享字符串；单元格按列引用
  展开、跳过的列补空位，保持「一行一条」的行内关联；工作表名为 `##` 标题，空表跳过。
- **CSV / TSV**：渲染成 Markdown 表格，表头保留列名语义——直接倒 `a,b,c` 会让模型
  根本不知道第二列是什么。
- **HTML**：复用爬虫的正文抽取器，而不是写第二套会漂移的实现。

### 踩坑

`r:id` 属性用的是 **officeDocument** 关系命名空间，不是 `.rels` 文件里的 **package**
命名空间。写错不会报错，只会静默返回「找不到幻灯片 / 工作表」。

## 14. 答案事实性守护：把「有没有引用」升级成「每条断言能不能对上」

`backend/app/rag/faithfulness.py` · 接入点 `services/chat_service.py::_save_assistant_message`

### 问题

「引用合规率 100%」听起来很安全，但它只验证了**格式**：答案里有 `[n]` 且编号没越界。
它验证不了**内容**——模型完全可以在一段正确引用旁边多写一句上下文里根本没有的话。
检索层挡不住这种事（见第 15 节：4 条语料外问题检索层一条都没拒答），
所以必须在生成之后再加一道。

### 实现

生成完成后，让 LLM 把回答拆成原子声明，逐条判定是否被**本次检索到的上下文**直接支持：

- `supported=true` 仅当该声明能在上下文里找到明确依据；上下文没提、与上下文矛盾、
  或明显来自模型自身知识的，都算 `false`。
- 输出 `grounded_ratio`（支持数 / 总数）与逐条依据，落库到 `messages.faithfulness`。

### 取舍

**它是 guard 不是 gate**：不改已经生成的答案，只产出一份报告。默认不拦截。

**任何失败一律 fail-open**（无密钥 / 超时 / 输出不可解析 → `source="fallback"` 且
`grounded=True`）。理由是：一个可能坏掉的裁判，如果它坏了会把好答案标记为不接地，
那它比没有裁判更糟——你会开始不信任自己的系统。所以**阈值只在裁判本身成功时才生效**。

**踩坑**：候选正文的键是 `content` 不是 `text`。写错不报错，只是每个答案都返回
`empty_context`——守护安静地通过了，却从没真正检查过任何东西。这类 bug 特别坏，
因为它让健康检查变绿。已加回归测试钉住键名。

## 15. 真实语料评测：玩具语料上的 100% 说明不了任何事

`backend/scripts/make_eval_dataset.py` · 80 条手工标注 · 184 篇真实语料

### 问题

之前那组数字跑在 15 个 chunk 的玩具语料上，Recall@5 = 100%。这个数字唯一的信息是
「语料太小」。于是换成四个真实语料包（Vue 3 / Vite / Vue Router / Pinia 官方中文文档，
184 篇 / 80 万字符），手工标注 80 条用例（70 条可答 + 10 条不可答）。

### gold 标签不能让被测系统自己生成

如果让检索器挑自己的标准答案，每个 recall 数字都会变成同义反复——它当然能召回它刚刚
召回的东西。所以标签是读原文挑出来的：先按 URL 锁定目标文档页，再通读该页 chunk。

两个刻意做难的设计：

- **比较类问题的答案通常横跨两块 chunk**（"介绍" + "局限性/vs"）。只标一块的话
  recall@1 就成了天花板，问题等于被人为简化。
- **gold 允许跨多篇文档**。命名路由 vs 命名视图、重定向 vs 别名、Pinia 的
  State / Getter / Action，这些页面名字和语义都很像，正是能把语义检索和关键词匹配
  区分开的用例。只标单篇文档测不出这个差别。

为此脚本的 `_resolve()` 支持两种定位方式：URL 子串（必须唯一）和完整 URL 精确匹配。
后者是必须的——Pinia 的 `/zh/core-concepts` 是 `/zh/core-concepts/state.html` 的前缀，
任何子串都不唯一。定位不唯一就 `SystemExit`，拒绝猜。

### 结果（80 条，0 报错）

| 配置 | Recall@1 | Recall@5 | Recall@10 | MRR | Hit@5 | P50 |
|---|---:|---:|---:|---:|---:|---:|
| 基线（final 5，阈值 0.45） | 19.8% | 50.0% | 50.0% | 0.468 | 68.6% | 212 ms |
| 放宽（top 30 → final 10，阈值 0.35） | 21.9% | 53.6% | 62.4% | 0.510 | 72.9% | 252 ms |

**Recall@10 等于 Recall@5，一分没涨（50.0% / 50.0%）**——天花板不是检索器，是 `final_top_k=5`。
这类问题靠盯模型永远看不出来，只有看到 Recall@k 曲线是平的才会发现。
26 条样本上看到过一次，扩到 80 条后**仍然是平的**，所以它不是小样本噪声。

代价要一起说：Recall@10 +12.4 个点换 P50 +40 ms（+19%）。这轮不算贵，因为只动了检索
参数；多查询要额外一轮 embedding，实测 P50 从 404 ms 涨到 1172 ms（2.9 倍），
那才是真正挑延迟预算的选项。

### 从 26 条扩到 80 条，Recall@5 反而从 52.3% 掉到 50.0%

第一反应是怀疑回归。按「是不是原来那 22 条」拆开就清楚了：

| 子集 | 条数 | 平均 gold 块数 | Recall@5 | MRR | Hit@5 |
|---|---:|---:|---:|---:|---:|
| 原来的 22 条 | 22 | 1.73 | **52.3%** | **0.417** | 63.6% |
| 新增的 48 条 | 48 | 1.96 | 49.0% | 0.491 | 70.8% |

**原来那 22 条在新一轮里 Recall@5 = 52.3%、MRR = 0.417，和上一轮一字不差。** 系统没退化，
而且这套评测是可重复的。掉的 3 个点全部来自新增用例，原因是平均 gold 块数更高
（1.96 vs 1.73）：标 2 块就要两块都进 top-k 才满分。

新增用例的 Hit@5 反而更高（70.8% vs 63.6%）——「至少命中一块」变容易、「全部命中」变难，
这正是 recall 和 hit 必须分开看的原因。

一个诚实的修正：我原本以为下滑主要来自「跨文档用例变多」，拆开后发现跨多文档的用例
**只有 2 条**，撑不起这个解释。**headline 数字会随标注构成移动**，所以
`scripts/eval_breakdown.py` 把这个拆分做成了常规动作，而不是拍脑袋给因果。

### 一个反直觉的发现：拒答是生成层在守，不是检索层

10 条语料外问题**全部召回了候选**，检索层拒答准确率 0 / 10（26 条那轮同样是 0 / 4）。
其中 6 条是「看起来该会」的前端邻域问题（React 的 `useState`/`useReducer`、Nginx 反向
代理缓存、TypeScript 装饰器、Svelte runes、Webpack 模块联邦、Element Plus 虚拟滚动），
它们比差旅报销这种明显跨域的问题更能诱出幻觉。小 embedding 模型对完全无关的两段中文
也会给出不低的相似度。

而生成层的拒答准确率是满分。所以真正挡住幻觉的是 grounded 提示，不是
`min_vector_score`。这个结论是测出来的，也正好解释了为什么还需要第 14 节那层裁判——
拒答这道门既然是模型在守，就需要一个能验证「模型这次守对了没」的机制。

### 误拒答的 5 条，全部是检索先输了

生成侧误拒答率 7.1%（70 条可答里 5 条）。把生成指标和检索指标按用例 join 起来看：
这 5 条的 **recall@5 和 hit@5 全是 0**（其中 4 条是 Pinia 的冷门页）——检索一条相关内容都没给到，模型于是说
「资料里没有」。**这在给定上下文下是正确行为**，而且答得不错：不是干巴巴拒答，
而是说明「资料里只有 X，没有你要的 Y」。

归因错了会往反方向修：如果只盯生成指标，会去调 prompt、放宽拒答条件，
而真正要修的是这 4 条为什么没被召回。顺带排除了一个可能原因：DB 有 2986 个 chunk，
Chroma 里也是 2986 个，不是索引缺失。

### 评测抓出了一个标注错误：错的是我，不是模型也不是裁判

「Angular 的信号 signal 怎么用？」原本标成语料外不可答。系统答了，
裁判给了 `faithfulness_ratio = 1.0`——看起来是「模型幻觉 + 裁判漏判」。
翻回原文发现**错的是标签**：Vue 的「深入响应式系统」里真有一节
「与信号 (signal) 的联系 / API 设计权衡 / Angular 信号」，原文就写着
`const count = signal(0)` / `count()` / `.set(1)` / `.update(...)`。
系统答对了，裁判也判对了。这条已改成可答案例。

这正是不可答集要刻意设计成「看起来该会」的原因：只放「差旅报销」这种明显跨域的问题
测不出东西，放这一类才会逼出「到底是不是语料外」的判断题。

### 最该修的是检索，不是生成

70 条可答案例里 22 条（31%）**recall@5 = 0**，一条相关内容都没进 top-5；
生成侧的 5 条误拒答全部来自这 22 条。所以这份评测给出的行动项是
**先修召回，别再动 prompt**。

## 16. 没有 Docker 也要能把项目跑起来

`scripts/dev_stack.sh` · `make dev-db-native`

原来的 `make dev-db` 只有一行 `docker compose up -d db redis`。机器上没有 Docker 时，
这条命令等于：迁移跑不了、集成测试跑不了、「检索到底能不能用」这个问题永远没有答案。
这个项目的集成测试在很长一段时间里从未被执行过，根因就在这里。

脚本用本机已有的 PG / Redis 二进制启动（端口 5433 / 6380，正好是 `.env.example`
的默认值，不会干扰 5432 上可能存在的 Docker 实例），自动 `initdb`、建 `ragscope`
角色与库。

一个 Windows 特有的坑：PostgreSQL 在 Windows 上创建不了 unix socket 锁文件，
会直接拒绝启动。所以脚本往 `postgresql.conf` 写入 `unix_socket_directories = ''`
（只留 TCP）。

配套的兼容性修复：**refresh token 轮换原本用 `GETDEL`，该命令只在 Redis ≥ 6.2 存在**，
对着 Redis 5 跑时每次刷新都是 500。改成用 `EVAL` 执行 GET+DEL 的 Lua 脚本——
既保留了「读后即删」的原子性（并发刷新不能读到同一个 jti 两次，这正是这个命令的意义），
又在任何支持 EVAL 的 Redis（2.6+）上可用。
