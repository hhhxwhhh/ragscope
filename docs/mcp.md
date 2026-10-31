# MCP：把知识库变成任何 AI 客户端的工具

MCP（Model Context Protocol）是 Anthropic 提出的工具协议，本质是 JSON-RPC 2.0 的
一次握手 + 工具描述 + 工具调用。RAGScope 扮演 **MCP Server**：Claude Desktop、
Cursor、Codex 这类客户端连上之后，就能直接检索并使用你上传的文档，而不需要你再写
一遍 RAG。

## 暴露的工具

| 工具 | 作用 | 需要的 scope |
|---|---|---|
| `list_knowledge_bases` | 列出可访问的知识库与 id | `kb:read` |
| `search_knowledge_base` | 混合检索，返回命中片段（含 chunk_id / 页码 / 分数） | `kb:read` |
| `ask_knowledge_base` | 检索 + 带 `[n]` 引用的 grounded 回答，无证据则拒答 | `chat` |
| `get_document_outline` | 文档标题结构与页码分布 | `kb:read` |

工具是**只读**的：上传、删除、改配置仍然只在 Web 端做，避免 AI 误操作破坏语料。

## 两种接入方式

### 1. stdio（本地客户端）

1. 在 Web 端「API Key」页创建一个带 `kb:read` + `chat` 的密钥；
2. 在客户端配置里注册：

```json
{
  "mcpServers": {
    "ragscope": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "E:/rag_learn/backend",
      "env": { "RAGSCOPE_API_KEY": "rk_xxx" }
    }
  }
}
```

也可以手动跑起来调试（stdout 只输出 JSON-RPC，日志走 stderr）：

```bash
cd backend
RAGSCOPE_API_KEY=rk_xxx ./.venv/Scripts/python.exe -m app.mcp.server
# 粘贴一行：
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}
```

### 2. HTTP（服务端集成）

```
POST /api/v1/mcp
Authorization: Bearer rk_xxx
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"search_knowledge_base",
           "arguments":{"kb_id":"<uuid>","query":"X1 的电池容量是多少"}}}
```

`GET /api/v1/mcp/tools` 返回工具清单与当前调用者的 scope，方便快速自检。

## 为什么手写协议层

`app/mcp/protocol.py` 只实现了本服务用得到的部分：握手、`tools/list`、
`tools/call`、`ping`、通知与错误码。这样做的收益是：

- 离线环境（无法访问 PyPI）也能跑，部署包不引入额外依赖；
- 协议行为可测试——`tests/unit/test_mcp_protocol.py` 覆盖版本协商、通知无响应、
  未知方法、未认证调用等边界；
- 面试时可以完整讲清「握手 → 能力协商 → 工具调用」的链路，而不是停留在「用了某个
  SDK」。

## 安全边界

- API Key 只存 SHA-256 哈希，`rk_` 前缀用于识别凭据类型；
- 每次工具调用都走 `kb_service.get_owned_kb`，越权访问返回 404 而非 403；
- HTTP 传输按 key 做固定窗口限流（默认 60 次/分钟）；
- stdio 传输在启动时校验一次密钥，进程生命周期内复用该 Principal。
