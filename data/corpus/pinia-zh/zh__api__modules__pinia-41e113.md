> 来源：https://pinia.vuejs.org/zh/api/modules/pinia.html

# 模块：pinia

## 枚举

- MutationType

## 接口

- DefineSetupStoreOptions
- DefineStoreOptions
- DefineStoreOptionsBase
- DefineStoreOptionsInPlugin
- MapStoresCustomization
- Pinia
- PiniaCustomProperties
- PiniaCustomStateProperties
- PiniaPlugin
- PiniaPluginContext
- StoreDefinition
- StoreProperties
- SubscriptionCallbackMutationDirect
- SubscriptionCallbackMutationPatchFunction
- SubscriptionCallbackMutationPatchObject
- _StoreOnActionListenerContext
- _StoreWithState
- _SubscriptionCallbackMutationBase

## 类型别名

### PiniaStorePlugin

Ƭ PiniaStorePlugin: `PiniaPlugin`

扩展每个 store 的插件。

`deprecated`

使用 PiniaPlugin 代替

### StateTree

Ƭ StateTree: `Record`<`string` | `number` | `symbol`, `any`>

Store 的通用 state

### Store

Ƭ Store<`Id`, `S`, `G`, `A`>: `_StoreWithState`<`Id`, `S`, `G`, `A`> & `UnwrapRef`<`S`> & `_StoreWithGetters`<`G`> & `_ActionsTree` extends `A` ? {} : `A` & `PiniaCustomProperties`<`Id`, `S`, `G`, `A`> & `PiniaCustomStateProperties`<`S`>

创建 store 的 Store 类型。

#### 类型参数

 | 名称 | 类型
 | `Id` | 扩展自 `string` = `string`
 | `S` | 扩展自 `StateTree` = {}
 | `G` | {}
 | `A` | {}

### StoreActions

Ƭ StoreActions<`SS`>: `SS` extends `Store`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, infer A> ? `A` : `_ExtractActionsFromSetupStore`<`SS`>

提取一个 store 类型的 action。 对 Setup Store 或 Option Store 都有效。

#### 类型参数

 | 名称
 | `SS`

### StoreGeneric

