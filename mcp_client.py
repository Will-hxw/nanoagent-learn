"""
MCP 客户端模块 - 管理远程 MCP 服务器连接和工具调用

通过后台 asyncio 事件循环驱动 MCP SDK（纯 async），
对外暴露同步接口供 agent.py 使用。
"""

import asyncio
import concurrent.futures
import threading
import json
import config
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
# 后台异步事件循环
# ============================================================================

_mcp_loop = asyncio.new_event_loop()
_mcp_thread = threading.Thread(target=_mcp_loop.run_forever, daemon=True)
_mcp_thread.start()

# 跨线程 print 锁，供 agent.py 导入使用
print_lock = threading.Lock()
_USE_CONFIG_TIMEOUT = object()


def _run_async(coro, timeout=_USE_CONFIG_TIMEOUT):
    """在后台事件循环中执行异步协程，同步等待结果"""
    future = asyncio.run_coroutine_threadsafe(coro, _mcp_loop)
    wait_timeout = config.get("mcp_timeout", 30) if timeout is _USE_CONFIG_TIMEOUT else timeout
    try:
        if wait_timeout is None:
            return future.result()
        return future.result(timeout=wait_timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise


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

    async def _cleanup_contexts(self, contexts: list):
        """按相反顺序关闭本次尝试中创建的 context，避免重试时残留坏连接"""
        for ctx in reversed(contexts):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass

    @staticmethod
    def _format_error(exc: Exception) -> str:
        if isinstance(exc, (asyncio.TimeoutError, concurrent.futures.TimeoutError)):
            return f"连接超时（{config.get('mcp_timeout', 30)}s）"
        message = str(exc).strip()
        return message or exc.__class__.__name__

    async def _connect_server(self, srv_cfg: dict):
        """连接单个 MCP 服务器，发现工具"""
        name = srv_cfg["name"]
        local_contexts = []

        try:
            if srv_cfg.get("type") == "stdio":
                params = StdioServerParameters(
                    command=srv_cfg["command"],
                    args=srv_cfg.get("args", []),
                    env=srv_cfg.get("env"),
                )
                transport_ctx = stdio_client(params)
                read_stream, write_stream = await transport_ctx.__aenter__()
            else:
                url = srv_cfg["url"]
                headers = srv_cfg["headers"]
                transport_ctx = streamablehttp_client(url=url, headers=headers)
                read_stream, write_stream, _ = await transport_ctx.__aenter__()
            local_contexts.append(transport_ctx)

            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            local_contexts.append(session_ctx)

            await session.initialize()
            tools_response = await session.list_tools()
        except BaseException:
            await self._cleanup_contexts(local_contexts)
            raise

        self._contexts.extend(local_contexts)
        self.servers[name] = {
            "session": session,
            "tools": tools_response.tools,
        }

        # 仅在连接完全成功后注册工具，避免失败重试产生重复路由
        for tool in tools_response.tools:
            prefixed = f"mcp_{name}__{tool.name}"
            self.tool_routing[prefixed] = name
            self.tool_definitions.append({
                "name": prefixed,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            })

        return tools_response.tools

    async def _connect_server_with_timeout(self, srv_cfg: dict, timeout: int | None):
        """对单个服务应用超时控制，超时后取消连接并让 _connect_server 执行清理"""
        if timeout is None:
            return await self._connect_server(srv_cfg)
        return await asyncio.wait_for(self._connect_server(srv_cfg), timeout=timeout)

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
        """启动时调用：连接所有 MCP 服务器，失败时降级而不是中断启动"""
        summary = {
            "success_count": 0,
            "failed_count": 0,
            "connected": [],
            "failed": [],
        }
        timeout = config.get("mcp_timeout", 30)

        for srv_cfg in config.get_mcp_servers():
            name = srv_cfg["name"]
            last_error = None

            for attempt in range(1, 3):
                try:
                    tools = _run_async(
                        self._connect_server_with_timeout(srv_cfg, timeout),
                        timeout=None,
                    )
                    summary["connected"].append({
                        "name": name,
                        "tool_count": len(tools),
                    })
                    with print_lock:
                        print(f"\r" + " " * 60 + "\r", end='')
                        print(f"  \033[92m[OK] {name}: 已连接，发现 {len(tools)} 个工具\033[0m")
                        for t in tools:
                            print(f"    - mcp_{name}__{t.name}")
                    break
                except Exception as e:
                    last_error = e
                    if attempt < 2:
                        with print_lock:
                            print(f"\r" + " " * 60 + "\r", end='')
                            print(
                                f"  \033[93m[RETRY] {name}: 第 {attempt}/2 次连接失败 - "
                                f"{self._format_error(e)}，正在重试...\033[0m"
                            )
                    else:
                        reason = self._format_error(e)
                        summary["failed"].append({
                            "name": name,
                            "reason": reason,
                        })
                        with print_lock:
                            print(f"\r" + " " * 60 + "\r", end='')
                            print(f"  \033[91m[FAIL] {name}: 连接失败，已跳过 - {reason}\033[0m")

        summary["success_count"] = len(summary["connected"])
        summary["failed_count"] = len(summary["failed"])
        return summary

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
