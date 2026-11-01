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
    # --- 第二批：优先挑语义互相干扰的难题 ---
    (
        "q23",
        "Pinia 的 state、getter 和 action 分别是什么",
        "core-concepts",
        ("state", "getter", "action"),
    ),
    (
        "q24",
        "Pinia 的 setup store 和 option store 有什么区别",
        "core-concepts",
        ("setup", "option"),
    ),
    ("q25", "Pinia 和 Vuex 相比有什么不同", "introduction", ("Vuex", "Pinia")),
    ("q26", "Pinia 里怎么订阅 state 的变化", "core-concepts/state", ("$subscribe", "订阅")),
    ("q27", "命名路由和命名视图有什么区别", "named-views", ("命名视图", "命名路由")),
    ("q28", "路由重定向和别名有什么区别", "redirect-and-alias", ("重定向", "别名")),
    ("q29", "动态路由匹配和路由匹配语法有什么区别", "route-matching-syntax", ("匹配", "语法")),
    ("q30", "路由滚动行为怎么定制", "scroll-behavior", ("scrollBehavior", "滚动")),
    ("q31", "KeepAlive 是做什么用的", "keep-alive", ("KeepAlive", "缓存")),
    (
        "q32",
        "Transition 和 TransitionGroup 有什么区别",
        "transition-group",
        ("TransitionGroup", "列表"),
    ),
    ("q33", "异步组件怎么定义和使用", "components/async", ("defineAsyncComponent", "异步组件")),
    ("q34", "模板引用 ref 怎么拿到 DOM 或组件实例", "template-refs", ("模板引用", "ref")),
    ("q35", "Vite 的环境变量和模式怎么配合", "env-and-mode", ("模式", "环境变量")),
    ("q36", "Vite 生产构建产物怎么部署", "static-deploy", ("部署", "构建")),
    ("q37", "Vite 插件怎么写", "api-plugin", ("插件", "钩子")),
    ("q38", "Vite HMR API 怎么用", "api-hmr", ("hot", "HMR")),
    ("q39", "Vue 组件上的 v-model 怎么工作", "components/v-model", ("v-model", "双向")),
    ("q40", "插槽 slot 怎么用", "components/slots", ("插槽", "slot")),
    ("q41", "自定义指令怎么定义", "custom-directives", ("自定义指令", "directive")),
    ("q42", "组合式函数 composable 是什么", "composables", ("组合式函数", "composable")),
    ("q43", "Vue 应用怎么创建和挂载", "essentials/application", ("createApp", "mount")),
    ("q44", "单文件组件 SFC 是什么", "scaling-up/sfc", ("单文件组件", "SFC")),
    # --- 第三批：跨主题铺开，继续挑语义接近的页面 ---
    ("q45", "事件修饰符有哪些，怎么用", "essentials/event-handling", ("修饰符",)),
    ("q46", "v-model 在表单元素上怎么用", "essentials/forms", ("v-model",)),
    ("q47", "class 和 style 怎么动态绑定", "essentials/class-and-style", ("class", "style")),
    ("q48", "组件全局注册和局部注册有什么区别", "components/registration", ("全局", "局部")),
    ("q49", "子组件怎么向父组件触发事件", "components/events", ("emit",)),
    ("q50", "透传 attributes 是什么", "components/attrs", ("透传",)),
    ("q51", "Transition 组件怎么用", "built-ins/transition", ("Transition",)),
    ("q52", "模板语法里的插值和指令怎么用", "essentials/template-syntax", ("插值", "指令")),
    ("q53", "路由元信息 meta 怎么用", "advanced/meta", ("meta", "元信息")),
    ("q54", "路由切换时怎么加过渡动效", "advanced/transitions", ("过渡", "动效")),
    ("q55", "路由里怎么获取数据", "advanced/data-fetching", ("数据", "获取")),
    ("q56", "嵌套路由怎么配置", "essentials/nested-routes", ("嵌套",)),
    ("q57", "路由组件怎么传 props", "essentials/passing-props", ("props",)),
    ("q58", "404 页面路由怎么配置", "essentials/dynamic-matching", ("404", "捕获")),
    ("q59", "Vite 生产构建该怎么配置", "guide/build", ("build", "构建")),
    ("q60", "Vite 怎么和后端集成", "guide/backend-integration", ("后端",)),
    ("q61", "Vite 库模式怎么打包", "guide/lib", ("库模式", "lib")),
    ("q62", "Vite 构建性能怎么优化", "guide/performance", ("性能",)),
    ("q63", "Vite 怎么做服务端渲染", "guide/ssr", ("SSR", "服务端渲染")),
    ("q64", "Vite 的 base 公共路径怎么设置", "config/shared-options", ("base",)),
    ("q65", "Vite 预览服务器怎么配置", "config/preview-options", ("preview", "预览")),
    ("q66", "Pinia 插件能做什么", "core-concepts/plugins.html", ("插件",)),
    (
        "q67",
        "组件外怎么使用 Pinia store",
        "core-concepts/outside-component-usage.html",
        ("组件外",),
    ),
    ("q68", "Pinia 的 store 怎么单元测试", "cookbook/testing.html", ("测试",)),
    ("q69", "Pinia 怎么做热模块替换", "cookbook/hot-module-replacement.html", ("HMR", "热")),
    ("q70", "Pinia 里 store 之间怎么互相调用", "cookbook/composing-stores.html", ("store",)),
    ("q71", "怎么从 Vuex 迁移到 Pinia", "cookbook/migration-vuex.html", ("迁移",)),
    ("q72", "Pinia 在选项式 API 里怎么用", "cookbook/options-api.html", ("setup", "选项式")),
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