Ƭ StoreGeneric: `Store`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`>

泛型与 Store 的类型不安全版本。 在访问字符串时不会失败， 这使得编写不在意传递的 store 类型的通用函数更加容易。

### StoreGetters

Ƭ StoreGetters<`SS`>: `SS` extends `Store`<`string`, `StateTree`, infer G, `_ActionsTree`> ? `_StoreWithGetters`<`G`> : `_ExtractGettersFromSetupStore`<`SS`>

提取一个 store 类型的 getter。 对 Setup Store 或 Option Store都有效。

#### 类型参数

 | 名称
 | `SS`

### StoreOnActionListener

Ƭ StoreOnActionListener<`Id`, `S`, `G`, `A`>: (`context`: `StoreOnActionListenerContext`<`Id`, `S`, `G`, {} extends `A` ? `_ActionsTree` : `A`>) => `void`

#### 类型参数

 | 名称 | 类型
 | `Id` | 扩展自 `string`
 | `S` | 扩展自 `StateTree`
 | `G` | `G`
 | `A` | `A`

#### 类型声明

▸ (`context`): `void`

`store.$onAction()` 的参数

##### 参数

 | 名称 | 类型
 | `context` | `StoreOnActionListenerContext`<`Id`, `S`, `G`, {} extends `A` ? `_ActionsTree` : `A`>

##### 返回值

`void`

### StoreOnActionListenerContext

Ƭ StoreOnActionListenerContext<`Id`, `S`, `G`, `A`>: `_ActionsTree` extends `A` ? `_StoreOnActionListenerContext`<`StoreGeneric`, `string`, `_ActionsTree`> : { [Name in keyof A]: Name extends string ? _StoreOnActionListenerContext<Store<Id, S, G, A>, Name, A> : never }[keyof `A`]

传递给 `store.$onAction(context => {})` 的回调的上下文对象。 TODO：应该只有Id，Store 和 Action 来生成适当的对象。

#### 类型参数

 | 名称 | 类型
 | `Id` | 扩展自 `string`
 | `S` | 扩展自 `StateTree`
 | `G` | `G`
 | `A` | `A`

### StoreState

Ƭ StoreState<`SS`>: `SS` extends `Store`<`string`, infer S, `_GettersTree`<`StateTree`>, `_ActionsTree`> ? `UnwrapRef`<`S`> : `_ExtractStateFromSetupStore`<`SS`>

提取一个 store 类型的 state。 对 Setup Store 或 Option Store 都有效。请注意，它自动解包 refs。

#### 类型参数

 | 名称
 | `SS`

### SubscriptionCallback

Ƭ SubscriptionCallback<`S`>: (`mutation`: `SubscriptionCallbackMutation`<`S`>, `state`: `UnwrapRef`<`S`>) => `void`

#### 类型参数

 | 名称
 | `S`

#### 类型声明

▸ (`mutation`, `state`): `void`

订阅的回调

##### 参数

 | 名称 | 类型
 | `mutation` | `SubscriptionCallbackMutation`<`S`>
 | `state` | `UnwrapRef`<`S`>

##### 返回值

`void`

### SubscriptionCallbackMutation

Ƭ SubscriptionCallbackMutation<`S`>: `SubscriptionCallbackMutationDirect` | `SubscriptionCallbackMutationPatchObject`<`S`> | `SubscriptionCallbackMutationPatchFunction`

传递给订阅回调的上下文对象。

#### 类型参数

 | 名称
 | `S`

### _ActionsTree

Ƭ _ActionsTree: `Record`<`string`, `_Method`>

行动的对象的类型。仅供内部使用。 仅供内部使用

### _Awaited

Ƭ _Awaited<`T`>: `T` extends `null` | `undefined` ? `T` : `T` extends `object` & { `then`: (`onfulfilled`: `F`) => `any` } ? `F` extends (`value`: infer V, ...`args`: `any`) => `any` ? `_Awaited`<`V`> : `never` : `T`

#### 类型参数

 | 名称
 | `T`

### _DeepPartial

Ƭ _DeepPartial<`T`>: { [K in keyof T]?: _DeepPartial<T[K]> }

递归的 `Partial<T>`。 ['$patch']会用到。

仅供内部使用

#### 类型参数

 | 名称
 | `T`

### _ExtractActionsFromSetupStore

Ƭ _ExtractActionsFromSetupStore<`SS`>: `SS` extends `undefined` | `void` ? {} : `_ExtractActionsFromSetupStore_Keys`<`SS`> extends keyof `SS` ? `Pick`<`SS`, `_ExtractActionsFromSetupStore_Keys`<`SS`>> : `never`

仅供内部使用

#### 类型参数

 | 名称
 | `SS`

### _ExtractActionsFromSetupStore_Keys

Ƭ _ExtractActionsFromSetupStore_Keys<`SS`>: keyof { [K in keyof SS as SS[K] extends _Method ? K : never]: any }

能够通过 IDE 进行重构的类型。 仅供内部使用

#### 类型参数

 | 名称
 | `SS`

### _ExtractGettersFromSetupStore

Ƭ _ExtractGettersFromSetupStore<`SS`>: `SS` extends `undefined` | `void` ? {} : `_ExtractGettersFromSetupStore_Keys`<`SS`> extends keyof `SS` ? `Pick`<`SS`, `_ExtractGettersFromSetupStore_Keys`<`SS`>> : `never`

仅供内部使用

#### 类型参数

 | 名称
 | `SS`

### _ExtractGettersFromSetupStore_Keys

Ƭ _ExtractGettersFromSetupStore_Keys<`SS`>: keyof { [K in keyof SS as SS[K] extends ComputedRef ? K : never]: any }

能够通过 IDE 进行重构的类型。 仅供内部使用

#### 类型参数

 | 名称
 | `SS`

### _ExtractStateFromSetupStore

Ƭ _ExtractStateFromSetupStore<`SS`>: `SS` extends `undefined` | `void` ? {} : `_ExtractStateFromSetupStore_Keys`<`SS`> extends keyof `SS` ? `_UnwrapAll`<`Pick`<`SS`, `_ExtractStateFromSetupStore_Keys`<`SS`>>> : `never`

仅供内部使用

#### 类型参数

 | 名称
 | `SS`

### _ExtractStateFromSetupStore_Keys

Ƭ _ExtractStateFromSetupStore_Keys<`SS`>: keyof { [K in keyof SS as SS[K] extends _Method | ComputedRef ? never : K]: any }

能够通过 IDE 进行重构的类型。 仅供内部使用

#### 类型参数

 | 名称
 | `SS`

### _GettersTree

Ƭ _GettersTree<`S`>: `Record`<`string`, (`state`: `UnwrapRef`<`S`> & `UnwrapRef`<`PiniaCustomStateProperties`<`S`>>) => `any` | () => `any`>

推断参数的 Getter 对象的类型。仅供内部使用。 仅供内部使用

#### 类型参数

 | 名称 | 类型
 | `S` | extends `StateTree`

### _MapActionsObjectReturn

Ƭ _MapActionsObjectReturn<`A`, `T`>: { [key in keyof T]: A[T[key]] }

仅供内部使用

#### 类型参数

 | 名称 | 类型
 | `A` | `A`
 | `T` | extends `Record`<`string`, keyof `A`>

### _MapActionsReturn

Ƭ _MapActionsReturn<`A`>: { [key in keyof A]: A[key] }

仅供内部使用

#### 类型参数

 | 名称
 | `A`

### _MapStateObjectReturn

Ƭ _MapStateObjectReturn<`Id`, `S`, `G`, `A`, `T`>: { [key in keyof T]: Function }

仅供内部使用

#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `A` | `A`
 | `T` | extends `Record`<`string`, keyof `S` | keyof `G` | (`store`: `Store`<`Id`, `S`, `G`, `A`>) => `any`> = {}

### _MapStateReturn

Ƭ _MapStateReturn<`S`, `G`, `Keys`>: { [key in Keys]: Function }

仅供内部使用

#### 类型参数

 | 名称 | 类型
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `Keys` | extends keyof `S` | keyof `G` = keyof `S` | keyof `G`

### _MapWritableStateObjectReturn

Ƭ _MapWritableStateObjectReturn<`S`, `T`>: { [key in keyof T]: Object }

仅供内部使用

#### 类型参数

 | 名称 | 类型
 | `S` | extends `StateTree`
 | `T` | extends `Record`<`string`, keyof `S`>

### _MapWritableStateReturn

Ƭ _MapWritableStateReturn<`S`>: { [key in keyof S]: Object }

仅供内部使用

#### 类型参数

 | 名称 | Type
 | `S` | extends `StateTree`

### _Method

Ƭ _Method: (...`args`: `any`[]) => `any`

#### 类型声明

▸ (...`args`): `any`

可以推断参数和返回值类型的函数通用类型。

仅供内部使用

##### 参数

 | 名称 | 类型
 | `...args` | `any`[]

##### 返回值

`any`

### _Spread

Ƭ _Spread<`A`>: `A` extends [infer L, ...(infer R)] ? `_StoreObject`<`L`> & `_Spread`<`R`> : `unknown`

仅供内部使用.

#### 类型参数

 | 名称 | 类型
 | `A` | extends readonly `any`[]

### _StoreObject

Ƭ _StoreObject<`S`>: `S` extends `StoreDefinition`<infer Ids, infer State, infer Getters, infer Actions> ? { [Id in `${Ids}${MapStoresCustomization extends Record<"suffix", string> ? MapStoresCustomization["suffix"] : "Store"}`]: Function } : {}

仅供内部使用.

#### 类型参数

 | 名称
 | `S`

### _StoreWithActions

Ƭ _StoreWithActions<`A`>: { [k in keyof A]: A[k] extends Function ? Function : never }

为 action 增强的 Store。仅供内部使用。 仅供内部使用

#### 类型参数

 | 名称
 | `A`

### _StoreWithGetters

Ƭ _StoreWithGetters<`G`>: { readonly [k in keyof G]: G[k] extends Function ? R : UnwrapRef<G[k]> }

Store augmented with getters. For internal usage only. 仅供内部使用

#### 类型参数

 | 名称
 | `G`

### _UnwrapAll

Ƭ _UnwrapAll<`SS`>: { [K in keyof SS]: UnwrapRef<SS[K]> }

能够通过 IDE 进行重构的类型。 仅供内部使用

#### 类型参数

 | 名称
 | `SS`

## 变量

### PiniaVuePlugin

• PiniaVuePlugin: `Plugin`

Vue 2 插件，必须安装该插件才能使 pinia 工作。 注意，如果你使用的是 Nuxt，那你不需要这个插件。请使用 `buildModule` 代替： https://pinia.vuejs.org/zh/ssr/nuxt.html

`Example`

js

```
import Vue from 'vue'
import { PiniaVuePlugin, createPinia } from 'pinia'

