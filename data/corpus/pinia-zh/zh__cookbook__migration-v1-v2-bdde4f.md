> 来源：https://pinia.vuejs.org/zh/cookbook/migration-v1-v2.html

# 从 0.x (v1) 迁移至 v2

从 `2.0.0-rc.4` 版本开始，pinia 同时支持 Vue 2 和 Vue 3！这意味着，v2 版本的所有更新，将会让 Vue 2 和 Vue 3 的用户都受益。如果你使用的是 Vue 3，这对你来说没有任何改变，因为你已经在使用 rc 版本，你可以查看发布日志来了解所有更新的详细解释。如果你使用的不是 Vue 3，那这个指南是为你准备的!

## 弃用

让我们来看看你需要对你的代码做出的所有修改。首先，为了解所有弃用，确保你已经在运行最新的 0.x 版本：

shell

```
npm i 'pinia@^0.x.x'
# 或者使用 yarn
yarn add 'pinia@^0.x.x'
```
如果你正在使用 ESLint，可以考虑使用这个插件，来查找所有废弃的用法。否则，你得手动检查。这些都是被废弃且已经删除了的 API：

- `createStore()` 变成 `defineStore()`
- 在订阅中，`storeName` 变成 `storeId`
- `PiniaPlugin` 更名为 `PiniaVuePlugin`(Vue 2 的 Pinia 插件)
- `$subscribe()` 不再接受 boolean 作为第二个参数，而是传递一个带有 `detached: true` 的对象。
- Pinia 插件不再直接接收 store 的 `id`。使用 `store.$id` 代替。

## 非兼容性更新

删除下面这些后，你可以用下面命令升级到 V2 版了：

shell

```
npm i 'pinia@^2.x.x'
# 或者使用 yarn
yarn add 'pinia@^2.x.x'
```
然后开始更新你的代码。

### 通用 Store 类型

添加于 2.0.0-rc.0

用 `StoreGeneric` 取代 `GenericStore` 类型的全部用法。这是新的通用 store 类型，应该可以接受任何类型的 store。如果你在写函数时使用 `Store` 类型而不想传递其泛型 (例如`Store<Id, State, Getters, Actions>`)，你可以使用 `StoreGeneric`，因为没有泛型的 `Store` 类型会创建一个空的 store 类型：

ts

```
function takeAnyStore(store: Store) {}
function takeAnyStore(store: StoreGeneric) {}
function takeAnyStore(store: GenericStore) {}
function takeAnyStore(store: StoreGeneric) {}
```
## 针对插件的 `DefineStoreOptions`

如果你在用 TypeScript 写插件并扩展了 `DefineStoreOptions` 类型来添加自定义选项，你应该把它改名为 `DefineStoreOptionsBase`。这个类型将同时适用于 setup 和 option store。

ts

```
declare module 'pinia' {
  export interface DefineStoreOptions<S, Store> {
  export interface DefineStoreOptionsBase<S, Store> {
    debounce?: {
      [k in keyof StoreActions<Store>]?: number
    }
  }
}
```
## `PiniaStorePlugin` 已被重命名

类型 `PiniaStorePlugin` 被重新命名为 `PiniaPlugin`。

ts

```
import { PiniaStorePlugin } from 'pinia'
import { PiniaPlugin } from 'pinia'
const piniaPlugin: PiniaStorePlugin = () => {
const piniaPlugin: PiniaPlugin = () => {
  // ...
}
```
注意这个更新只能在升级到最新的没有弃用的 Pinia 版本后生效。

## `@vue/composition-api` 版本

由于 pinia 目前依赖于 `effectScope()` ，你使用的 `@vue/composition-api` 的版本必须是 `1.1.0` 及以上：

shell

```
npm i @vue/composition-api@latest
# 或者使用 yarn
yarn add @vue/composition-api@latest
```
## 支持 webpack 4

如果你使用的是 webpack 4 (Vue CLI 使用的是 webpack 4)，你可能会遇到这样的错误：

```
ERROR  Failed to compile with 18 errors

 error  in ./node_modules/pinia/dist/pinia.mjs

Can't import the named export 'computed' from non EcmaScript module (only default export is available)
```
这是构建文件为支持 Node.js 中的原生 ESM 模块进行的现代化适配。为更好地支持 Node，文件现在使用的扩展名是 `.mjs` 和 `.cjs`。要解决这个问题，你有两种可用的方法：

- 如果你使用 Vue CLI 4.x，升级你的依赖。具体修复步骤如下。
- 如果你不可能升级，请将下面的代码添加到你的 `vue.config.js` 中：
js

```
// vue.config.js
module.exports = {
  configureWebpack: {
    module: {
      rules: [
        {
          test: /\.mjs$/,
          include: /node_modules/,
          type: 'javascript/auto',
        },
      ],
    },
  },
}
```
- 如果你手动处理 webpack，你将必须让它知道如何处理 `.mjs` 文件：
js

```
// webpack.config.js
module.exports = {
  module: {
    rules: [
      {
        test: /\.mjs$/,
        include: /node_modules/,
        type: 'javascript/auto',
      },
    ],
  },
}
```
## Devtools

Pinia v2 不再劫持 Vue Devtools v5，它需要的是 Vue Devtools v6。可以在 Vue Devtools 文档中找到该扩展 beta 版本的下载链接。

## Nuxt

如果你正在使用 Nuxt，pinia 现在有了专门的 Nuxt 软件包🎉。请用以下方法安装它：

bash

```
npm i @pinia/nuxt
# 或者使用 yarn
yarn add @pinia/nuxt
```
还要确保更新你的 `@nuxtjs/composition-api` 包。

如果你使用 TypeScript，还要调整你的 `nuxt.config.js` 和 `tsconfig.json`：

js

```
// nuxt.config.js
module.exports {
  buildModules: [
    '@nuxtjs/composition-api/module',
    'pinia/nuxt',
    '@pinia/nuxt',
  ],
}
```
json

```
// tsconfig.json
{
  "types": [
    // ...
    "pinia/nuxt/types"
    "@pinia/nuxt"
  ]
}
```
Nuxt 专属章节也值得一读。
