"""
Agent 学习工具 - 可视化展示Agent工作原理

核心流程：
1. 用户输入 → 2. 发送给API（显示完整JSON请求）→ 3. 获取响应（显示完整JSON响应）
4. 如果响应是tool_use → 执行工具 → 反馈结果 → 回到步骤2
5. 如果响应是end_turn → 返回最终答案给用户
"""

import sys
import os

# 必须最先执行：切换 CMD 代码页到 UTF-8，确保双击 exe 也生效
os.system("chcp 65001 >nul 2>&1")

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
sys.stdin.reconfigure(encoding='utf-8', errors='replace')

import anthropic
import subprocess
import json
import locale
import urllib.request
import warnings
warnings.filterwarnings("ignore")
import requests
import chardet
import time
import threading
from mcp_client import mcp_manager, print_lock as _print_lock

class Colors:
    HEADER = '\033[95m'
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    ENDC   = '\033[0m'
    BOLD   = '\033[1m'

client = anthropic.Anthropic(
    api_key="sk-cp-N0a9hAUNXsnOun0mxby9_R9ESe_V6hDhZJ5VNuOEpVV_rqFTMXmnsElpXDX6IV_DuBwI6U4_k0ce6P4Wn3DTEVwiRjaIhJF2OfX688MXScwY3eypkXx2sXY",
    base_url="https://api.minimaxi.com/anthropic"
)
    # api_key="sk-sp-f8a97e8602d343f68eef487e13ef5c24",
    # base_url="https://coding.dashscope.aliyuncs.com/apps/anthropic"

    # api_key="sk-udhHZddO7Y79ZQEPhd3JJnIt6idrmn5FYoSVQIv8ZAYiJpNe",
    # base_url="https://codeflow.asia"

conversation_history = []
call_count = 0
token_stats = {"total_input": 0, "total_output": 0}

# 当前工作目录，跨命令持久化，支持 cd 切换
_cwd = os.getcwd()

# chcp 65001 之后取值，反映实际代码页；fallback 用 utf-8
_encoding = locale.getpreferredencoding(False) or 'utf-8'

# 运行模式：'json' 或 'chat'
_display_mode = 'chat'


# ============================================================================
# 序列化 / 打印
# ============================================================================

def serialize_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        result = []
        for item in content:
            if hasattr(item, 'type'):
                if item.type == "tool_use":
                    result.append({"type": "tool_use", "id": item.id, "name": item.name, "input": item.input})
                elif item.type == "text":
                    result.append({"type": "text", "text": item.text})
            elif isinstance(item, dict):
                result.append(item)
            else:
                result.append(str(item))
        return result
    return content


def print_context(messages: list, tools: list, call_num: int, model: str):
    if _display_mode != 'json':
        return
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*80}")
    print(f"📤 第 {call_num} 次 API 调用 - 发送给API的完整请求")
    print(f"{'='*80}{Colors.ENDC}\n")
    serializable_messages = [
        {"role": m["role"], "content": serialize_content(m["content"])}
        for m in messages
    ]
    api_request = {
        "model": model,
        "max_tokens": 2048,
        "system": build_system_prompt(),
        "tools": tools,
        "messages": serializable_messages,
        "temperature": "未设置（默认1.0）",
        "top_p": "未设置",
        "top_k": "未设置",
        "stop_sequences": "未设置",
    }
    print(f"{Colors.CYAN}{Colors.BOLD}【完整JSON请求】{Colors.ENDC}\n")
    print(json.dumps(api_request, ensure_ascii=False, indent=2))
    print()


