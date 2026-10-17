# 架构说明

这份文档按请求链路说明 RAGScope 的分层、数据一致性策略和关键取舍。目标读者是第一次读这个仓库的人，以及准备面试时想快速回忆"为什么这么写"的作者本人。

## 总览

```text
Browser (Vue 3 + Element Plus)
   │  axios/fetch, Bearer access token, HttpOnly refresh cookie
   ▼
Nginx (生产) ── /api/ ──> FastAPI (uvicorn)
                              ├── PostgreSQL 16  (业务数据 / 评测 / trace)
                              ├── Redis 7        (refresh token、登录限流)
                              ├── Chroma         (每 KB 一个 collection)
                              └── 本地进程内任务   (文档入库、评测运行)
                                   ├── BGE embedding
                                   ├── BM25 + jieba
                                   ├── bge-reranker
                                   └── DeepSeek API (流式生成)
```

后端只有一层进程：API、后台任务、模型推理都在同一个容器里。这是作品集项目的刻意选择，不是没来得及拆。代价和扩展路径写在最后一节。

## 目录分层

| 目录 | 职责 | 不做什么 |
|---|---|---|
| `app/api/v1` | HTTP 参数、状态码、依赖注入 | 不写业务规则 |
| `app/services` | 事务边界、业务规则、ownership 校验 | 不碰请求对象 |
| `app/rag` | 切块、embedding、BM25、融合、重排、Prompt | 不直接写数据库 |
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

入库过程中 `index_version` 只在完全成功后递增。BM25 缓存和 Chroma metadata 都以它作为键，所以新索引不会读到旧缓存。

### 3. 问答（SSE）

```text
POST /kbs/{id}/chat/stream
  ├─ 落用户消息（短 session，立即提交）
  ├─ 检索: rewrite -> embed -> vector + BM25 -> RRF -> rerank
  ├─ 先发 meta / retrieval 事件，再发 token
  ├─ 流式期间不持有 session
  └─ 结束或断连: 落 assistant 消息 + citations + retrieval_trace，
                  断连标记 cancelled 并保留部分内容
```

SSE 用 POST + `fetch` 而不是 `EventSource`，因为需要请求体和 `Authorization` 头。前端自定义按 `\n\n` 分帧，并用 `TextDecoder({stream: true})` 处理跨 chunk 的中文多字节字符。

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

### 5. 启动与健康检查

- `lifespan`：恢复 stale 文档和 stale 评测运行；启动阶段不加载模型。
- `/api/v1/healthz`：进程存活。
- `/api/v1/readyz`：PostgreSQL、Redis、Chroma 三项都可用才返回 200，任一失败返回 503。
- 请求中间件：注入 `X-Request-ID` 和 `X-Process-Time-Ms`，记录方法、路径、状态码、耗时；健康检查不刷日志。

## 数据模型

| 表 | 关键点 |
|---|---|
| `users` | email 唯一、小写归一；argon2 哈希 |
| `knowledge_bases` | `owner_id`；`index_version` 驱动缓存失效；`retrieval_config` JSONB |
| `documents` | `(kb_id, sha256)` 唯一；状态机 + `error_stage` |
| `chunks` | `(document_id, chunk_index)` 唯一；`heading_path`、字符区间 |
| `conversations` / `messages` | 消息状态 `completed/cancelled/failed`，含延迟与 token 用量 |
| `citations` | 引用排名、分数、片段；chunk 删除后置空 |
| `retrieval_traces` | 改写 query、四阶段候选、分阶段耗时、配置快照 |
| `eval_datasets` / `eval_cases` | 数据集快照切块参数；用例标注到 chunk 级 |
| `eval_runs` / `eval_results` | 运行快照配置与索引版本；结果快照问题与标注 |

JSONB 只用于配置、候选、指标、标注这类"整体读写"的数据，不参与高频过滤和 join。

## 关键取舍

### 越权返回 404

所有子资源都先 join 父知识库校验 `owner_id`。不存在和无权限返回同一个 404，避免通过 403/404 的差异探测资源是否存在。

### 后台任务用 BackgroundTasks

当前只有两类后台任务（入库、评测），都不需要跨进程调度。用 BackgroundTasks 省掉了 broker、worker、结果后端和一整套部署。代价：任务跟着 API 进程生命周期走，进程重启会中断；所以启动时要把 stale 状态改成 failed，并由前端提供重试。

扩展路径：任务写 `jobs` 表 -> Celery/RQ worker 消费 -> 任务状态仍存 PostgreSQL。现有状态机和轮询接口不需要改。

### 嵌入式 Chroma

每个知识库一个 collection，chunk id 直接用 PostgreSQL UUID，删除和 upsert 天然幂等。Chroma 是同步库，所有调用都包在 `anyio.to_thread` 里，避免阻塞事件循环。单机够用；要多副本时换成独立向量服务，并在 metadata 中保留 `document_id/chunk_index` 的映射。

### 检索配置快照

`EvalRun.config` 存 `asdict(RetrievalConfig)`，而不是只存用户改过的字段。这样即使默认值以后变了，历史运行仍然可以复现。对比两个 run 时要求 `index_version` 相同，否则 chunk 变了，排名没有可比性。

### 长任务不在事务里做慢操作

embedding 在写事务外完成；SSE 流式期间不持有 session；评测执行器每条用例单独提交。目标是缩短事务、避免长连接，并让取消/崩溃最多损失一条用例。

## 失败与恢复

| 失败 | 表现 | 恢复 |
|---|---|---|
| embedding / 解析失败 | 文档 `failed`，记录阶段和错误 | 前端重试，重试前清理旧 chunk/vector |
| 进程在入库中重启 | 文档卡在 parsing/indexing | 启动时超过 15 分钟标记 failed |
| 进程在评测中重启 | run 卡在 pending/running | 启动时标记 failed，可重新发起 |
| reranker 超时/异常 | 回退 RRF 顺序，trace 标记降级 | 不失败，只损失排序质量 |
| Redis 不可用 | 登录/刷新失败 | 恢复 Redis；不降级放行 |
| Chroma 不可用 | readyz 503，检索报错 | 恢复后重试；/readyz 可被编排系统使用 |

## 扩展方向

1. **多副本 API**：BackgroundTasks 换成外部队列；Chroma 换成独立服务；SSE 通过负载均衡的 sticky session 或独立网关。
2. **更大语料**：BM25 全量进内存不现实，换 OpenSearch/Elasticsearch 或按 KB 分片；embedding 批处理与增量索引。
3. **生成侧评测**：citation validity、人工抽检、A/B 回答对比；仍然不建议直接用 LLM judge 当唯一标准。
4. **多租户与权限**：团队、角色、审计日志；所有查询增加 tenant 维度。
5. **可观测性**：结构化日志 + OpenTelemetry trace，把检索阶段耗时接到 APM。