Vue.use(PiniaVuePlugin)
const pinia = createPinia()

new Vue({
  el: '#app',
  // ...
  pinia,
})
```
`param`

从 'vue' 导入的 `Vue`。

## 函数

### acceptHMRUpdate

▸ acceptHMRUpdate(`initialUseStore`, `hot`): (`newModule`: `any`) => `any`

创建一个 accept 函数，在 Vite 应用中传递给 `import.meta.hot`。

`Example`

js

```
const useUser = defineStore(...)
if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useUser, import.meta.hot))
}
```
#### 参数

 | 名称 | 类型 | 描述
 | `initialUseStore` | `StoreDefinition`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`> | return of the defineStore to hot update
 | `hot` | `any` | `import.meta.hot`

#### 返回值

`fn`

▸ (`newModule`): `any`

##### 参数

 | 名称 | 类型
 | `newModule` | `any`

##### 返回值

`any`

### createPinia

▸ createPinia(): `Pinia`

创建一个 Pinia 实例，供应用使用。

#### 返回值

`Pinia`

### defineStore

▸ defineStore<`Id`, `S`, `G`, `A`>(`id`, `options`): `StoreDefinition`<`Id`, `S`, `G`, `A`>

创建一个 `useStore` 函数，检索 store 实例

#### 类型参数

 | 名称 | 类型
 | `Id` | 扩展自 `string`
 | `S` | 扩展自 `StateTree` = {}
 | `G` | 扩展自 `_GettersTree`<`S`> = {}
 | `A` | {}

