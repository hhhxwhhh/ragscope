> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.SubscriptionCallbackMutationPatchFunction.html

# 接口：SubscriptionCallbackMutationPatchFunction

pinia.SubscriptionCallbackMutationPatchFunction

当 `store.$patch()` 被一个函数调用时， 传递给订阅回调的上下文。

## 层次结构

- `_SubscriptionCallbackMutationBase`
↳ `SubscriptionCallbackMutationPatchFunction`

## 属性

### 事件

• events: `DebuggerEvent`[]

仅限开发环境。在回调中所有已完成的 mutation 的数组。

### storeId

• storeId: `string`

执行 mutation 的 store 的 `id`

#### 继承于

_SubscriptionCallbackMutationBase.storeId

### 类型

• type: `patchFunction`

mutation 的类型

#### 重写

_SubscriptionCallbackMutationBase.type
