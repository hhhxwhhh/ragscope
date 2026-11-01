> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.DefineStoreOptionsBase.html

# 接口：DefineStoreOptionsBase<S, Store>

pinia.DefineStoreOptionsBase

传递给 `defineStore()` 的选项，在 option store 和 setup store 之间是通用的。 如果你想为这两种 store 添加自定义的选项， 请扩展这个接口。

## 类型参数

 | Name | Type
 | `S` | extends `StateTree`
 | `Store` | `Store`

## 层次结构

- `DefineStoreOptionsBase`
↳ `DefineStoreOptions`
↳ `DefineSetupStoreOptions`
