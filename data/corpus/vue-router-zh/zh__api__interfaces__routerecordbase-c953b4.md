> 来源：https://router.vuejs.org/zh/api/interfaces/routerecordbase

# Interface: _RouteRecordBase

Internal type for common properties among all kind of RouteRecordRaw.

## Hierarchy

- `PathParserOptions`
↳ `_RouteRecordBase`
↳↳ `RouteRecordSingleView`
↳↳ `RouteRecordSingleViewWithChildren`
↳↳ `RouteRecordMultipleViews`
↳↳ `RouteRecordMultipleViewsWithChildren`
↳↳ `RouteRecordRedirect`

## Properties

### alias

• `Optional` alias: `string` | `string`[]

Aliases for the record. Allows defining extra paths that will behave like a copy of the record. Allows having paths shorthands like `/users/:id` and `/u/:id`. All `alias` and `path` values must share the same params.

### beforeEnter

• `Optional` beforeEnter: `NavigationGuardWithThis`<`undefined`> | `NavigationGuardWithThis`<`undefined`>[]

Before Enter guard specific to this record. Note `beforeEnter` has no effect if the record has a `redirect` property.

### children

• `Optional` children: `RouteRecordRaw`[]

Array of nested routes.

### end

• `Optional` end: `boolean`

Should the RegExp match until the end by appending a `$` to it.

`Default Value`

`true`

#### Inherited from

PathParserOptions.end

### meta

• `Optional` meta: `RouteMeta`

Arbitrary data attached to the record.

### name

• `Optional` name: `RouteRecordName`

Name for the route record. Must be unique.

### path

• path: `string`

Path of the record. Should start with `/` unless the record is the child of another record.

`Example`

ts

```
`/users/:id` matches `/users/1` as well as `/users/posva`.
```
### props

• `Optional` props: `_RouteRecordProps` | `Record`<`string`, `_RouteRecordProps`>

Allow passing down params as props to the component rendered by `router-view`.

### redirect

• `Optional` redirect: `RouteRecordRedirectOption`

Where to redirect if the route is directly matched. The redirection happens before any navigation guard and triggers a new navigation with the new target location.

### sensitive

• `Optional` sensitive: `boolean`

Makes the RegExp case-sensitive.

`Default Value`

`false`

#### Inherited from

PathParserOptions.sensitive

### strict

• `Optional` strict: `boolean`

Whether to disallow a trailing slash or not.

`Default Value`

`false`

#### Inherited from

PathParserOptions.strict
