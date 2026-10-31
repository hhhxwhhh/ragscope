"""Bulk crawl a documentation site into a local, reusable corpus pack.

Different from the in-app connectors on purpose:

* **Connectors** keep one knowledge base fresh (incremental, DB-backed, scheduled).
* **This script** builds a *corpus pack* on disk — a folder of Markdown files plus
  an ``index.json`` — which can be reviewed, committed, and imported into any
  number of knowledge bases later.

That separation matters for reproducibility: the demo corpus is a file in git, so
"the knowledge base I evaluated yesterday" can be rebuilt exactly, without
depending on a live site.

Features that a one-shot scraper usually lacks and a real pipeline needs:

* resumable — every document is written as soon as it is fetched, and
  ``index.json`` is rewritten atomically; re-running skips finished URLs;
* polite — robots.txt, per-request delay shared across workers, bounded pages;
* concurrent — N in-flight requests, but a single rate limiter for the domain;
* attributable — source URL, fetch time, sha256 and char count per document, plus
  a licence notice written into the pack.

Usage::

    cd backend
    PYTHONPATH=. .venv/Scripts/python.exe scripts/crawl_corpus.py \
        --name "Vue 3 官方文档（中文）" --slug vue3-zh \
        --sitemap https://cn.vuejs.org/sitemap.xml --include /guide/ \
        --max-pages 60 --out ../data/corpus/vue3-zh \
        --license "MIT · 来源 https://cn.vuejs.org/"

    # 或从若干起始页做 BFS
    PYTHONPATH=. .venv/Scripts/python.exe scripts/crawl_corpus.py \
        --name "示例" --slug demo --start https://example.com/ --max-pages 5
"""

from app.core.env import configure_env

configure_env()

import argparse  # noqa: E402
import asyncio  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from collections import deque  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

import httpx  # noqa: E402

from app.services.sources.base import ca_bundle  # noqa: E402
from app.services.sources.html_extract import (  # noqa: E402
    extract,
    is_crawlable,
    normalize_url,
    same_site,
)
from app.services.sources.robots import DEFAULT_UA, RobotsCache  # noqa: E402

USER_AGENT = DEFAULT_UA


class RateLimiter:
    """One delay per request, shared by all workers.

    Sleeping inside each worker would still let N requests hit the server at the
    same instant; a lock plus a "next allowed time" turns concurrency into
    throughput without turning it into a burst.
    """

    def __init__(self, delay: float) -> None:
        self.delay = max(0.0, delay)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        if self.delay <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait_for = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.delay
        if wait_for:
            await asyncio.sleep(wait_for)


def slug_for(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.strip("/").replace("/", "__").replace(".html", "") or "index"
    stem = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in path)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
    return f"{stem[:90]}-{digest}"


def load_index(out_dir: Path) -> dict:
    index_path = out_dir / "index.json"
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def write_index(out_dir: Path, payload: dict) -> None:
    tmp = out_dir / "index.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_dir / "index.json")


async def sitemap_urls(client: httpx.AsyncClient, sitemap_url: str) -> list[str]:
    response = await client.get(sitemap_url)
    response.raise_for_status()
    import xml.etree.ElementTree as ET

    root = ET.fromstring(response.text.strip())
    urls = [
        element.text.strip()
        for element in root.iter()
        if element.tag.endswith("loc") and element.text
    ]
    if root.tag.endswith("sitemapindex"):
        collected: list[str] = []
        for child in urls[:5]:
            try:
                child_response = await client.get(child)
                child_root = ET.fromstring(child_response.text.strip())
                collected.extend(
                    element.text.strip()
                    for element in child_root.iter()
                    if element.tag.endswith("loc") and element.text
                )
            except Exception:
                continue
        urls = collected
    return [normalize_url(url) for url in urls if normalize_url(url)]


