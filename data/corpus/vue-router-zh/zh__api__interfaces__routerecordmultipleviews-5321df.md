> 来源：https://router.vuejs.org/zh/api/interfaces/routerecordmultipleviews

# Interface: RouteRecordMultipleViews

Route Record defining multiple named components with the `components` option.

## Hierarchy

- `_RouteRecordBase`
↳ `RouteRecordMultipleViews`

## Properties

### alias

• `Optional` alias: `string` | `string`[]

Aliases for the record. Allows defining extra paths that will behave like a copy of the record. Allows having paths shorthands like `/users/:id` and `/u/:id`. All `alias` and `path` values must share the same params.

#### Inherited from

_RouteRecordBase.alias

### beforeEnter

• `Optional` beforeEnter: `NavigationGuardWithThis`<`undefined`> | `NavigationGuardWithThis`<`undefined`>[]

Before Enter guard specific to this record. Note `beforeEnter` has no effect if the record has a `redirect` property.

#### Inherited from

_RouteRecordBase.beforeEnter

### children

• `Optional` children: `undefined`

Array of nested routes.

#### Overrides

_RouteRecordBase.children

### component

• `Optional` component: `undefined`

### components

• components: `Record`<`string`, `RawRouteComponent`>

Components to display when the URL matches this route. Allow using named views.

### end

• `Optional` end: `boolean`

Should the RegExp match until the end by appending a `$` to it.

`Default Value`

`true`

#### Inherited from

_RouteRecordBase.end

### meta

• `Optional` meta: `RouteMeta`

Arbitrary data attached to the record.

#### Inherited from

_RouteRecordBase.meta

### name

• `Optional` name: `RouteRecordName`

Name for the route record. Must be unique.

#### Inherited from

_RouteRecordBase.name

### path

• path: `string`

Path of the record. Should start with `/` unless the record is the child of another record.

`Example`

ts

```
`/users/:id` matches `/users/1` as well as `/users/posva`.
```
#### Inherited from

_RouteRecordBase.path

### props

• `Optional` props: `boolean` | `Record`<`string`, `_RouteRecordProps`>

Allow passing down params as props to the component rendered by `router-view`. Should be an object with the same keys as `components` or a boolean to be applied to every component.

#### Overrides

_RouteRecordBase.props

### redirect

• `Optional` redirect: `undefined`

Where to redirect if the route is directly matched. The redirection happens before any navigation guard and triggers a new navigation with the new target location.

#### Overrides

_RouteRecordBase.redirect

### sensitive

• `Optional` sensitive: `boolean`

Makes the RegExp case-sensitive.

`Default Value`

`false`

#### Inherited from

_RouteRecordBase.sensitive

### strict

• `Optional` strict: `boolean`

Whether to disallow a trailing slash or not.

`Default Value`

`false`

#### Inherited from

_RouteRecordBase.strict
