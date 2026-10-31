> 来源：https://cn.vite.dev/guide/static-deploy

# 部署静态站点

本指南建立在以下几个假设基础之上：

- 你正在使用的是默认的构建输出路径（`dist`）。这个路径 可以通过 `build.outDir` 更改，在这种情况下，你可以从这篇指南中找到所需的指引。
- 你正在使用 NPM；或者 Yarn 等其他可以运行下面的脚本指令的包管理工具。
- Vite 已作为一个本地开发依赖（dev dependency）安装在你的项目中，并且你已经配置好了如下的 npm scripts：

package.json

json

```
{
  "scripts": {
    "build": "vite build",
    "preview": "vite preview"
  }
}
```
值得注意的是 `vite preview` 用作预览本地构建，而不应直接作为生产服务器。

注意

本篇指南提供了部署 Vite 静态站点的说明。Vite 也对服务端渲染（SSR）有了实验性的支持。SSR 是指支持在 Node 中运行相应应用的前端框架，预渲染成 HTML，最后在客户端激活（hydrate）。查看 SSR 指南 了解更多细节。另一方面，如果你在寻找与传统服务端框架集成的方式，那么请查看 后端集成 章节。

## 构建应用

你可以运行 `npm run build` 命令来执行应用的构建。

bash

```
$ npm run build
```
默认情况下，构建会输出到 `dist` 文件夹中。你可以部署这个 `dist` 文件夹到任何你喜欢的平台。

### 本地测试应用

当你构建完成应用后，你可以通过运行 `npm run preview` 命令，在本地测试该应用。

bash

```
$ npm run preview
```
`vite preview` 命令会在本地启动一个静态 Web 服务器，将 `dist` 文件夹运行在 `http://localhost:4173`。这样在本地环境下查看该构建产物是否正常可用就方便多了。

你可以通过 `--port` 参数来配置服务的运行端口。

package.json

json

```
{
  "scripts": {
    "preview": "vite preview --port 8080"
  }
}
```
现在 `preview` 命令会将服务器运行在 `http://localhost:8080`。

## GitHub Pages

- 更新 Vite 配置
在 `vite.config.js` 中设置正确的 `base`。
如果你正要部署到 `https://<USERNAME>.github.io/`，或者通过 GitHub Pages 部署到一个自定义域名（例如 `www.example.com`），请将 `base` 设置为 `'/'`。或者，你也可以从配置中移除 `base`，因为它默认为 `'/'`。
如果你正在部署到 `https://<USERNAME>.github.io/<REPO>/`（例如你的仓库地址为 `https://github.com/<USERNAME>/<REPO>`），那么请将 `base` 设置为 `'/<REPO>/'`。
- 启用 GitHub Pages
在你的仓库中，进入 Settings → Pages。在 Build and deployment 下，打开 Source 下拉菜单，然后选择 GitHub Actions。
GitHub 现在将使用 GitHub Actions 工作流 来部署你的网站，这是必要的，因为 Vite 需要构建步骤来进行部署。
- 创建工作流
在你的仓库中创建一个新文件 `.github/workflows/deploy.yml`。你也可以从上一步点击 "create your own"，这将为你生成一个起始工作流程文件。
这里有一个示例工作流程，它使用 npm 安装依赖项，构建网站，并在你向 `main` 分支推送更改时部署它：
.github/workflows/deploy.yml
yaml

```
# 一个简单的 GitHub Pages 部署工作流
name: Deploy static content to Pages

on:
  # 仅在推送到默认分支时运行
  push:
    branches: ['main']

  # 这个选项可以使你手动在 Action tab 页面触发工作流
  workflow_dispatch:

# 设置 GITHUB_TOKEN 的权限，以允许部署到 GitHub Pages
permissions:
  contents: read
  pages: write
  id-token: write

# 允许一个并发部署
concurrency:
  group: 'pages'
  cancel-in-progress: true

jobs:
  # 单次部署的工作描述
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - name: Set up Node
        uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7
        with:
          node-version: lts/*
          cache: 'npm'
      - name: Install dependencies
        run: npm ci
      - name: Build
        run: npm run build
      - name: Setup Pages
        uses: actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d # v6
      - name: Upload artifact
        uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5
        with:
          # 上传 dist 文件夹
          path: './dist'
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346 # v5
```
## GitLab Pages 配合 GitLab CI