#### 参数

 | 名称 | 类型 | 描述
 | `id` | `Id` | id of the store (must be unique)
 | `options` | `Omit`<`DefineStoreOptions`<`Id`, `S`, `G`, `A`>, `"id"`> | options to define the store

#### 返回值

`StoreDefinition`<`Id`, `S`, `G`, `A`>

▸ defineStore<`Id`, `S`, `G`, `A`>(`options`): `StoreDefinition`<`Id`, `S`, `G`, `A`>

创建一个 `useStore` 函数，检索 store 实例

#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree` = {}
 | `G` | extends `_GettersTree`<`S`> = {}
 | `A` | {}

#### 参数

 | 名称 | 类型 | 描述
 | `options` | `DefineStoreOptions`<`Id`, `S`, `G`, `A`> | options to define the store

#### 返回值

`StoreDefinition`<`Id`, `S`, `G`, `A`>

▸ defineStore<`Id`, `SS`>(`id`, `storeSetup`, `options?`): `StoreDefinition`<`Id`, `_ExtractStateFromSetupStore`<`SS`>, `_ExtractGettersFromSetupStore`<`SS`>, `_ExtractActionsFromSetupStore`<`SS`>>

创建一个 `useStore` 函数，检索 store 实例

#### 类型参数

 | 名称 | 类型
 | `Id` | 扩展自 `string`
 | `SS` | `SS`

