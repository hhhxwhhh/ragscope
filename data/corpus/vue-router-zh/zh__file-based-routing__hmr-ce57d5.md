> 来源：https://router.vuejs.org/zh/file-based-routing/hmr.html

# 热更新

当使用 `definePage()` 和 `<route>` 块时，可以为你的路由启用热更新 (HMR)，在你对路由进行更改时避免重新加载页面或服务器。

强烈建议启用 HMR，目前仅适用于 Vite。

src/router.ts

ts

```
import { createRouter, createWebHistory } from 'vue-router'
import {
  routes,
  handleHotUpdate,
} from 'vue-router/auto-routes'

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 这将在运行时更新路由而无需重新加载页面
if (import.meta.hot) {
  handleHotUpdate(router)
}
```
## 运行时路由

如果你在运行时添加路由，你需要在回调中添加它们以确保它们在开发期间被添加。

src/router.ts

ts

```
import { createRouter, createWebHistory } from 'vue-router'
import { routes, handleHotUpdate } from 'vue-router/auto-routes'

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

function addRedirects() {
  router.addRoute({
    path: '/new-about',
    redirect: '/about?from=/new-about',
  })
}

if (import.meta.hot) {
  handleHotUpdate(router, (newRoutes) => {
    addRedirects()
  })
} else {
  // 生产环境
  addRedirects()
}
```
这是可选的，你也可以只是重新加载页面。