- 在 `vite.config.js` 中设置正确的 `base`。
如果你要部署在 `https://<USERNAME or GROUP>.gitlab.io/` 上，你可以省略 `base` 使其默认为 `'/'`。
如果你要部署在 `https://<USERNAME or GROUP>.gitlab.io/<REPO>/` 上，例如你的仓库地址为 `https://gitlab.com/<USERNAME>/<REPO>`，那么请设置 `base` 为 `'/<REPO>/'`。
- 在项目根目录创建一个 `.gitlab-ci.yml` 文件，并包含以下内容。它将使得每次你更改内容时都重新构建与部署站点：
.gitlab-ci.yml
yaml

```
image: node:lts
pages:
  stage: deploy
  cache:
    key:
      files:
        - package-lock.json
      prefix: npm
    paths:
      - node_modules/
  script:
    - npm install
    - npm run build
    - cp -a dist/. public/
  artifacts:
    paths:
      - public
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```
## Netlify

### Netlify CLI

- 通过 `npm install -g netlify-cli` 安装 Netlify CLI。
- 使用 `netlify init` 创建一个新站点。
- 使用 `netlify deploy` 来部署。

Netlify 命令行工具（CLI）会为你提供一个预览链接，供你查看效果。当你准备发布到生产环境时，可使用 `prod` 参数执行命令：`netlify deploy --prod`。

### Netlify with Git

- 将你的代码推送到 git 仓库（GitHub、GitLab、BitBucket 或是 Azure DevOps 等服务）
- 在 Netlify 中 导入该项目
- 选择分支，输出目录，如果需要还可以设置环境变量。
- 点击 部署
- 你的 Vite 应用就部署完成了！

在你的项目被导入和部署后，所有对生产分支以外的其他分支（可能来自合并请求）的后续推送都会生成 预览部署，所有对生产分支（通常是 "main"）的更改都会生成一个 生产部署。

## Vercel

### Vercel CLI

- 通过 `npm i -g vercel` 命令安装 Vercel CLI，然后运行 vercel 命令完成部署。
- Vercel 会检测到你正在使用 Vite，并会为你开启相应的正确配置。
- 你的应用被部署好了！（示例：vite-vue-template.vercel.app）

### Vercel for Git

- 将你的代码推送到远程仓库（GitHub，GitLab，Bitbucket）
- 导入你的 Vite 仓库 到 Vercel
- Vercel 会检测到你正在使用 Vite，并会为你的部署开启相应的正确配置。
- 你的应用被部署好了！（示例：vite-vue-template.vercel.app）

在你的项目被导入和部署后，所有对分支的后续推送都会生成 预览部署，而所有对生产分支（通常是 "main"）的更改都会生成一次 生产部署。

查看 Vercel 的 Git 集成 了解更多细节。

## Cloudflare

### Cloudflare Workers

Cloudflare Vite 插件 集成了 Cloudflare Workers，通过 Vite 的 Environment API，让你的服务端代码在开发时运行于 Cloudflare Workers 运行时环境中。

要在现有 Vite 项目中添加 Cloudflare Workers，请安装插件并将其添加到配置中：

bash

```
$ npm install --save-dev @cloudflare/vite-plugin
```
vite.config.js

js

```
import { defineConfig } from 'vite'
import { cloudflare } from '@cloudflare/vite-plugin'

export default defineConfig({
  plugins: [cloudflare()],
})
```
wrangler.jsonc

jsonc

```
{
  "name": "my-vite-app",
}
```
运行 `npm run build` 后，你的应用就可以通过 `npx wrangler deploy` 进行部署。

你还可以轻松地为 Vite 应用添加后端 API，用于与 Cloudflare 资源进行安全通信。开发时，这些 API 运行在 Workers 运行时环境中，部署时会随前端代码一同发布。详细步骤请查看 Cloudflare Vite 插件教程。

### Cloudflare Pages

#### Cloudflare Pages 与 Git

Cloudflare Pages 提供了一种直接部署到 Cloudflare 的方式，而无需管理 Wrangler 文件。

