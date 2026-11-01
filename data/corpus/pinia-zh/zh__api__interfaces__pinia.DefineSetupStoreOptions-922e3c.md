> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.DefineSetupStoreOptions.html

# 接口：DefineSetupStoreOptions<Id, S, G, A>

pinia.DefineSetupStoreOptions

`defineStore()` 的选项参数，用于设置 store。 可以通过插件 API 扩展来增强 store 的功能。

`See`

DefineStoreOptionsBase.

## 类型参数

 | 名称 | 类型
 | `Id` | extends `string`
 | `S` | extends `StateTree`
 | `G` | `G`
 | `A` | `A`

## 层次结构

- `DefineStoreOptionsBase`<`S`, `Store`<`Id`, `S`, `G`, `A`>>
↳ `DefineSetupStoreOptions`

## 属性

### actions

• `Optional` actions: `A`

提取的 action。由 useStore() 添加。不应该由用户在创建 store 时添加。 可以在插件中使用，以获得用 setup 函数定义的 store 中的 action 列表。 注意这个属性一定是会定义的。