def print_response(response, call_num: int):
    global token_stats
    if _display_mode != 'json':
        # 非 json 模式也累计 token
        if hasattr(response, 'usage'):
            token_stats["total_input"] += response.usage.input_tokens
            token_stats["total_output"] += response.usage.output_tokens
        return
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*80}")
    print(f"📥 第 {call_num} 次 API 调用 - API的完整响应")
    print(f"{'='*80}{Colors.ENDC}\n")

    # 构建 usage 信息
    usage_data = {}
    if hasattr(response, 'usage'):
        usage_data["input_tokens"] = response.usage.input_tokens
        usage_data["output_tokens"] = response.usage.output_tokens
        cache_create = getattr(response.usage, 'cache_creation_input_tokens', None)
        cache_read = getattr(response.usage, 'cache_read_input_tokens', None)
        if cache_create is not None:
            usage_data["cache_creation_input_tokens"] = cache_create
        if cache_read is not None:
            usage_data["cache_read_input_tokens"] = cache_read
        token_stats["total_input"] += response.usage.input_tokens
        token_stats["total_output"] += response.usage.output_tokens

    # 构建完整响应数据
    response_data = {
        "id": getattr(response, 'id', None),
        "type": getattr(response, 'type', None),
        "role": getattr(response, 'role', None),
        "model": getattr(response, 'model', None),
        "stop_reason": response.stop_reason,
        "stop_sequence": getattr(response, 'stop_sequence', None),
        "usage": usage_data,
        "content": []
    }
    for block in response.content:
        if block.type == "text":
            response_data["content"].append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            response_data["content"].append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    print(f"{Colors.YELLOW}{Colors.BOLD}【完整JSON响应】{Colors.ENDC}\n")
    print(json.dumps(response_data, ensure_ascii=False, indent=2))

    # 打印 token 累计统计
    print(f"\n{Colors.BOLD}{Colors.CYAN}【Token 统计】{Colors.ENDC}")
    if usage_data:
        print(f"  本次输入: {usage_data.get('input_tokens', 0)} tokens")
        print(f"  本次输出: {usage_data.get('output_tokens', 0)} tokens")
    print(f"  累计输入: {token_stats['total_input']} tokens")
    print(f"  累计输出: {token_stats['total_output']} tokens")
    print()


# ============================================================================
# 工具定义
# ============================================================================

tools = [
    {
        "name": "bash",
        "description": (
            "在 Windows CMD 中执行命令。使用标准 Windows CMD 语法。"
            "支持管道、重定向、多命令（用 & 或 && 连接）。"
            "支持 cd 切换目录，目录状态在多次调用间保持。"
            "写文件请使用 write_file 工具，不要用 echo > 重定向。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Windows CMD 命令，例如：dir、type file.txt、python script.py"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "write_file",
        "description": "将文本内容写入文件（创建或覆盖）。避免 shell 重定向的编码问题。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "文件路径，相对路径基于当前工作目录"},
                "content": {"type": "string", "description": "要写入的文本内容"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "read_file",
        "description": "读取本地文件内容。自动检测文件编码（UTF-8、GBK等），确保中文正确显示。读取文件请优先使用此工具而非 bash + type。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径，相对路径基于当前工作目录"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "web_fetch",
        "description": "Read and extract clean content from any URL using Jina Reader. Returns markdown-formatted text. Good for reading web pages, articles, documentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to read and extract content from"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "web_search",
        "description": "搜索互联网获取最新信息。返回搜索结果包含标题、URL和内容摘要。适用于查询实时信息、技术文档、新闻等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词"
                },
                "num_results": {
                    "type": "integer",
                    "description": "返回结果数量（1-20），默认15",
                    "default": 15
                }
            },
            "required": ["query"]
        }
    }
]


# ============================================================================
# 工具执行
# ============================================================================

def execute_bash(command: str) -> str:
    global _cwd
    try:
        stripped = command.strip()
        # 纯 cd 命令：更新持久工作目录
        if stripped.lower().startswith("cd ") and "&" not in stripped:
            target = stripped[3:].strip().strip('"').strip("'")
            new_dir = os.path.normpath(os.path.join(_cwd, target))
            if os.path.isdir(new_dir):
                _cwd = new_dir
                return f"已切换到: {_cwd}"
            return f"目录不存在: {new_dir}"

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            cwd=_cwd,
            timeout=30,
        )
        def decode_bytes(b):
            if not b:
                return ""
            try:
                return b.decode('utf-8')
            except UnicodeDecodeError:
                detected = chardet.detect(b)
                enc = detected.get("encoding") or 'gbk'
                return b.decode(enc, errors='replace')
        output = decode_bytes(result.stdout) + decode_bytes(result.stderr)
        return output.strip() or "命令执行成功，无输出"

    except subprocess.TimeoutExpired:
        return "命令执行超时（30s）"
    except Exception as e:
        return f"执行错误: {str(e)}"


