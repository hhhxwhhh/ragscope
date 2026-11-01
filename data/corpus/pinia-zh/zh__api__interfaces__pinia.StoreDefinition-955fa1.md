> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.StoreDefinition.html

# 接口：StoreDefinition<Id, S, G, A>

pinia.StoreDefinition

## 类型参数

 | 名称 | 类型
 | `Id` | extends `string` = `string`
 | `S` | extends `StateTree` = `StateTree`
 | `G` | `_GettersTree`<`S`>
 | `A` | `_ActionsTree`

## Callable

### StoreDefinition

▸ StoreDefinition(`pinia?`, `hot?`): `Store`<`Id`, `S`, `G`, `A`>

返回一个 store，有需要才创建它。

#### 参数

 | 名称 | 类型 | 描述
 | `pinia?` | `null` | `Pinia` | Pinia instance to retrieve the store
 | `hot?` | `StoreGeneric` | dev only hot module replacement

#### 返回值

`Store`<`Id`, `S`, `G`, `A`>

## 属性

### $id

• $id: `Id`

store 的 id。供映射辅助函数使用。
