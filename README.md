# Desktop Agent

<p align="center">
  <strong>Agent 基础架构学习</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="MIT License"></a>
</p>

**Desktop Agent** 是一个用于学习 Agent 基础架构原理的终端 AI 助手。它完整展示了 Agent 的核心工作流程：用户输入 → API 请求 → 工具调用 → 结果反馈 → 循环直到完成。

---

## 核心流程

```
用户输入 → 发送 API 请求（显示完整 JSON） → 获取响应（显示完整 JSON）
    ↓
如果是 tool_use → 执行工具 → 反馈结果 → 回到 API 请求
    ↓
如果是 end_turn → 返回最终答案
```

---

## 功能特性

- **内置工具** — bash、文件读写编辑、目录浏览、grep 搜索、网页抓取、Web 搜索、PDF 解析
- **MCP 扩展** — 支持远程 HTTP MCP 服务器和本地 stdio 类型 MCP 服务
- **双显示模式** — `chat` 模式简洁输出，`json` 模式展示完整请求/响应
- **上下文管理** — 自动压缩超出 token 预算的对话
- **流式输出** — 实时显示响应，支持中断
- **配置驱动** — 所有参数通过 `config.yaml` 管理，无硬编码

---

## 快速上手

**安装依赖**

```bash
pip install -r requirements.txt
```

**配置**

复制 `config.example.yaml` 为 `config.yaml`，填入 API key

**运行**

```bash
python agent.py
```

---

## 项目结构

```
agent.py          — 主入口，CLI 循环，工具调度，核心流程实现
mcp_client.py     — MCP 客户端（异步后台循环，同步调用接口）
config.py         — 配置加载（YAML + 默认值）
config.yaml       — 用户配置
requirements.txt  — Python 依赖
```

---

## License

MIT