#### 参数

 | 名称 | 类型 | 描述
 | `id` | `Id` | id of the store (must be unique)
 | `storeSetup` | () => `SS` | function that defines the store
 | `options?` | `DefineSetupStoreOptions`<`Id`, `_ExtractStateFromSetupStore`<`SS`>, `_ExtractGettersFromSetupStore`<`SS`>, `_ExtractActionsFromSetupStore`<`SS`>> | extra options

#### 返回值

`StoreDefinition`<`Id`, `_ExtractStateFromSetupStore`<`SS`>, `_ExtractGettersFromSetupStore`<`SS`>, `_ExtractActionsFromSetupStore`<`SS`>>

### getActivePinia

▸ getActivePinia(): `undefined` | `Pinia`

如果有的话，获取当前激活的 pinia

#### 返回值

`undefined` | `Pinia`

### mapActions

▸ mapActions<`Id`, `S`, `G`, `A`, `KeyMapper`>(`useStore`, `keyMapper`): `_MapActionsObjectReturn`<`A`, `KeyMapper`>

通过生成一个传递到组件的 `methods` 字段的对象， 允许直接使用 store 的 action，而不需要使用组合式 API(`setup()`)。 该对象的值是 action， 而键是产生的方法名称。

`Example`

js

```
export default {
  methods: {
    // 其他方法属性
    // useCounterStore 有两个 action，分别是 `increment` 与 `setCount`。
    ...mapActions(useCounterStore, { moar: 'increment', setIt: 'setCount' }),
  },

  created() {
    this.moar()
    this.setIt(2)
  },
}
```
#### 类型参数

 | 名称 | 类型
 | `Id` | 扩展自 `string`
 | `S` | 扩展自 `StateTree`
 | `G` | 扩展自 `_GettersTree`<`S`>
 | `A` | `A`
 | `KeyMapper` | 扩展自 `Record`<`string`, keyof `A`>

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keyMapper` | `KeyMapper` | object to define new names for the actions

#### 返回值

`_MapActionsObjectReturn`<`A`, `KeyMapper`>

▸ mapActions<`Id`, `S`, `G`, `A`>(`useStore`, `keys`): `_MapActionsReturn`<`A`>

允许直接使用 store 里的 action， 而不是必须使用组合式 API(`setup()`)， 通过生成一个对象，传递到组件的 `methods` 字段。

`Example`

js

```
export default {
  methods: {
    // 其他方法属性
    ...mapActions(useCounterStore, ['increment', 'setCount']),
  },

  created() {
    this.increment()
    this.setCount(2) // 像往常一样传递参数
  },
}
```
#### 类型参数

 | 名称 | 类型
 | `Id` | 扩展自 `string`
 | `S` | 扩展自 `StateTree`
 | `G` | 扩展自 `_GettersTree`<`S`>
 | `A` | `A`

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keys` | keyof `A`[] | array of action names to map

#### 返回值

`_MapActionsReturn`<`A`>

### mapGetters

▸ mapGetters<`Id`, `S`, `G`, `A`, `KeyMapper`>(`useStore`, `keyMapper`): `_MapStateObjectReturn`<`Id`, `S`, `G`, `A`, `KeyMapper`>

通过生成一个对象传递到组件的 `computed` 字段中。 允许使用来自一个 store 的 state 和 getter，而不必使用组合式 API(`setup()`)。 该对象的值是 state 属性/ getter 而键则是产生的计算属性的名称。 另外，你还可以传递一个自定义函数， 该函数将接收 state 的作为其第一个参数。 注意，虽然它可以通过 `this` 访问组件的实例，但它没有标注类型。

`Example`

js

