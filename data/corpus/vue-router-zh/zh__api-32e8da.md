> 来源：https://router.vuejs.org/zh/api

# API 文档

## TS 枚举

- NavigationFailureType

## TS 接口

- HistoryState
- NavigationFailure
- NavigationGuard
- NavigationGuardNext
- NavigationGuardWithThis
- NavigationHookAfter
- RouteLocation
- RouteLocationMatched
- RouteLocationNormalized
- RouteLocationNormalizedLoaded
- RouteLocationOptions
- RouteMeta
- RouteRecordMultipleViews
- RouteRecordMultipleViewsWithChildren
- RouteRecordNormalized
- RouteRecordRedirect
- RouteRecordSingleView
- RouteRecordSingleViewWithChildren
- Router
- RouterHistory
- RouterLinkProps
- RouterOptions
- RouterScrollBehavior
- RouterViewProps
- _RouteRecordBase

## TS 类型别名

### LocationQuery

Ƭ LocationQuery: `Record`<`string`, `LocationQueryValue` | `LocationQueryValue`[]>

出现在 RouteLocationNormalized 中的规范化查询对象。

### LocationQueryRaw

Ƭ LocationQueryRaw: `Record`<`string` | `number`, `LocationQueryValueRaw` | `LocationQueryValueRaw`[]>

松散的 LocationQuery 对象，可以被传递给诸如 Router.push、Router.replace 或任何创建 RouteLocationRaw 的函数。

### PathParserOptions

Ƭ PathParserOptions: `Pick`<`_PathParserOptions`, `"end"` | `"sensitive"` | `"strict"`>

### RouteComponent

Ƭ RouteComponent: `Component` | `DefineComponent`

在 RouteLocationMatched 中允许的组件。

### RouteLocationRaw

Ƭ RouteLocationRaw: `string` | `RouteLocationPathRaw` | `RouteLocationNamedRaw`

用户级别的路由位置。

### RouteParams

Ƭ RouteParams: `Record`<`string`, `RouteParamValue` | `RouteParamValue`[]>

### RouteParamsRaw

Ƭ RouteParamsRaw: `Record`<`string`, `RouteParamValueRaw` | `Exclude`<`RouteParamValueRaw`, `null` | `undefined`>[]>

### RouteRecord

Ƭ RouteRecord: `RouteRecordNormalized`

一个路由记录的规范化版本。

### RouteRecordName

Ƭ RouteRecordName: `string` | `symbol`

用户定义的路由记录的可能的名称。

### RouteRecordRaw

Ƭ RouteRecordRaw: `RouteRecordSingleView` | `RouteRecordSingleViewWithChildren` | `RouteRecordMultipleViews` | `RouteRecordMultipleViewsWithChildren` | `RouteRecordRedirect`

### UseLinkOptions

Ƭ UseLinkOptions: `VueUseOptions`<`RouterLinkOptions`>

## 变量

### RouterLink

• `Const` RouterLink: `_RouterLinkI`

用来渲染一个链接的组件，该链接在被点击时会触发导航。

### RouterView

• `Const` RouterView: () => { `$props`: `AllowedComponentProps` & `ComponentCustomProps` & `VNodeProps` & `RouterViewProps` ; `$slots`: { `default?`: (`__namedParameters`: { `Component`: `VNode`<`RendererNode`, `RendererElement`, { `[key: string]`: `any`; }> ; `route`: `RouteLocationNormalizedLoaded` }) => `VNode`<`RendererNode`, `RendererElement`, { `[key: string]`: `any`; }>[] } }

#### 类型声明

• new RouterView(): `Object`

用于显示用户当前所处路由的组件。

##### 返回值

`Object`

 | 名称 | 类型
 | `$props` | `AllowedComponentProps` & `ComponentCustomProps` & `VNodeProps` & `RouterViewProps`
 | `$slots` | { `default?`: (`__namedParameters`: { `Component`: `VNode`<`RendererNode`, `RendererElement`, { `[key: string]`: `any`; }> ; `route`: `RouteLocationNormalizedLoaded` }) => `VNode`<`RendererNode`, `RendererElement`, { `[key: string]`: `any`; }>[] }
 | `$slots.default?` | (`__namedParameters`: { `Component`: `VNode`<`RendererNode`, `RendererElement`, { `[key: string]`: `any`; }> ; `route`: `RouteLocationNormalizedLoaded` }) => `VNode`<`RendererNode`, `RendererElement`, { `[key: string]`: `any`; }>[]

### START_LOCATION

• `Const` START_LOCATION: `RouteLocationNormalizedLoaded`

路由器的初始路由位置。可以在导航守卫中使用来区分初始导航。

示例

js

```
import { START_LOCATION } from 'vue-router'

router.beforeEach((to, from) => {
  if (from === START_LOCATION) {
    // 初始导航
  }
})
```
## 函数

### createMemoryHistory

▸ createMemoryHistory(`base?`): `RouterHistory`

创建一个基于内存的历史。该历史的主要目的是为了处理服务端渲染。它从一个不存在的特殊位置开始。用户可以通过调用 `router.push` 或 `router.replace` 将该位置替换成起始位置。

#### 参数

 | 名称 | 类型 | 默认值 | 描述
 | `base` | `string` | `''` | 所有 URL 的基础位置，默认为 '/'

#### 返回值

`RouterHistory`

一个历史对象，可以传递给路由器构造函数。

### createRouter

▸ createRouter(`options`): `Router`

