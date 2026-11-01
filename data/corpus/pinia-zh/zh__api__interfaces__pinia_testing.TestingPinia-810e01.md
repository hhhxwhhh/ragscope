> 来源：https://pinia.vuejs.org/zh/api/interfaces/pinia_testing.TestingPinia.html

# 接口：TestingPinia

@pinia/testing.TestingPinia

专门为测试设计的 Pinia 实例。 用测试中的特定属性扩展普通的 Pinia 实例。

## 层次结构

- `Pinia`
↳ `TestingPinia`

## 属性

### app

• app: `App`<`any`>

Pinia 使用的应用

### 安装

• install: (`app`: `App`<`any`>) => `void`

#### 类型声明

▸ (`app`): `void`

##### 参数

 | 名称 | 类型
 | `app` | `App`<`any`>

##### 返回值

`void`

#### 继承于

Pinia.install

### state

• state: `Ref`<`Record`<`string`, `StateTree`>>

根 state

#### 继承于

Pinia.state

## 方法

### use

▸ use(`plugin`): `Pinia`

增加了一个 store 插件来扩展每个 store

#### 参数

 | 名称 | 类型 | 描述
 | `plugin` | `PiniaPlugin` | store plugin to add

#### 返回值

`Pinia`

#### 继承于

Pinia.use