```
export default {
  computed: {
    // 其他计算属性
    // useCounterStore 有一个名为 `count` 的 state 属性以及一个名为 `double` 的 getter
    ...mapState(useCounterStore, {
      n: 'count',
      triple: (store) => store.n * 3,
      // 注意如果你想要使用 `this`，那你不能使用箭头函数
      custom(store) {
        return this.someComponentValue + store.n
      },
      doubleN: 'double',
    }),
  },

  created() {
    this.n // 2
    this.doubleN // 4
  },
}
```
#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `A` | `A`
 | `KeyMapper` | extends `Record`<`string`, keyof `S` | keyof `G` | (`store`: `Store`<`Id`, `S`, `G`, `A`>) => `any`>

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keyMapper` | `KeyMapper` | object of state properties or getters

#### 返回值

`_MapStateObjectReturn`<`Id`, `S`, `G`, `A`, `KeyMapper`>

▸ mapGetters<`Id`, `S`, `G`, `A`, `Keys`>(`useStore`, `keys`): `_MapStateReturn`<`S`, `G`, `Keys`>

通过生成一个对象传递到组件的 `computed` 字段中， 以允许使用来自一个 store 的 state 和 getter， 而不必使用组合式 API(`setup()`)，

`Example`

js

```
export default {
  computed: {
    // 其他计算属性
    ...mapState(useCounterStore, ['count', 'double']),
  },

  created() {
    this.count // 2
    this.double // 4
  },
}
```
#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `A` | `A`
 | `Keys` | extends `string` | `number` | `symbol`

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keys` | readonly `Keys`[] | array of state properties or getters

#### 返回值

`_MapStateReturn`<`S`, `G`, `Keys`>

### mapState

▸ mapState<`Id`, `S`, `G`, `A`, `KeyMapper`>(`useStore`, `keyMapper`): `_MapStateObjectReturn`<`Id`, `S`, `G`, `A`, `KeyMapper`>

通过生成一个对象，并传递至组件的 `computed` 字段， 以允许在不使用组合式 API(`setup()`)的情况下使用一个 store 的 state 和 getter。 该对象的值是 state 属性/getter， 而键是生成的计算属性名称。 你也可以选择传递一个自定义函数，该函数将接收 store 作为其第一个参数。 注意，虽然它可以通过 `this` 访问组件实例，但它没有标注类型。

`Example`

js

```
export default {
  computed: {
    // 其他计算属性
    // useCounterStore 拥有一个名为 `count` 的 state 属性和一个名为 `double` 的 getter
    ...mapState(useCounterStore, {
      n: 'count',
      triple: (store) => store.n * 3,
      // 如果想使用 `this`，就不能使用箭头函数
      custom(store) {
        return this.someComponentValue + store.n
      },
      doubleN: 'double',
    }),
  },

  created() {
    this.n // 2
    this.doubleN // 4
  },
}
```
#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `A` | `A`
 | `KeyMapper` | extends `Record`<`string`, keyof `S` | keyof `G` | (`store`: `Store`<`Id`, `S`, `G`, `A`>) => `any`>

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keyMapper` | `KeyMapper` | object of state properties or getters

#### 返回值

`_MapStateObjectReturn`<`Id`, `S`, `G`, `A`, `KeyMapper`>

▸ mapState<`Id`, `S`, `G`, `A`, `Keys`>(`useStore`, `keys`): `_MapStateReturn`<`S`, `G`, `Keys`>

通过生成一个对象，并传递到组件的 `computed` 字段， 允许在不使用组合式 API(`setup()`)的情况下 使用一个 store 的 state 和 getter，

`Example`

js

```
export default {
  computed: {
    // 其他计算属性
    ...mapState(useCounterStore, ['count', 'double']),
  },

  created() {
    this.count // 2
    this.double // 4
  },
}
```
#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `A` | `A`
 | `Keys` | extends `string` | `number` | `symbol`

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keys` | readonly `Keys`[] | array of state properties or getters

#### 返回值

`_MapStateReturn`<`S`, `G`, `Keys`>

### mapStores

▸ mapStores<`Stores`>(...`stores`): `_Spread`<`Stores`>

通过生成一个对象，传递到组件的 `computed` 字段 以允许在不使用组合式 API(`setup()`)的情况下使用 store。 它接受一个 store 定义的列表参数。

`Example`

js

```
export default {
  computed: {
    // 其他计算属性
    ...mapStores(useUserStore, useCartStore),
  },

  created() {
    this.userStore // id 为 "user" 的 store
    this.cartStore // id 为 "cart" 的 store
  },
}
```
#### 类型参数

 | 名称 | 类型
 | `Stores` | 扩展 `any`[]