def execute_write_file(path: str, content: str) -> str:
    try:
        full_path = path if os.path.isabs(path) else os.path.join(_cwd, path)
        parent = os.path.dirname(full_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"文件已写入: {full_path}"
    except Exception as e:
        return f"写入错误: {str(e)}"


def execute_read_file(path: str) -> str:
    try:
        full_path = path if os.path.isabs(path) else os.path.join(_cwd, path)
        if not os.path.isfile(full_path):
            return f"文件不存在: {full_path}"
        with open(full_path, 'rb') as f:
            raw = f.read()
        if not raw:
            return "（空文件）"
        # 优先尝试 UTF-8，失败再用 chardet 检测
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            detected = chardet.detect(raw)
            enc = detected.get("encoding") or 'gbk'
            return raw.decode(enc, errors='replace')
    except Exception as e:
        return f"读取错误: {str(e)}"


def execute_web_fetch(url: str) -> str:
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(jina_url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        return f"读取错误: {str(e)}"


def execute_web_search(query: str, num_results: int = 15) -> str:
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": "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",
                "query": query,
                "search_depth": "advanced",
                "max_results": num_results
            },
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return "未找到相关结果"
        output = []
        for i, r in enumerate(results, 1):
            output.append(f"{i}. {r.get('title', '无标题')}\n   URL: {r.get('url', '')}\n   {r.get('content', '')}")
        return "\n\n".join(output)
    except Exception as e:
        return f"搜索错误: {str(e)}"


def process_tool_call(tool_name: str, tool_input: dict) -> str:
    if tool_name == "bash":
        return execute_bash(tool_input["command"])
    if tool_name == "write_file":
        return execute_write_file(tool_input["path"], tool_input["content"])
    if tool_name == "read_file":
        return execute_read_file(tool_input["path"])
    if tool_name == "web_fetch":
        return execute_web_fetch(tool_input["url"])
    if tool_name == "web_search":
        return execute_web_search(tool_input["query"], tool_input.get("num_results", 15))
    if mcp_manager.is_mcp_tool(tool_name):
        return mcp_manager.call_tool(tool_name, tool_input)
    return "未知工具"


# ============================================================================
# 上下文管理
# ============================================================================

def truncate_tool_result(result: str, max_chars: int = 30000) -> str:
    """截断过长的工具返回结果，保留头尾各一半"""
    if len(result) <= max_chars:
        return result
    half = max_chars // 2
    return (
        result[:half]
        + f"\n\n... [内容过长已截断：原始 {len(result)} 字符，保留前后各 {half} 字符] ...\n\n"
        + result[-half:]
    )


def estimate_tokens(messages: list, system_prompt: str = "") -> int:
    """粗略估算 token 数，1 token ≈ 3 字符（偏保守，适合中英混合）"""
    total_chars = len(system_prompt)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    total_chars += len(json.dumps(item, ensure_ascii=False))
                else:
                    total_chars += len(str(item))
    return total_chars // 3


def trim_history(messages: list, system_prompt: str, token_budget: int = 150000) -> list:
    """超预算时裁剪早期历史，保留最近对话"""
    current = estimate_tokens(messages, system_prompt)
    if current <= token_budget:
        return messages

    print(f"\n{Colors.YELLOW}[上下文管理] 预估 {current} tokens，超过预算 {token_budget}，开始裁剪...{Colors.ENDC}")

    summary = {
        "role": "user",
        "content": "[系统提示：之前的对话历史因上下文长度限制已被省略，请基于后续消息继续对话。]"
    }

    keep_min = min(10, len(messages))
    trimmed = [summary] + messages[-keep_min:]

    while estimate_tokens(trimmed, system_prompt) > token_budget and keep_min > 2:
        keep_min -= 2
        trimmed = [summary] + messages[-keep_min:]

    print(f"{Colors.YELLOW}[上下文管理] 裁剪完成，保留 {len(trimmed)} 条消息（含摘要占位）{Colors.ENDC}")
    return trimmed


# ============================================================================
# Agent 核心
# ============================================================================

def _get_python_info() -> str:
    try:
        r = subprocess.run(["python", "--version"], capture_output=True, text=True, timeout=5)
        ver = (r.stdout or r.stderr).strip()
        return ver if ver else "未检测到"
    except Exception:
        return "未检测到"


def _get_cwd_files() -> str:
    try:
        entries = os.listdir(_cwd)
        if not entries:
            return "（空目录）"
        return "  ".join(entries[:50])  # 最多列50个，避免 prompt 过长
    except Exception:
        return "（无法读取）"


def build_system_prompt() -> str:
    """动态生成 system prompt，确保反映运行时实际工作目录"""
    return (
        "你是一个在 Windows 系统上运行的 AI Agent。\n\n"
        "当前环境：\n"
        f"- 操作系统：Windows\n"
        f"- Shell：CMD (cmd.exe)\n"
        f"- 当前工作目录：{_cwd}\n"
        f"- 用户：{os.environ.get('USERNAME', 'unknown')}\n"
        f"- CMD输出编码：{_encoding}\n"
        f"- Python：{_get_python_info()}\n"
        f"- 工作目录文件：{_get_cwd_files()}\n\n"
        "执行命令规则：\n"
        "1. 使用标准 Windows CMD 语法（不是 PowerShell，不是 bash）\n"
        "2. 写入文件时，必须使用 write_file 工具，不要用 echo > 重定向\n"
        "3. 路径分隔符用反斜杠 \\ 或正斜杠 / 均可\n"
        "4. 多条命令用 && 连接\n"
        "5. cd 命令会持久改变工作目录，后续命令在新目录执行\n"
        "\n回复格式规则：\n"
        "- 禁止使用 Markdown 格式，不要用 **加粗**、*斜体*、# 标题、- 列表符号等\n"
        "- 纯文本输出即可\n"
    )


def chat(user_message: str, model: str = "MiniMax-M2.7") -> str:
    global call_count, conversation_history
    all_tools = tools + mcp_manager.get_tool_definitions()

    conversation_history.append({"role": "user", "content": user_message})
    conversation_history = trim_history(conversation_history, build_system_prompt())

    def safe_api_call():
        global conversation_history
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                return client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=build_system_prompt(),
                    tools=all_tools,
                    messages=conversation_history
                )
            except anthropic.RateLimitError:
                if attempt < max_retries:
                    time.sleep(2 ** (attempt + 1))  # 2s, 4s, 8s
                    continue
                raise
            except anthropic.BadRequestError:
                conversation_history = trim_history(
                    conversation_history, build_system_prompt(), token_budget=80000
                )
                return client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=build_system_prompt(),
                    tools=all_tools,
                    messages=conversation_history
                )
            except anthropic.PermissionDeniedError:
                print(f"\n{Colors.RED}{Colors.BOLD}API欠费失效，请联系xiaoweihuacqu@gamil.com{Colors.ENDC}\n")
                return None

    call_count += 1
    print_context(conversation_history, all_tools, call_count, model)
    response = show_loading_with_task(safe_api_call, msg="Agent 思考中")
    if response is None:
        return ""
    print_response(response, call_count)

    while response.stop_reason == "tool_use":
        conversation_history.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                if _display_mode == 'json':
                    print(f"\n{Colors.BOLD}{Colors.CYAN}🔧 执行工具: {block.name}{Colors.ENDC}")
                    print(f"{Colors.CYAN}   工作目录: {_cwd}{Colors.ENDC}")
                else:
                    print(f"  {Colors.CYAN}◆ {block.name}...{Colors.ENDC}", end='', flush=True)
                tool_result = process_tool_call(block.name, block.input)
                tool_result = truncate_tool_result(tool_result)
                if _display_mode == 'json':
                    print(f"{Colors.GREEN}✓ 工具执行结果:{Colors.ENDC}\n{tool_result}\n")
                else:
                    print(f"\r  {Colors.GREEN}✓ {block.name}{Colors.ENDC}          ")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tool_result,
                    "is_error": False
                })

        conversation_history.append({"role": "user", "content": tool_results})
        conversation_history = trim_history(conversation_history, build_system_prompt())

        call_count += 1
        print_context(conversation_history, all_tools, call_count, model)
        response = show_loading_with_task(safe_api_call, msg="Agent 思考中")
        if response is None:
            return ""
        print_response(response, call_count)

    final_response = "".join(block.text for block in response.content if hasattr(block, "text"))
    conversation_history.append({"role": "assistant", "content": final_response})
    return final_response


