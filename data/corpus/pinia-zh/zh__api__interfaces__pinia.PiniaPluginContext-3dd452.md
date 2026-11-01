> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.PiniaPluginContext.html

# 接口：PiniaPluginContext<Id, S, G, A>

pinia.PiniaPluginContext

传递给 Pinia 插件的上下文参数。

## 类型参数

 | Name | Type
 | `Id` | extends `string` = `string`
 | `S` | extends `StateTree` = `StateTree`
 | `G` | `_GettersTree`<`S`>
 | `A` | `_ActionsTree`

## 属性

### app

• app: `App`<`any`>

用 `Vue.createApp()`创建的当前应用。

### options

• options: `DefineStoreOptionsInPlugin`<`Id`, `S`, `G`, `A`>

调用 `defineStore()` 时定义 store 的初始选项。

### pinia

• pinia: `Pinia`

pinia 实例

### store

• store: `Store`<`Id`, `S`, `G`, `A`>

目前正在扩展的 store
