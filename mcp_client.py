"""
MCP 客户端模块 - 管理远程 MCP 服务器连接和工具调用

通过后台 asyncio 事件循环驱动 MCP SDK（纯 async），
对外暴露同步接口供 agent.py 使用。
"""

import asyncio
import threading
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


# ============================================================================
# MCP 服务器配置
# ============================================================================

MCP_SERVERS = [
    {
        "name": "context7",
        "url": "https://mcp.context7.com/mcp",
        "headers": {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "CONTEXT7_API_KEY": "ctx7sk-50b9e9fa-299a-4792-9035-46d55f213384",
        },
    },
    {
        "name": "deepwiki",
        "url": "https://mcp.deepwiki.com/mcp",
        "headers": {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    },
]


# ============================================================================
# 后台异步事件循环
# ============================================================================

_mcp_loop = asyncio.new_event_loop()
_mcp_thread = threading.Thread(target=_mcp_loop.run_forever, daemon=True)
_mcp_thread.start()


def _run_async(coro):
    """在后台事件循环中执行异步协程，同步等待结果"""
    future = asyncio.run_coroutine_threadsafe(coro, _mcp_loop)
    return future.result(timeout=30)


# ============================================================================
# MCPManager
# ============================================================================

class MCPManager:
    def __init__(self):
        # server_name -> {"session": ClientSession, "tools": [...]}
        self.servers = {}
        # prefixed_tool_name -> server_name
        self.tool_routing = {}
        # Anthropic 格式的工具定义
        self.tool_definitions = []
        # 保持 async context manager 引用，防止被回收
        self._contexts = []

    # ---- async 内部方法 ----

    async def _connect_server(self, config: dict):
        """连接单个 MCP 服务器，发现工具"""
        name = config["name"]
        url = config["url"]
        headers = config["headers"]

        transport_ctx = streamablehttp_client(url=url, headers=headers)
        read_stream, write_stream, _ = await transport_ctx.__aenter__()
        self._contexts.append(transport_ctx)

        session_ctx = ClientSession(read_stream, write_stream)
        session = await session_ctx.__aenter__()
        self._contexts.append(session_ctx)

        await session.initialize()
        tools_response = await session.list_tools()

        self.servers[name] = {
            "session": session,
            "tools": tools_response.tools,
        }

        # 转换为 Anthropic 工具格式并注册路由
        for tool in tools_response.tools:
            prefixed = f"mcp_{name}__{tool.name}"
            self.tool_routing[prefixed] = name
            self.tool_definitions.append({
                "name": prefixed,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            })

        return tools_response.tools

    async def _connect_all(self, configs: list):
        """连接所有 MCP 服务器"""
        for config in configs:
            name = config["name"]
            try:
                tools = await self._connect_server(config)
                print(f"  \033[92m[OK] {name}: 已连接，发现 {len(tools)} 个工具\033[0m")
                for t in tools:
                    print(f"    - mcp_{name}__{t.name}")
            except Exception as e:
                print(f"  \033[91m[FAIL] {name}: 连接失败 - {e}\033[0m")

    async def _call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """调用 MCP 工具"""
        server_name = self.tool_routing[prefixed_name]
        original_name = prefixed_name[len(f"mcp_{server_name}__"):]
        session = self.servers[server_name]["session"]

        result = await session.call_tool(original_name, arguments)
        texts = []
        for block in result.content:
            if hasattr(block, "text"):
                texts.append(block.text)
            else:
                texts.append(str(block))
        return "\n".join(texts) if texts else "（工具无输出）"

    # ---- 同步公开接口 ----

    def init_servers(self):
        """启动时调用：连接所有 MCP 服务器"""
        _run_async(self._connect_all(MCP_SERVERS))

    def get_tool_definitions(self) -> list:
        """返回 Anthropic 格式的 MCP 工具定义列表"""
        return self.tool_definitions

    def is_mcp_tool(self, tool_name: str) -> bool:
        return tool_name in self.tool_routing

    def call_tool(self, tool_name: str, tool_input: dict) -> str:
        """同步调用 MCP 工具，返回文本结果"""
        try:
            return _run_async(self._call_tool(tool_name, tool_input))
        except Exception as e:
            return f"MCP 工具调用失败: {e}"


# 模块级实例
mcp_manager = MCPManager()
