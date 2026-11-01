> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.SubscriptionCallbackMutationPatchObject.html

# 接口：SubscriptionCallbackMutationPatchObject<S>

pinia.SubscriptionCallbackMutationPatchObject

当 `store.$patch()` 与一个对象一起被调用时， 传递给订阅回调的上下文。

## 类型参数

 | 名称
 | `S`

## 层次结构

- `_SubscriptionCallbackMutationBase`
↳ `SubscriptionCallbackMutationPatchObject`

## 属性

### 事件

• events: `DebuggerEvent`[]

仅限 DEV， patch 调用的数组。

### payload

• payload: `_DeepPartial`<`S`>

传递给 `store.$patch()` 的对象

### storeId

• storeId: `string`

执行 mutation 的 store 的 `id`

#### 继承于

_SubscriptionCallbackMutationBase.storeId

### 类型

• type: `patchObject`

mutation 的类型

#### 重写

_SubscriptionCallbackMutationBase.type
