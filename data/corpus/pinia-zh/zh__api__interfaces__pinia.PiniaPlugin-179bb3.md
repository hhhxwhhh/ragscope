> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.PiniaPlugin.html

# 接口：PiniaPlugin

pinia.PiniaPlugin

## Callable

### PiniaPlugin

▸ PiniaPlugin(`context`): `void` | `Partial`<`PiniaCustomProperties`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`> & `PiniaCustomStateProperties`<`StateTree`>>

用于扩展每个 store 的插件。返回一个扩展 store 的对象或 没有返回值。

#### 参数

 | 名称 | 类型 | 描述
 | `context` | `PiniaPluginContext`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`> | Context

#### 返回值

`void` | `Partial`<`PiniaCustomProperties`<`string`, `StateTree`, `_GettersTree`<`StateTree`>, `_ActionsTree`> & `PiniaCustomStateProperties`<`StateTree`>>