创建一个可以被 Vue 应用使用的 Router 实例。

#### 参数

 | 名称 | 类型 | 描述
 | `options` | `RouterOptions` | RouterOptions

#### 返回值

`Router`

### createWebHashHistory

▸ createWebHashHistory(`base?`): `RouterHistory`

创建一个 hash 模式的历史。在没有主机的 web 应用 (如 `file://`) 或无法通过配置服务器来处理任意 URL 的时候非常有用。

#### 参数

 | 名称 | 类型 | 描述
 | `base?` | `string` | 可选提供的基础位置。默认为 `location.pathname + location.search`。如果在 `head` 中有一个 `<base>` 标签，它的值会因此被忽略，但注意它会影响所有 history.pushState() 的调用，这意味着如果你使用一个 `<base>` 标签，它的 `href` 值必须与这个参数匹配 (忽略 `#` 后的任何东西)。

#### 返回值

`RouterHistory`

示例

js

```
// 基于 https://example.com/folder
createWebHashHistory() // 给出一个 `https://example.com/folder#` 的 URL
createWebHashHistory('/folder/') // 给出一个 `https://example.com/folder/#` 的 URL
// 如果其基础位置提供了 `#`，则不会被 `createWebHashHistory` 添加
createWebHashHistory('/folder/#/app/') // 给出一个 `https://example.com/folder/#/app/` 的 URL
// 你应该避免这样做，因为它改变了原始的 URL 且破坏了复制 URL 的工作
createWebHashHistory('/other-folder/') // 给出一个 `https://example.com/other-folder/#` 的 URL

// 基于 file:///usr/etc/folder/index.html
// 对于没有 `host` 的位置，该 base 会被忽略
createWebHashHistory('/iAmIgnored') // 给出一个 `file:///usr/etc/folder/index.html#` 的 URL
```
### createWebHistory

▸ createWebHistory(`base?`): `RouterHistory`

创建一个 HTML5 历史。对于单页应用来说这是最常见的历史。

#### 参数

 | 名称 | 类型
 | `base?` | `string`

#### 返回值

`RouterHistory`

▸ isNavigationFailure(`error`, `type?`): error is NavigationRedirectError

检查一个对象是否是 NavigationFailure。

 | 名称 | 类型 | 描述
 | `error` | `any` | 可能的 NavigationFailure
 | `type?` | `NAVIGATION_GUARD_REDIRECT` | 可选的待检查类型

error is NavigationRedirectError

示例

js

```
import { isNavigationFailure, NavigationFailureType } from 'vue-router'

router.afterEach((to, from, failure) => {
  // 任何类型的导航失败
  if (isNavigationFailure(failure)) {
    // ...
  }
  // 重复的导航
  if (isNavigationFailure(failure, NavigationFailureType.duplicated)) {
    // ...
  }
  // 中止或取消的导航
  if (isNavigationFailure(failure, NavigationFailureType.aborted | NavigationFailureType.canceled)) {
    // ...
  }
})
```
▸ isNavigationFailure(`error`, `type?`): error is NavigationFailure

 | 名称 | 类型
 | `error` | `any`
 | `type?` | `ErrorTypes` | `NavigationFailureType`

error is NavigationFailure

### loadRouteLocation

▸ loadRouteLocation(`route`): `Promise`<`RouteLocationNormalizedLoaded`>

确保路由被加载，所以它可以作为一个 prop 传递给 `<RouterView>`。

#### 参数

 | 名称 | 类型 | 描述
 | `route` | `RouteLocationNormalized` | 解析要加载的路由

#### 返回值

`Promise`<`RouteLocationNormalizedLoaded`>

### onBeforeRouteLeave

▸ onBeforeRouteLeave(`leaveGuard`): `void`

添加一个导航守卫，不论当前位置的组件何时离开都会触发。类似于 beforeRouteLeave，但可以在任意组件中使用。当组件被卸载时，该守卫会被移除。

#### 参数

 | 名称 | 类型 | 描述
 | `leaveGuard` | `NavigationGuard` | NavigationGuard

#### 返回值

`void`

### onBeforeRouteUpdate

▸ onBeforeRouteUpdate(`updateGuard`): `void`

添加一个导航守卫，不论当前位置何时被更新都会触发。类似于 beforeRouteUpdate，但可以在任何组件中使用。当组件被卸载时，该守卫会被移除。

#### 参数

 | 名称 | 类型 | 描述
 | `updateGuard` | `NavigationGuard` | NavigationGuard

#### 返回值

`void`

### useLink

▸ useLink(`props`): `Object`

#### 参数

 | 名称 | 类型
 | `props` | `VueUseOptions`<`RouterLinkOptions`>

#### 返回值

`Object`

 | 名称 | 类型
 | `href` | `ComputedRef<string\>`
 | `isActive` | `ComputedRef`<`boolean`>
 | `isExactActive` | `ComputedRef`<`boolean`>
 | `navigate` | (`e`: `MouseEvent`) => `Promise`<`void` | `NavigationFailure`>
 | `route` | `ComputedRef`<`RouteLocation` & { `href`: `string` }>

### useRoute

▸ useRoute(): `RouteLocationNormalizedLoaded`

返回当前的路由地址。相当于在模板中使用 `$route`。

#### 返回值

`RouteLocationNormalizedLoaded`

### useRouter

▸ useRouter(): `Router`

返回路由器实例。相当于在模板中使用 `$router`。

#### 返回值

`Router`