- 将你的代码推送到你的 Git 仓库（GitHub, GitLab）。
- 登录 Cloudflare 控制台，在 Account Home > Workers & Pages 下选择你的账号。
- 选择 Create a new Project 和 Pages 选项，然后选择 Git。
- 选择你想要部署的 Git 项目，然后点击 Begin setup。
- 根据你所选择的 Vite 框架，在构建设置中选择相应的框架预设。否则为你的项目输入构建命令和预期的输出目录。
- 然后保存并部署！
- 你的应用就部署完成了！（例如：`https://<PROJECTNAME>.pages.dev/`）

在你的项目被导入和部署后，所有对分支的后续推送都会生成 预览部署，除非你特意在 分支构建控制 中写明不触发。所有对 生产分支（通常是 "main"）的更改都会生成一个 生产部署。

你也可以添加自定义域名，并自定义各个页面的构建设置。查看 Cloudflare Pages Git 集成 了解更多详情。

## Google Firebase

- 通过 `npm i -g firebase-tools` 命令安装 firebase-tools 工具。
- 在你的项目根目录下创建以下文件：

firebase.json.firebaserc

json

```
{
  "hosting": {
    "public": "dist",
    "ignore": [],
    "rewrites": [
      {
        "source": "**",
        "destination": "/index.html"
      }
    ]
  }
}
```
js

```
{
  "projects": {
    "default": "<YOUR_FIREBASE_ID>"
  }
}
```
:::

- 运行 `npm run build` 后，通过 `firebase deploy` 命令部署。

## Surge

- 通过 `npm i -g surge` 命令安装 surge。
- 运行 `npm run build`。
- 运行 `surge dist` 命令部署到 surge。

你也可以通过添加 `surge dist yourdomain.com` 部署到一个 自定义域名。

## Azure 的静态网站应用

你可以通过微软 Azure 的 静态网站应用 服务来快速部署你的 Vite 应用。你只需：

- 注册 Azure 账号并获取一个订阅（subscription）的 key。可以在 此处快速完成注册。
- 将你的应用代码托管到 GitHub。
- 在 VSCode 中安装 SWA 扩展。

安装完此扩展后，进入你应用的根目录。打开 SWA 的扩展程序，登录 Azure，并点击 '+'，来创建一个全新的 SWA。系统会提示你指定所需的订阅 key。

按照扩展程序的启动向导，给你的应用程序起个名字，选择框架预设，并指定应用程序的根目录（通常为 `/`）以及构建文件的路径 `/dist`。此向导完成后，会在你的 repo 中的 `.github` 文件夹中创建一个 GitHub Action。

这个 action 致力于部署你的应用程序（可以在仓库的 Actions 标签中，查看相关进度），成功完成后，你可以点击 GitHub 中出现的 “浏览站点” 的按钮，查看你的应用程序。

## Render

你可以在 Render 部署你的 Vite 应用。

- 创建一个 Render 账号
- 在 控制台 页面点击 New 按钮并选择 Static Site。
- 链接你的 GitHub/GitLab 账号或使用一个公共仓库
- 指定一个项目名称和所用分支
- 构建命令：`npm install && npm run build`
- 发布目录：`dist`
- 点击 Create Static Site，你的应用将会被部署在 `https://<PROJECTNAME>.onrender.com/`。

默认情况下，推送到该指定分支的任何新提交都会自动触发新的部署。可以在项目设置中配置 自动部署。

还可以为项目添加一个 自定义域名。

## Flightcontrol

根据 说明，使用 Flightcontrol 来部署你的静态站点。

## xmit 静态站点托管

根据 指南，使用 xmit 部署你的静态站点。

## Zephyr Cloud

Zephyr Cloud 是一个部署平台，它直接集成到你的构建过程中，并为模块联邦和其他类型的应用程序提供全球边缘分发。

Zephyr 与其他云提供商采用不同的方法。它直接与 Vite 构建流程集成，因此每次你构建或运行应用程序的开发服务器时，它都会自动部署到 Zephyr Cloud。

请按照 Vite 部署指南 中的步骤开始使用。

## EdgeOne Pages

通过遵循这些 说明，使用 EdgeOne Pages 部署你的静态站点。