#### 参数

 | 名称 | 类型 | 描述
 | `...stores` | [...Stores[]] | list of stores to map to an object

#### 返回值

`_Spread`<`Stores`>

### mapWritableState

▸ mapWritableState<`Id`, `S`, `G`, `A`, `KeyMapper`>(`useStore`, `keyMapper`): `_MapWritableStateObjectReturn`<`S`, `KeyMapper`>

除了创建的计算属性的 setter，其他与 `mapState()` 相同， 所以 state 可以被修改。 与 `mapState()` 不同的是，只有 `state` 属性可以被添加。

#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `A` | `A`
 | `KeyMapper` | extends `Record`<`string`, keyof `S`>

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keyMapper` | `KeyMapper` | object of state properties

#### 返回值

`_MapWritableStateObjectReturn`<`S`, `KeyMapper`>

▸ mapWritableState<`Id`, `S`, `G`, `A`>(`useStore`, `keys`): `_MapWritableStateReturn`<`S`>

通过生成一个对象并传递到组件的 `computed` 字段 以允许在不使用组合式 API(`setup()`)的情况下 使用来自一个 store 的 state 和 getter，。

#### 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | extends `_GettersTree`<`S`>
 | `A` | `A`

#### 参数

 | 名称 | 类型 | 描述
 | `useStore` | `StoreDefinition`<`Id`, `S`, `G`, `A`> | store to map from
 | `keys` | keyof `S`[] | array of state properties

#### 返回值

`_MapWritableStateReturn`<`S`>

### setActivePinia

▸ setActivePinia(`pinia`): `undefined` | `Pinia`

设置或取消设置激活的 pinia。 在 SSR 和内部调用 action 和 getter 时使用。

#### 参数

 | 名称 | 类型 | 描述
 | `pinia` | `undefined` | `Pinia` | Pinia instance

#### 返回值

`undefined` | `Pinia`

### setMapStoreSuffix

▸ setMapStoreSuffix(`suffix`): `void`

改变由 `mapStores()` 添加的后缀。可以设置为空字符串。 默认为`"Store"`。如果你需要使用 TypeScript， 请确保扩展 MapStoresCustomization 接口。

#### 参数

 | 名称 | 类型 | 描述
 | `suffix` | `string` | new suffix

#### 返回值

`void`

### skipHydrate

▸ skipHydrate<`T`>(`obj`): `T`

告诉 Pinia 跳过给定对象的激活过程。当你在 store 中返回一个有状态的对象，但它并不是真正的状态时，(仅)在 setup store 中这是很有用的。 例如，在一个 setup store 中返回一个路由器实例。

#### 类型参数

 | 名称 | 类型
 | `T` | `any`

#### 参数

 | 名称 | 类型 | 描述
 | `obj` | `T` | target object

#### 返回值

`T`

obj

### storeToRefs

▸ storeToRefs<`SS`>(`store`): `ToRefs`<`StoreState`<`SS`> & `StoreGetters`<`SS`> & `PiniaCustomStateProperties`<`StoreState`<`SS`>>>

创建一个引用对象，包含 store 的所有 state、 getter 和 plugin 添加的 state 属性。 类似于 `toRefs()`，但专门为 Pinia store 设计， 所以 method 和非响应式属性会被完全忽略。

#### 类型参数

 | 名称 | 类型
 | `SS` | extends `_StoreWithState`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`, `SS`> & `StateTree` & `_StoreWithGetters`<`_GettersTree`<`StateTree`>> & `PiniaCustomProperties`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`, `SS`> & `PiniaCustomStateProperties`<`StateTree`, `SS`>

#### 参数

 | 名称 | 类型 | 描述
 | `store` | `SS` | store to extract the refs from

#### 返回值

`ToRefs`<`StoreState`<`SS`> & `StoreGetters`<`SS`> & `PiniaCustomStateProperties`<`StoreState`<`SS`>>>