async def crawl(args: argparse.Namespace) -> int:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    index = load_index(out_dir)
    # --force re-fetches everything and overwrites the files. Filenames are derived
    # from the URL, so this is idempotent: the pack ends up matching the site's
    # current content instead of a mix of old and new pages.
    documents: list[dict] = [] if args.force else index.get("documents", [])
    done_urls = set() if args.force else {item["url"] for item in documents}

    limiter = RateLimiter(args.delay)
    clients = httpx.AsyncClient(
        timeout=25,
        follow_redirects=True,
        verify=ca_bundle() if not args.insecure else False,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )

    async with clients as client:
        robots = RobotsCache(client, USER_AGENT)

        if args.sitemap:
            candidates = await sitemap_urls(client, args.sitemap)
            if args.include:
                candidates = [url for url in candidates if args.include in url]
            if args.exclude:
                candidates = [url for url in candidates if args.exclude not in url]
            queue = deque((url, 0) for url in candidates)
        else:
            queue = deque((normalize_url(url), 0) for url in args.start if normalize_url(url))
        if not queue:
            print("没有可抓取的 URL：检查 --start / --sitemap / --include")
            return 1

        domains = sorted({urlsplit(url).netloc for url, _ in queue if url})
        print(f"候选 {len(queue)} 个 URL，目标最多 {args.max_pages} 篇，域名 {domains}")

        semaphore = asyncio.Semaphore(max(1, args.concurrency))
        lock = asyncio.Lock()
        fetched = 0
        errors: list[dict] = []

        async def handle(url: str, depth: int) -> list[str]:
            nonlocal fetched
            if url in done_urls:
                return []
            if args.respect_robots and not await robots.allowed(url):
                errors.append({"url": url, "error": "robots.txt 禁止抓取"})
                return []
            async with semaphore:
                await limiter.wait()
                try:
                    response = await client.get(url)
                except Exception as exc:
                    errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
                    return []
            if response.status_code >= 400:
                errors.append({"url": url, "error": f"HTTP {response.status_code}"})
                return []
            page = extract(response.text, str(response.url))
            if len(page.markdown) < args.min_chars:
                errors.append({"url": url, "error": "正文过短，跳过"})
                return []

            final_url = normalize_url(str(response.url))
            stem = slug_for(final_url)
            heading = "" if page.markdown.startswith("# ") else f"# {page.title}\n\n"
            body = f"{heading}> 来源：{final_url}\n\n{page.markdown}\n"
            (out_dir / f"{stem}.md").write_text(body, encoding="utf-8")
            record = {
                "url": final_url,
                "title": page.title,
                "file": f"{stem}.md",
                "chars": len(body),
                "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "fetched_at": datetime.now(UTC).isoformat(),
                "depth": depth,
            }
            async with lock:
                done_urls.add(url)
                documents.append(record)
                fetched += 1
                if fetched % 5 == 0 or fetched == 1:
                    print(f"  已抓取 {fetched} 篇 · 最新：{page.title[:40]}")
            return page.links

        while queue and len(documents) < args.max_pages:
            batch = []
            while queue and len(batch) < args.concurrency * 2:
                url, depth = queue.popleft()
                if url in done_urls:
                    continue
                batch.append((url, depth))
            if not batch:
                break
            results = await asyncio.gather(*(handle(url, depth) for url, depth in batch))
            for (_url, depth), links in zip(batch, results, strict=False):
                if args.sitemap or depth >= args.max_depth:
                    continue
                for link in links:
                    if len(documents) + len(queue) >= args.max_pages * 2:
                        break
                    if link in done_urls or not same_site(link, domains):
                        continue
                    if not is_crawlable(link):
                        continue
                    if args.include and args.include not in link:
                        continue
                    if args.exclude and args.exclude in link:
                        continue
                    queue.append((link, depth + 1))

        payload = {
            "name": args.name,
            "slug": args.slug,
            "source": args.sitemap or (args.start[0] if args.start else ""),
            "license": args.license,
            "generated_at": datetime.now(UTC).isoformat(),
            "count": len(documents),
            "chars": sum(item["chars"] for item in documents),
            "documents": documents,
            "errors": errors[:50],
        }
        write_index(out_dir, payload)

        notice = out_dir / "LICENSE-NOTICE.md"
        notice.write_text(
            "# 语料来源与许可\n\n"
            f"- 语料名称：{args.name}\n"
            f"- 来源：{payload['source']}\n"
            f"- 许可说明：{args.license or '未声明，请在使用前自行确认'}\n"
            f"- 抓取时间：{payload['generated_at']}\n"
            f"- 文档数：{len(documents)}，总字符：{payload['chars']}\n\n"
            "本目录内容由 crawl_corpus.py 自动抓取，仅用于本地 RAG 演示与评测；\n"
            "版权归原作者所有，转载/再分发请遵守上游许可。\n",
            encoding="utf-8",
        )

    print(
        f"完成：{len(documents)} 篇 / {payload['chars']} 字符 -> {out_dir}\n"
        f"索引：index.json，许可说明：LICENSE-NOTICE.md，失败 {len(errors)} 条"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl a docs site into a corpus pack")
    parser.add_argument("--name", required=True, help="语料包名称")
    parser.add_argument("--slug", required=True, help="目录/标识，例如 vue3-zh")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--start", nargs="*", default=[], help="起始 URL（BFS）")
    parser.add_argument("--sitemap", default="", help="sitemap.xml 地址")
    parser.add_argument("--include", default="", help="URL 必须包含的子串")
    parser.add_argument("--exclude", default="", help="URL 排除的子串")
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.6, help="每次请求的最小间隔（秒）")
    parser.add_argument("--min-chars", type=int, default=200)
    parser.add_argument("--license", default="")
    parser.add_argument("--insecure", action="store_true", help="关闭 TLS 校验（仅限受控网络）")
    parser.add_argument("--force", action="store_true", help="忽略已有 index，全量重抓并覆盖")
    parser.add_argument("--respect-robots", action="store_true", default=True)
    raise SystemExit(asyncio.run(crawl(parser.parse_args())))


if __name__ == "__main__":
    main()
