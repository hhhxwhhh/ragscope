"""One-off: preview candidate gold chunks so they can be labelled by hand.

Not part of the app. Prints, for each (question, target page, keywords) triple,
the chunks of that page containing any keyword — the annotator then picks the
chunk indexes that actually answer the question.

Usage:
    PYTHONPATH=. python scripts/_inspect_chunks.py <kb_id>
"""

import asyncio
import sys

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.chunk import Chunk
from app.models.document import Document

# (label, question, url substring, keywords)
TARGETS: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("q1", "ref 和 reactive 有什么区别", "reactivity-fundamentals", ("reactive()", "ref()")),
    ("q2", "组合式 API 和选项式 API 该怎么选", "composition-api-faq", ("组合式", "选项式")),
    ("q3", "Teleport 解决什么问题", "teleport", ("Teleport", "传送")),
    ("q4", "watch 和 watchEffect 的区别", "watchers", ("watchEffect", "watch")),
    ("q5", "computed 和 methods 的区别", "computed", ("计算属性", "缓存")),
    ("q6", "v-if 和 v-show 怎么选", "conditional", ("v-show", "v-if")),
    ("q7", "父子组件怎么传值", "components/props", ("props", "defineProps")),
    ("q8", "Vue 响应式原理是怎么实现的", "reactivity-in-depth", ("Proxy", "track")),
    ("q9", "v-for 里 key 有什么作用", "list", ("key", "v-for")),
    ("q10", "onMounted 什么时候调用", "lifecycle", ("onMounted", "挂载")),
    ("q11", "Suspense 是做什么用的", "suspense", ("Suspense", "异步")),
    ("q12", "provide 和 inject 怎么用", "provide-inject", ("provide", "inject")),
    ("q13", "Vite 依赖预构建是做什么的", "dep-pre-bundling", ("预构建", "esbuild")),
    ("q14", "Vite 为什么启动比 webpack 快", "guide/why", ("为什么", "启动")),
    ("q15", "Vite 怎么配置开发服务器代理", "server-options", ("proxy", "代理")),
    ("q16", "Vite 怎么处理静态资源", "guide/assets", ("静态资源", "assets")),
    ("q17", "build.target 怎么设置", "build-options", ("target",)),
    ("q18", "路由 params 和 query 有什么区别", "essentials/navigation", ("params", "query")),
    ("q19", "路由懒加载怎么实现", "lazy-loading", ("懒加载", "动态导入")),
    ("q20", "导航守卫有哪些，执行顺序是什么", "navigation-guards", ("守卫", "beforeEach")),
    ("q21", "history 模式和 hash 模式有什么区别", "history-mode", ("history", "hash")),
    ("q22", "编程式导航怎么跳转", "essentials/navigation", ("router.push", "编程式")),
]


async def main() -> None:
    kb_id = sys.argv[1]
    async with SessionLocal() as db:
        for label, question, needle, keywords in TARGETS:
            rows = (
                await db.execute(
                    select(Document.id, Document.filename, Document.external_url)
                    .where(Document.kb_id == kb_id, Document.external_url.ilike(f"%{needle}%"))
                    .order_by(Document.filename)
                )
            ).all()
            if not rows:
                print(f"\n### {label} {question}\n    !! no doc matching {needle}")
                continue
            print(f"\n### {label} {question}")
            for doc_id, filename, url in rows[:2]:
                print(f"    DOC {filename} | {url}")
                chunks = (
                    await db.execute(
                        select(Chunk.chunk_index, Chunk.heading_path, Chunk.content)
                        .where(Chunk.document_id == doc_id)
                        .order_by(Chunk.chunk_index)
                    )
                ).all()
                for index, heading, content in chunks:
                    body = (content or "").replace("\n", " ")
                    hits = [kw for kw in keywords if kw in body]
                    if not hits:
                        continue
                    print(
                        f"    [{index}] ({','.join(hits)}) {heading} :: {body[:130]}"
                    )


if __name__ == "__main__":
    asyncio.run(main())
