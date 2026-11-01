> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia._StoreOnActionListenerContext.html

# 接口：_StoreOnActionListenerContext<Store, ActionName, A>

pinia._StoreOnActionListenerContext

StoreOnActionListenerContext的实际类型。 存在的目的是重构。仅供内部使用。 仅供内部使用

## 类型参数

 | 名称 | 类型
 | `Store` | `Store`
 | `ActionName` | extends `string`
 | `A` | `A`

## 属性

### after

• after: (`callback`: `A` extends `Record`<`ActionName`, `_Method`> ? (`resolvedReturn`: `_Awaited`<`ReturnType`<`A`[`ActionName`]>>) => `void` : () => `void`) => `void`

#### 类型声明

▸ (`callback`): `void`

action 执行完的钩子。 它接收 action 的返回值，如果是 Promise，它将被自动解包。

##### 参数

 | 名称 | 类型
 | `callback` | `A` extends `Record`<`ActionName`, `_Method`> ? (`resolvedReturn`: `_Awaited`<`ReturnType`<`A`[`ActionName`]>>) => `void` : () => `void`

##### 返回值

`void`

### args

• args: `A` extends `Record`<`ActionName`, `_Method`> ? `Parameters`<`A`[`ActionName`]> : `unknown`[]

传递给 action 的参数

### name

• name: `ActionName`

action 的名称

### onError

• onError: (`callback`: (`error`: `unknown`) => `void`) => `void`

#### 类型声明

▸ (`callback`): `void`

action 的错误钩子。 返回 `false` 以捕获错误并阻止其继续传播。

##### 参数

 | 名称 | 类型
 | `callback` | (`error`: `unknown`) => `void`

##### 返回值

`void`

### store

• store: `Store`

正在调用 action 的 Store
