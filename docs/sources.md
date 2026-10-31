# 数据源与定期同步：让知识库自己长大

上传只能覆盖「我手里有文件」的场景。真实的知识库通常跟着外部世界变化：产品文档在 docs
站点更新、团队 handbook 在 Git 仓库里、公告在 RSS 里。这个子系统把「抓取 → 归一化 →
去重 → 增量入库」做成可调度的连接器。

代码：`backend/app/services/sources/`、`source_service.py`、`api/v1/sources.py`。

---

## 1. 四种连接器

| 类型 | 发现方式 | 关键配置 | 适用场景 |
|---|---|---|---|
| `web` | 从起始 URL 做有界 BFS | `start_urls`、`max_pages`、`max_depth`、`allowed_domains`、`include_patterns` / `exclude_patterns` | 文档站、FAQ、公告页 |
| `sitemap` | 读 `sitemap.xml`（支持一层 sitemap index） | `sitemap_url`、`max_pages`、`include_patterns` | 结构化文档站，比 BFS 更可控 |
| `rss` | 解析 feed，直接用条目正文 | `feed_url`、`max_items` | 博客、公告、更新日志 |
| `github` | REST contents API 递归目录 | `repo`、`path`、`ref`、`glob`、`max_files` | 仓库里的 `docs/`、handbook |

所有连接器的输出都是同一种结构：

```python
SourceItem(external_id=url, title=..., content=<markdown>, metadata={...})
```

`external_id` 是文档身份，`content` 是归一化后的 Markdown。连接器不碰数据库、不碰向量库、
不调用 LLM —— 这是它们可单元测试的原因，也是增量逻辑只存在一处的原因。

---

## 2. 增量同步：三行核心逻辑

```text
digest = sha256(normalized_content)
existing = documents where (kb_id, external_url) == (kb, item.external_id)
  没有       -> create  : 落盘 + 建 Document + 走原入库流水线
  digest 相同 -> unchanged: 直接跳过（不 embedding、不花 token）
  digest 不同 -> update  : 覆盖文件 + 删旧 chunk、bump index_version + 重新入库
```

几个实现细节：

- **身份用 URL，不用标题**。标题会改，URL 一般不会；同一篇文档换标题不应该产生重复条目。
- **去重粒度是知识库**。`(kb_id, sha256)` 上的唯一约束意味着同一段内容在同一 KB 里只会
  存在一份；如果两个 URL 内容完全相同，第二个会被计为 `unchanged` 而不是报错。
- **更新会 bump `index_version`**。BM25 缓存与检索缓存的指纹都含这个字段，所以改动后不会
  读到旧索引。
- **截断保护**。单篇超过 `SYNC_MAX_DOCUMENT_CHARS`（默认 20 万字符）会截断并注明，避免一个
  巨型页面把 embedding 拖死。

每次同步落一条 `sync_runs` 记录：

```json
{"added": 3, "updated": 1, "unchanged": 12, "failed": 0, "skipped": 0,
 "pages_fetched": 16, "duration_ms": 8421,
 "errors": [{"url": "...", "error": "HTTP 404"}]}
```

状态语义：`success`（全部成功）/ `partial`（有页面失败但整体跑完）/ `failed`（配置或网络
导致整体失败）。前端展示的是这三个词，而不是一个模糊的“完成”。

---

## 3. 调度

两种后端，行为一致：

| 部署 | 谁触发 | 说明 |
|---|---|---|
| `TASK_BACKEND=inline` | API 进程内的 asyncio 循环（`services/scheduler.py`） | 本地/单容器即可用，不用起 beat |
| `TASK_BACKEND=celery` | Celery beat 每 60s 查一次到期数据源 | 生产推荐；`beat` 在 compose 与 K8s CronJob 里都有 |

无论哪种，`sync_due_sources()` 都用 Redis `SET NX`（60s）做 tick 锁：多个 API 副本同时
跑循环也只有一个会真正派发任务。

`next_run_at = last_run_at + schedule_minutes`。失败也会推进下一次，避免一个坏数据源每
分钟重试一次。

---

## 4. 抓取礼貌与合规

这部分不是可选项：

- **robots.txt**：每个 host 抓取前检查，遵守 `Crawl-delay`；不可达时按“无规则”处理，但会
  在 run 的 errors 里留痕。
- **限速**：默认 0.8s/请求（sitemap 0.5s），配置项可调，上限 10s。
- **边界**：`max_pages ≤ 200`、`max_depth ≤ 3`，单次同步最多 `SYNC_MAX_ITEMS_PER_RUN`
  条（默认 50）。
- **只抓正文**：不下载二进制、不执行 JS、不跟随外域链接。
- **合规责任在配置者**：只应抓取你有权使用的内容，并遵守目标站点的服务条款。这个模块提供
  的是「可控」的抓取，不是「可以随便抓」。

---

## 5. 新增一个连接器的步骤

1. 在 `services/sources/` 新建模块，继承 `Fetcher`，实现 `async fetch(config) -> FetchOutcome`；
2. 在 `schemas/source.py` 加一个 `XxxConfig`（Pydantic 校验）并注册进 `CONFIG_MODELS`；
3. 在 `services/sources/__init__.py` 的 `FETCHERS` 里注册；
4. 前端 `SourcesPanel` 的表单补一个类型分支（可选，默认走 JSON 编辑）。

不需要改 `source_service`、调度器或数据库 —— 增量与调度是与连接器无关的。

---

## 6. 运维视角

- `GET /api/v1/sources/{id}/runs` 看历史；`stats.errors` 是排查“为什么没抓到”的第一现场。
- `/api/v1/ops/summary` 的 `graphrag` / `ingest` 计数只能看总量，数据源维度看 run 记录。
- 数据源删除**不会**删除已入库文档（`documents.source_id` 是 `ON DELETE SET NULL`）：
  连接器是管道，不是内容的所有者。
- 同步任务与入库共用 Celery 队列；单篇文档失败只影响该条目，`failed` 计数会体现。

---

## 7. 试一下

```bash
# 建一个 3 页、1 跳、1.2s 间隔的演示数据源，并立即同步一次
cd backend
PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_sources.py \
  --kb "星河科技" --type web --url https://fastapi.tiangolo.com/tutorial/ --sync

# 也可以建 GitHub 文档源（私有仓库需要 GITHUB_TOKEN）
PYTHONPATH=. .venv/Scripts/python.exe scripts/seed_sources.py \
  --kb "星河科技" --type github --repo tiangolo/fastapi --path docs/en/docs/tutorial --limit 5
```

然后在知识库详情页的「数据源」标签里点「立即同步」，看 run 从 `running` 变成 `success`，
文档列表里出现来源为网页的条目。
