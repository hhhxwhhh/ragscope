> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.DefineStoreOptions.html

# 接口：DefineStoreOptions<Id, S, G, A>

pinia.DefineStoreOptions

用于 option store 的 `defineStore()` 的配置参数。 可以通过插件 API 扩展来增强 store。

`See`

DefineStoreOptionsBase.

## 类型参数

 | 名字 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | `G`
 | `A` | `A`

## 层次结构

- `DefineStoreOptionsBase`<`S`, `Store`<`Id`, `S`, `G`, `A`>>
↳ `DefineStoreOptions`

## 属性

### actions

• `Optional` actions: `A` & `ThisType`<`A` & `UnwrapRef`<`S`> & `_StoreWithState`<`Id`, `S`, `G`, `A`> & `_StoreWithGetters`<`G`> & `PiniaCustomProperties`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`>>

action 的可选对象

### getters

• `Optional` getters: `G` & `ThisType`<`UnwrapRef`<`S`> & `_StoreWithGetters`<`G`> & `PiniaCustomProperties`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`>> & `_GettersTree`<`S`>

getter 的可选对象

### id

• id: `Id`

唯一的字符串密钥，用于识别整个应用中的 store。

### state

• `Optional` state: () => `S`

#### 类型声明

▸ (): `S`

创建一个新 state 的函数。 必须是一个箭头函数，以确保正确的类型标注!

##### 返回值

`S`

## 方法

### hydrate

▸ `Optional` hydrate(`storeState`, `initialState`): `void`

当 store 定义中使用了复杂的 state (如仅客户端的引用)，并且仅从 `pinia.state` 中复制值是不够时， 允许在 SSR 期间对 store 进行 hydrating。

`Example`

如果在你的 `state` 中，你使用了任何在服务器和客户端有不同值的 `customRef`、`computed` 或 `ref`， 你需要手动激活它们。 例如，一个存储在本地存储的自定义 ref：

ts

```
const useStore = defineStore('main', {
  state: () => ({
    n: useLocalStorage('key', 0),
  }),
  hydrate(storeState, initialState) {
    // @ts-expect-error: https://github.com/microsoft/TypeScript/issues/43826
    storeState.n = useLocalStorage('key', 0)
  },
})
```
#### 参数

 | 名字 | 类型 | 描述
 | `storeState` | `UnwrapRef`<`S`> | the current state in the store
 | `initialState` | `UnwrapRef`<`S`> | initialState

#### 返回值

`void`
