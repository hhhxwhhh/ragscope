> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.StoreProperties.html

# 接口：StoreProperties<Id>

pinia.StoreProperties

store 的属性。

## 类型参数

 | 名称 | 类型
 | `Id` | extends `string`

## 层次结构

- `StoreProperties`
↳ `_StoreWithState`

## 属性

### $id

• $id: `Id`

store 的唯一标识符

### _customProperties

• _customProperties: `Set`<`string`>

供 devtools 插件使用，用于检索插件添加的属性。 在生产环境中会被移除。 开发者可用于添加应在 devtools 中显示的 store 的属性键。
