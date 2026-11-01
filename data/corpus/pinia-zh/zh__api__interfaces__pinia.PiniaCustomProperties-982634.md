> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.PiniaCustomProperties.html

# 接口：PiniaCustomProperties<Id, S, G, A>

pinia.PiniaCustomProperties

当用户通过插件添加属性时，接口可被扩展。

## 类型参数

 | 名称 | 类型
 | `Id` | extends `string` = `string`
 | `S` | extends `StateTree` = `StateTree`
 | `G` | `_GettersTree`<`S`>
 | `A` | `_ActionsTree`

## Accessors

### route

• `get` route(): `RouteLocationNormalized`

#### 返回值

`RouteLocationNormalized`

• `set` route(`value`): `void`

#### 参数

 | Name | Type
 | `value` | `RouteLocationNormalizedLoaded` | `Ref`<`RouteLocationNormalizedLoaded`>

#### 返回值

`void`