# ============================================================================
# 主程序
# ============================================================================

def show_loading_with_task(task_func, msg: str = "正在预加载"):
    """显示加载动画，同时执行实际任务"""
    frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    idx = 0

    # 在后台线程执行任务
    result = [None]
    exception = [None]

    def run_task():
        try:
            result[0] = task_func()
        except Exception as e:
            exception[0] = e

    task_thread = threading.Thread(target=run_task, daemon=True)
    task_thread.start()

    # 显示加载动画直到任务完成
    while task_thread.is_alive():
        with _print_lock:
            print(f"\r  {Colors.CYAN}{frames[idx % len(frames)]} {msg}...{Colors.ENDC}", end='', flush=True)
        idx += 1
        task_thread.join(timeout=0.1)

    # 清除动画行，输出最终状态
    print("\r" + " " * 60 + "\r", end='', flush=True)
    if exception[0]:
        print(f"  {Colors.RED}✗ {msg}失败{Colors.ENDC}")
        raise exception[0]
    else:
        print(f"  {Colors.GREEN}✓ {msg}完成{Colors.ENDC}")

    return result[0]


def print_environment_info():
    user = os.environ.get("USERNAME", os.environ.get("USER", "unknown"))
    print(f"\n{Colors.BOLD}{Colors.CYAN}📍 运行环境信息:{Colors.ENDC}")
    print(f"  {Colors.YELLOW}操作系统{Colors.ENDC}    : Windows")
    print(f"  {Colors.YELLOW}Shell{Colors.ENDC}       : CMD (cmd.exe)")
    print(f"  {Colors.YELLOW}工作目录{Colors.ENDC}    : {_cwd}")
    print(f"  {Colors.YELLOW}CMD编码{Colors.ENDC}     : {_encoding}")
    print(f"  {Colors.YELLOW}用户{Colors.ENDC}        : {user}\n")


