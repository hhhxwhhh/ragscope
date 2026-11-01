> 来源：https://router.vuejs.org/zh/installation.html

# 安装

 Get the Vue Router Cheat Sheet from Vue Mastery

## 包管理器

对于一个现有的使用 JavaScript 包管理器的项目，你可以从 npm registry 中安装 Vue Router：

npmyarnpnpmbun

bash

```
npm install vue-router
```
bash

```
yarn add vue-router
```
bash

```
pnpm add vue-router
```
bash

```
bun add vue-router
```
如果你打算启动一个新项目，你可能会发现使用 create-vue 这个脚手架工具更容易，它能创建一个基于 Vite 的项目，并包含加入 Vue Router 的选项：

npmyarnpnpmbun

bash

```
npm create vue@latest
```
bash

```
yarn create vue
```
bash

```
pnpm create vue
```
bash

```
bun create vue
```
你需要回答一些关于你想创建的项目类型的问题。如果您选择安装 Vue Router，示例应用还将演示 Vue Router 的一些核心特性。

使用包管理器的项目通常会使用 ES 模块来访问 Vue Router，例如 `import { createRouter } from 'vue-router'`。

## 直接下载 / CDN

如果你不想使用打包工具，你可以通过 CDN 或下载相关文件自行托管，直接在浏览器中加载 Vue Router。

Vue Router 提供了多种预构建文件以覆盖不同的使用场景，与 Vue 类似。如果你还不熟悉 Vue 是如何处理这一点的，我们建议先阅读相关文档：

- https://vuejs.org/guide/quick-start.html#using-vue-from-cdn

Vue Router 同时支持 ES module 和 global 构建版本。我们推荐尽可能使用 ES modules。

无论选择哪种方式，对于你所使用的库，特别是在生产环境中，固定版本都很重要。这意味着你应该在 CDN URL 中包含你要使用的确切版本，而不是让 CDN 为你选择最新版本。

每个构建都附带了开发版本和生产版本。开发版本明显更大，但包含了有助于调试的额外代码。生产版本的名称中带有 `.prod`。

### 使用 ES modules

html

```
<script type="importmap">
  {
    "imports": {
      "vue": "https://unpkg.com/vue@3.5.44/dist/vue.esm-browser.js",
      "vue-router": "https://unpkg.com/vue-router@5.4.0/dist/vue-router.esm-browser.js",
      "nostics": "https://unpkg.com/nostics@1/dist/index.mjs",
      "@vue/devtools-api": "https://unpkg.com/@vue/devtools-api@8/dist/vue-devtools-api.esm-browser.js"
    }
  }
</script>
<script type="module">
  import { createApp } from 'vue'
  import { createRouter } from 'vue-router'

  // ...
</script>
```
`@vue/devtools-api` 仅在使用 Vue Router 的开发构建版本时需要。当使用生产构建版本 `vue-router.esm-browser.prod.js` 时可以移除。

### 使用 global 构建

html

```
<script src="https://unpkg.com/vue@3.5.44/dist/vue.global.js"></script>
<script src="https://unpkg.com/vue-router@5.4.0/dist/vue-router.global.js"></script>
<script>
  const { createApp } = Vue
  const { createRouter } = VueRouter

  // ...
</script>
```
对应的 Vue Router 生产构建版本为 `vue-router.global.prod.js`。
