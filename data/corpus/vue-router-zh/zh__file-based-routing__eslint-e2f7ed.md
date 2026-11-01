> 来源：https://router.vuejs.org/zh/file-based-routing/eslint.html

# ESLint

如果你不使用自动导入，你需要告诉 ESLint 关于 `vue-router/auto-routes` 的信息。将这些行添加到你的 eslint 配置中：

json

```
{
  "settings": {
    "import/core-modules": ["vue-router/auto-routes"]
  }
}
```
## `definePage()`

由于 `definePage()` 是一个全局宏，你需要告诉 ESLint 关于它的信息。将这些行添加到你的 eslint 配置中：

json

```
{
  "globals": {
    "definePage": "readonly"
  }
}
```