def main():
    global _display_mode

    # 打印启动界面
    print(f"\n{Colors.BOLD}{Colors.HEADER}╭─────────────────────────────────────────────────────────────────────────────╮{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}│  🤖  Agent  ·  LLM API工作流演示工具                          HuaXiaowei    │{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}╰─────────────────────────────────────────────────────────────────────────────╯{Colors.ENDC}\n")

    # 模式选择
    print(f"{Colors.BOLD}{Colors.CYAN}请选择运行模式：{Colors.ENDC}\n")
    print(f"  {Colors.GREEN}[1]{Colors.ENDC}  对话模式    像正常 AI 助手一样交互，隐藏 API 细节")
    print(f"  {Colors.GREEN}[2]{Colors.ENDC}  JSON 模式   显示完整 API 请求/响应 JSON，适合学习调试\n")

    while True:
        choice = input(f"  {Colors.BOLD}{Colors.YELLOW}输入 1 或 2，按 Enter 确认：{Colors.ENDC} ").strip()
        if choice == "1":
            _display_mode = 'chat'
            print(f"  {Colors.GREEN}✓ 已选择：对话模式{Colors.ENDC}")
            break
        elif choice == "2":
            _display_mode = 'json'
            print(f"  {Colors.GREEN}✓ 已选择：JSON 模式{Colors.ENDC}")
            break
        else:
            print(f"  {Colors.RED}✗ 输入无效，请输入 1 或 2{Colors.ENDC}")

    print_environment_info()

    # 连接 MCP 服务器（带加载动画）
    print(f"{Colors.BOLD}{Colors.CYAN}🔌 连接 MCP 服务器...{Colors.ENDC}")
    show_loading_with_task(mcp_manager.init_servers)
    print()

    print("\n功能说明：")
    print("  • 支持多轮对话，维护完整对话历史")
    print("  • 执行CMD命令（subprocess + 持久工作目录）")
    print("  • 读取文件（chardet 自动识别编码，支持 UTF-8/GBK 等）")
    print("  • 写入文件（原生 open 写入，避免 shell 重定向编码问题）")
    print("  • 读取网页内容（Jina Reader API 转 Markdown）")
    print("  • 进行网络搜索（Tavily Search API）")
    print("  • 上下文管理（自动估算 token，超预算时裁剪早期历史）")
    mcp_tools = mcp_manager.get_tool_definitions()
    if mcp_tools:
        print(f"  • MCP 工具（{len(mcp_tools)} 个来自远程服务器，动态注册到工具列表）")
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*80}{Colors.ENDC}\n")
    print("输入 'exit' 退出\n")

    while True:
        user_input = input(f"{Colors.BOLD}{Colors.GREEN}你: {Colors.ENDC}").strip()
        if user_input.lower() == "exit":
            print(f"{Colors.BOLD}{Colors.CYAN}再见!{Colors.ENDC}")
            break
        if not user_input:
            continue

        print()
        try:
            response = chat(user_input)
        except anthropic.RateLimitError:
            print(f"\n{Colors.YELLOW}请求过于频繁，请稍后再试。{Colors.ENDC}")
            continue
        except Exception as e:
            print(f"\n{Colors.RED}发生错误: {e}{Colors.ENDC}")
            print(f"{Colors.YELLOW}已清空对话历史，请重新开始。{Colors.ENDC}")
            conversation_history.clear()
            continue

        print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*80}")
        print("🎯 最终回复")
        print(f"{'='*80}{Colors.ENDC}")
        print(f"{Colors.GREEN}{response}{Colors.ENDC}\n")

if __name__ == "__main__":
    main()
