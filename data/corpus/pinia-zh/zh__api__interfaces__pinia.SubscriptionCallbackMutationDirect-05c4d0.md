> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.SubscriptionCallbackMutationDirect.html

# 接口：SubscriptionCallbackMutationDirect

pinia.SubscriptionCallbackMutationDirect

当用 `store.someState = newValue` 或 `store.$state.someState = newValue` 直接改变 store 的状态时， 传递给订阅回调的上下文。

## 层次结构

- `_SubscriptionCallbackMutationBase`
↳ `SubscriptionCallbackMutationDirect`

## 属性

### 事件

• events: `DebuggerEvent`

只支持开发环境。不同的 mutation 调用。

### storeId

• storeId: `string`

执行 mutation 的 store 的 `id`

#### 继承于

_SubscriptionCallbackMutationBase.storeId

### 类型

• type: `direct`

mutation 的类型

#### 重写

_SubscriptionCallbackMutationBase.type
