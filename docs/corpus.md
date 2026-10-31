# 语料库：把爬到的知识变成可复用的资产

数据源解决「知识库要保鲜」，语料库解决「知识从哪来、能不能复现」。两者是流水线的两端：

```text
crawl_corpus.py  →  data/corpus/<slug>/  →  /corpus 页面  →  导入知识库
   （抓取）            （快照 + 清单）         （审阅/选择）      （切块/embedding）
```

代码：`backend/scripts/crawl_corpus.py`、`app/services/corpus_service.py`、`api/v1/corpus.py`、
`frontend/src/views/CorpusView.vue`。

---

## 1. 语料包长什么样

```text
data/corpus/vue3-zh/
├── index.json                 # 名称、来源、许可、每篇的 URL/标题/字数/sha256/抓取时间
├── LICENSE-NOTICE.md          # 来源与许可声明（跟着数据一起走）
├── guide__essentials__reactivity-fundamentals-3f2a1c.md
└── … 54 篇
```

单篇格式：

```markdown
> 来源：https://cn.vuejs.org/guide/essentials/reactivity-fundamentals

# 响应式基础

...
```

把来源写进正文不是装饰：切块之后，每个 chunk 仍然能回答「这段话出自哪一页」，
引用抽屉和评测标注都靠它。

---

## 2. 为什么不让连接器直接入库

| | 数据源连接器 | 语料包 |
|---|---|---|
| 生命周期 | 长期、按计划增量 | 一次性快照 |
| 存哪 | 数据库 + 向量库 | 磁盘目录 + `index.json` |
| 适用 | 知识库保鲜 | 复现实验、批量 bootstrap、离线分发 |
| 审阅 | 入库后看文档列表 | **入库前**逐篇预览 |

第三条是关键：50 篇文档进索引之前，人可以花两分钟看看抓到了什么。企业里「先审后入」
往往比「自动入库再回滚」便宜得多。

---

## 3. 抓取质量是怎么保证的

`app/services/sources/html_extract.py` 是一个约 250 行的 readability-lite：

- **按语义标签丢弃**：`script/style/nav/footer/aside/form/iframe/svg`；
- **按属性丢弃**：`hidden`、`aria-hidden="true"`，以及 class/id 里出现
  `nav|sidebar|menu|footer|sponsor|breadcrumb|outline|toc|banner|api-style|…` 的容器。
  现代文档站很少用 `<nav>`，基本都是 `<div class="VPNav">`，只靠标签会漏掉大量噪声；
- **保留结构**：标题 → Markdown 标题（chunker 用它生成 `heading_path`），列表 → `- item`，
  代码块 → 三反引号，表格 → 管道分隔；
- **清洗**：零宽字符（锚点占位符）、行尾空白、连续空行、追踪参数（`utm_*` / `spclid`）。

这些规则都有单测（`tests/unit/test_sources.py`），改动提取器先跑测试，避免「清理噪声」时
把正文一起清掉。

---

## 4. 断点续爬与礼貌抓取

```bash
cd backend
PYTHONPATH=. .venv/Scripts/python.exe scripts/crawl_corpus.py \
  --name "Vue 3 官方文档（中文）" --slug vue3-zh \
  --sitemap https://cn.vuejs.org/sitemap.xml --include guide/ \
  --max-pages 50 --out ../data/corpus/vue3-zh \
  --license "MIT · Vue.js 官方文档，来源 https://cn.vuejs.org/"
```

- **断点续爬**：每抓完一篇立即写盘，`index.json` 原子重写；中断后重跑会跳过已完成的 URL。
- **`--force`**：忽略 index 全量重抓，文件名由 URL 决定，覆盖即幂等（用于提取器升级后重建）。
- **礼貌**：robots.txt + `Crawl-delay`、全局请求间隔（默认 0.6s，多 worker 共享一个限速器）、
  `max-pages` / `max-depth` 上限。
- **可归属**：每篇记录来源 URL、抓取时间、sha256；`LICENSE-NOTICE.md` 写明上游许可。

> Git Bash 会把 `--include /guide/` 当成路径转换，导致过滤为空。用 `--include guide/`
> （不带前导斜杠），或设置 `MSYS_NO_PATHCONV=1`。这个坑已经踩过，写在这里省下一次。

---

## 5. 导入语义

- **快照语义**：同一知识库里，来源 URL 已存在的文档直接跳过。新鲜度交给连接器，
  这里只负责 bootstrap。
- **逐篇入库**：复用正常入库流水线（解析 → 切块 → embedding → 索引），所以失败只影响单篇，
  `added/skipped/failed` 会计数。
- **进度可见**：进度写在 Redis（`corpus:import:{kb}:{slug}`），页面每 1.5s 轮询，
  不额外建任务表。
- **可重复导入**：重复点导入不会产生重复文档，只会得到 `skipped=N`。

---

## 6. 仓库里现在有什么

`data/corpus/vue3-zh/`：Vue 3 官方中文文档的 **54 篇**指南页，约 **31.8 万字符**，
抓取自 `cn.vuejs.org`（MIT 许可，见包内 `LICENSE-NOTICE.md`）。

它能支撑的真实问题包括「响应式原理是什么」「ref 和 reactive 有什么区别」「组合式 API 与
选项式 API 如何取舍」「Teleport 解决什么问题」——用来演示检索、引用、多跳 Agent 和评测，
比虚构语料更有说服力。

---

## 7. 合规提醒

- 只抓取你有权使用的内容，并遵守目标站点服务条款与 robots.txt；
- 语料包里的 `LICENSE-NOTICE.md` 必须随数据保留，再分发前确认上游许可；
- 演示/评测用途与商业再分发是两件事，后者需要逐一确认授权。
