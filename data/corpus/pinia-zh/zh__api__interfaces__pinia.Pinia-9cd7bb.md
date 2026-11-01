> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia.Pinia.html

# 接口：Pinia

pinia.Pinia

Every application must own its own pinia to be able to create stores

## 层次结构

- `Pinia`
↳ `TestingPinia`

## 属性

### 安装

• install: (`app`: `App`<`any`>) => `void`

#### 类型声明

▸ (`app`): `void`

##### 参数

 | Name | Type
 | `app` | `App`<`any`>

##### 返回值

`void`

### state

• state: `Ref`<`Record`<`string`, `StateTree`>>

根 state

## 方法

### use

▸ use(`plugin`): `Pinia`

添加 store 插件来扩展每一个 store

#### 参数

 | Name | Type | Description
 | `plugin` | `PiniaPlugin` | store plugin to add

#### 返回值

`Pinia`
