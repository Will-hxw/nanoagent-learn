"""
MCP 客户端模块 - 管理远程 MCP 服务器连接和工具调用

通过后台 asyncio 事件循环驱动 MCP SDK（纯 async），
对外暴露同步接口供 agent.py 使用。
"""

import asyncio
import threading
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client


# ============================================================================
# 颜色常量（用于 JSON 模式打印）
# ============================================================================

_BOLD   = '\033[1m'
_CYAN   = '\033[96m'
_YELLOW = '\033[93m'
_ENDC   = '\033[0m'


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
    {
        "name": "minimax",
        "type": "stdio",
        "command": "uvx",
        "args": ["minimax-coding-plan-mcp", "-y"],
        "env": {
            "MINIMAX_API_KEY": "sk-cp-N0a9hAUNXsnOun0mxby9_R9ESe_V6hDhZJ5VNuOEpVV_rqFTMXmnsElpXDX6IV_DuBwI6U4_k0ce6P4Wn3DTEVwiRjaIhJF2OfX688MXScwY3eypkXx2sXY",
            "MINIMAX_API_HOST": "https://api.minimaxi.com",
        },
    },
]


# ============================================================================
# 后台异步事件循环
# ============================================================================

_mcp_loop = asyncio.new_event_loop()
_mcp_thread = threading.Thread(target=_mcp_loop.run_forever, daemon=True)
_mcp_thread.start()

# 跨线程 print 锁，供 agent.py 导入使用
print_lock = threading.Lock()


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

        if config.get("type") == "stdio":
            params = StdioServerParameters(
                command=config["command"],
                args=config.get("args", []),
                env=config.get("env"),
            )
            transport_ctx = stdio_client(params)
            read_stream, write_stream = await transport_ctx.__aenter__()
        else:
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
                with print_lock:
                    print(f"\r" + " " * 60 + "\r", end='')
                    print(f"  \033[92m[OK] {name}: 已连接，发现 {len(tools)} 个工具\033[0m")
                    for t in tools:
                        print(f"    - mcp_{name}__{t.name}")
            except Exception as e:
                with print_lock:
                    print(f"\r" + " " * 60 + "\r", end='')
                    print(f"  \033[91m[FAIL] {name}: 连接失败 - {e}\033[0m")

    async def _call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """调用 MCP 工具"""
        import agent as _agent

        server_name = self.tool_routing[prefixed_name]
        original_name = prefixed_name[len(f"mcp_{server_name}__"):]
        session = self.servers[server_name]["session"]

        # 打印请求
        if _agent._display_mode == 'json':
            print(f"\n{_CYAN}{_BOLD}{'='*80}")
            print(f"🔌 MCP 工具调用 - 发送请求")
            print(f"{'='*80}{_ENDC}\n")
            mcp_request = {
                "server": server_name,
                "tool": original_name,
                "prefixed_name": prefixed_name,
                "arguments": arguments
            }
            print(f"{_CYAN}{_BOLD}【MCP请求】{_ENDC}\n")
            print(json.dumps(mcp_request, ensure_ascii=False, indent=2))
            print()

        result = await session.call_tool(original_name, arguments)

        # 打印响应
        if _agent._display_mode == 'json':
            print(f"\n{_YELLOW}{_BOLD}{'='*80}")
            print(f"🔌 MCP 工具调用 - 收到响应")
            print(f"{'='*80}{_ENDC}\n")
            content_list = []
            for block in result.content:
                if hasattr(block, "text"):
                    content_list.append({"type": "text", "text": block.text})
                else:
                    content_list.append({"type": str(type(block).__name__), "raw": str(block)})
            mcp_response = {
                "server": server_name,
                "tool": original_name,
                "is_error": getattr(result, "isError", False),
                "content": content_list
            }
            print(f"{_YELLOW}{_BOLD}【MCP响应】{_ENDC}\n")
            print(json.dumps(mcp_response, ensure_ascii=False, indent=2))
            print()

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
