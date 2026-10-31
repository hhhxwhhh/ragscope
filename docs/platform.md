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
