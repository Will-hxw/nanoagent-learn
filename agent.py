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
import fnmatch
import re
import locale
import urllib.request
import warnings
warnings.filterwarnings("ignore")
import requests
import chardet
import time
import threading
import signal
from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from mcp_client import mcp_manager, print_lock as _print_lock
import config

class Colors:
    HEADER = '\033[95m'
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    ENDC   = '\033[0m'
    BOLD   = '\033[1m'

client = anthropic.Anthropic(
    api_key=config.get("api.key"),
    base_url=config.get("api.base_url"),
)

conversation_history = []
call_count = 0
token_stats = {"total_input": 0, "total_output": 0}

# 中断控制
_interrupted = threading.Event()
# streaming 是否可用（首次失败后永久降级）
_streaming_available = True

# 当前工作目录，跨命令持久化，支持 cd 切换
_cwd = os.getcwd()

# 哨兵标记：用于从 subprocess 输出中提取子进程最终工作目录
_CWD_SENTINEL = "===CWD_SYNC==="

# chcp 65001 之后取值，反映实际代码页；fallback 用 utf-8
_encoding = locale.getpreferredencoding(False) or 'utf-8'

# 运行模式：'json' 或 'chat'
_display_mode = config.get("display.mode", "chat")

# 终端渲染和输入
_rich_console = Console()

from prompt_toolkit.key_binding import KeyBindings
_input_bindings = KeyBindings()

@_input_bindings.add('enter')
def _submit(event):
    """Enter 直接提交"""
    event.current_buffer.validate_and_handle()

@_input_bindings.add('c-j')
def _newline_ctrl_enter(event):
    """Ctrl+Enter 插入换行"""
    event.current_buffer.insert_text('\n')

_prompt_session = PromptSession(
    multiline=True,
    key_bindings=_input_bindings,
    prompt_continuation=lambda width, line_number, is_soft_wrap: "... ",
)


def render_markdown(text: str):
    """用 rich 渲染 Markdown 到终端，失败时 fallback 纯文本"""
    try:
        md = RichMarkdown(text, code_theme="monokai")
        _rich_console.print(md)
    except Exception:
        print(f"{Colors.GREEN}{text}{Colors.ENDC}")


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
        "max_tokens": config.get("api.max_tokens", 2048),
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
    },
    {
        "name": "edit_file",
        "description": (
            "对已有文件进行精确编辑，通过字符串匹配定位并替换内容。\n"
            "用法：\n"
            "1. 替换：提供 old_string 和 new_string\n"
            "2. 删除：old_string 为要删除的内容，new_string 设为空字符串\n"
            "3. 插入：old_string 为插入点附近的已有文本，new_string 为该文本加上要插入的新内容\n"
            "注意：old_string 必须与文件内容完全匹配（包括缩进和换行）。"
            "如果匹配到多处会报错，请提供更多上下文使 old_string 唯一。"
            "编辑前请先用 read_file 查看文件内容。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径，相对路径基于当前工作目录"},
                "old_string": {"type": "string", "description": "要被替换的原始文本，必须与文件内容完全匹配"},
                "new_string": {"type": "string", "description": "替换后的新文本，留空表示删除 old_string"}
            },
            "required": ["path", "old_string", "new_string"]
        }
    },
    {
        "name": "list_dir",
        "description": "列出目录内容，返回文件和子目录列表。每项标注类型（[文件]/[目录]）和大小。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，相对路径基于当前工作目录。默认为当前工作目录"}
            },
            "required": []
        }
    },
    {
        "name": "grep_search",
        "description": (
            "在文件内容中搜索匹配的文本或正则表达式。"
            "递归搜索目录下所有文本文件，返回匹配的文件名、行号和内容。"
            "适合查找函数定义、变量引用、import 语句等。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式，支持 Python 正则表达式"},
                "path": {"type": "string", "description": "搜索起始路径（文件或目录），默认当前工作目录"},
                "file_pattern": {"type": "string", "description": "文件名过滤（glob 模式），如 '*.py'、'*.js'。不指定则搜索所有文本文件"}
            },
            "required": ["pattern"]
        }
    }
]

# 按配置过滤已禁用的工具
tools = [t for t in tools if config.is_tool_enabled(t["name"])]


# ============================================================================
# 工具执行
# ============================================================================

def _decode_bytes(b: bytes) -> str:
    """解码子进程输出字节，优先 UTF-8，fallback chardet 检测"""
    if not b:
        return ""
    try:
        return b.decode('utf-8')
    except UnicodeDecodeError:
        detected = chardet.detect(b)
        enc = detected.get("encoding") or 'gbk'
        return b.decode(enc, errors='replace')


def execute_bash(command: str) -> str:
    global _cwd
    try:
        # 在命令末尾追加哨兵 + cd，用 & (非 &&) 确保即使命令失败也能拿到目录
        injected = f'{command} & echo {_CWD_SENTINEL} & cd'

        result = subprocess.run(
            injected,
            shell=True,
            capture_output=True,
            cwd=_cwd,
            timeout=config.get_tool_config("bash").get("timeout", 30),
        )

        stdout_text = _decode_bytes(result.stdout)
        stderr_text = _decode_bytes(result.stderr).strip()

        # 从 stdout 提取哨兵后的工作目录，并分离用户输出
        user_output = stdout_text
        if _CWD_SENTINEL in stdout_text:
            parts = stdout_text.split(_CWD_SENTINEL, 1)
            user_output = parts[0]
            after = parts[1]
            for line in after.splitlines():
                line = line.strip()
                if line and os.path.isdir(line):
                    _cwd = os.path.normpath(line)
                    break

        stdout = user_output.strip()
        if not stdout and not stderr_text and result.returncode == 0:
            stdout = "命令执行成功，无输出"
        return json.dumps({"stdout": stdout, "stderr": stderr_text, "returncode": result.returncode, "cwd": _cwd}, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        return json.dumps({"stdout": "", "stderr": "命令执行超时（30s）", "returncode": -1, "cwd": _cwd}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"stdout": "", "stderr": f"执行错误: {str(e)}", "returncode": -1, "cwd": _cwd}, ensure_ascii=False)


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


def execute_edit_file(path: str, old_string: str, new_string: str) -> str:
    try:
        full_path = path if os.path.isabs(path) else os.path.join(_cwd, path)

        # old_string 为空 → 创建新文件
        if not old_string:
            if os.path.exists(full_path):
                return f"错误：文件已存在: {full_path}，如需编辑请提供 old_string"
            parent = os.path.dirname(full_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(new_string)
            return f"新文件已创建: {full_path}"

        # 编辑已有文件
        if not os.path.isfile(full_path):
            return f"文件不存在: {full_path}"

        with open(full_path, 'rb') as f:
            raw = f.read()
        try:
            content = raw.decode('utf-8')
            encoding = 'utf-8'
        except UnicodeDecodeError:
            detected = chardet.detect(raw)
            encoding = detected.get("encoding") or 'gbk'
            content = raw.decode(encoding, errors='replace')

        # 检查匹配次数
        count = content.count(old_string)
        if count == 0:
            return "错误：未找到匹配文本，请检查 old_string 是否与文件内容完全一致（包括缩进、换行、空格）"
        if count > 1:
            return f"错误：old_string 匹配到 {count} 处，请提供更多上下文使其唯一"

        # 执行替换并写回
        new_content = content.replace(old_string, new_string, 1)
        with open(full_path, 'w', encoding=encoding) as f:
            f.write(new_content)

        old_lines = old_string.count('\n') + 1
        new_lines = new_string.count('\n') + 1
        if not new_string:
            return f"已删除 {old_lines} 行内容: {full_path}"
        return f"已编辑: {full_path}（{old_lines} 行 → {new_lines} 行）"
    except Exception as e:
        return f"编辑错误: {str(e)}"


_SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'dist', 'build', '.idea', '.vscode'}
_BINARY_EXT = {'.exe', '.dll', '.so', '.pyc', '.pyd', '.zip', '.tar', '.gz', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.bmp', '.pdf', '.woff', '.woff2', '.ttf'}


def execute_list_dir(path: str = ".") -> str:
    try:
        full_path = path if os.path.isabs(path) else os.path.join(_cwd, path)
        if not os.path.isdir(full_path):
            return f"目录不存在: {full_path}"
        entries = sorted(os.listdir(full_path))
        if not entries:
            return "（空目录）"
        lines = []
        for name in entries:
            fp = os.path.join(full_path, name)
            if os.path.isdir(fp):
                lines.append(f"[目录] {name}")
            else:
                size = os.path.getsize(fp)
                if size < 1024:
                    s = f"{size}B"
                elif size < 1024 * 1024:
                    s = f"{size/1024:.1f}KB"
                else:
                    s = f"{size/1024/1024:.1f}MB"
                lines.append(f"[文件] {name} ({s})")
        return "\n".join(lines)
    except Exception as e:
        return f"目录读取错误: {str(e)}"


def execute_grep_search(pattern: str, path: str = ".", file_pattern: str = None) -> str:
    try:
        full_path = path if os.path.isabs(path) else os.path.join(_cwd, path)
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"正则表达式错误: {e}"

        matches = []
        _grep_cfg = config.get_tool_config("grep_search")
        max_matches = _grep_cfg.get("max_matches", 100)

        def search_file(filepath):
            if os.path.splitext(filepath)[1].lower() in _BINARY_EXT:
                return
            try:
                with open(filepath, 'rb') as f:
                    raw = f.read(_grep_cfg.get("max_file_size", 262144))
                try:
                    text = raw.decode('utf-8')
                except UnicodeDecodeError:
                    detected = chardet.detect(raw)
                    enc = detected.get("encoding") or 'gbk'
                    text = raw.decode(enc, errors='replace')
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        rel = os.path.relpath(filepath, _cwd)
                        matches.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(matches) >= max_matches:
                            return
            except Exception:
                pass

        if os.path.isfile(full_path):
            search_file(full_path)
        elif os.path.isdir(full_path):
            for root, dirs, files in os.walk(full_path):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fname in files:
                    if file_pattern and not fnmatch.fnmatch(fname, file_pattern):
                        continue
                    search_file(os.path.join(root, fname))
                    if len(matches) >= max_matches:
                        break
                if len(matches) >= max_matches:
                    break
        else:
            return f"路径不存在: {full_path}"

        if not matches:
            return "未找到匹配"
        result = "\n".join(matches)
        if len(matches) >= max_matches:
            result += f"\n\n（结果已截断，仅显示前 {max_matches} 条匹配）"
        return result
    except Exception as e:
        return f"搜索错误: {str(e)}"


def execute_web_fetch(url: str) -> str:
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(jina_url, headers=headers, timeout=config.get_tool_config("web_fetch").get("timeout", 30))
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        return f"读取错误: {str(e)}"


def execute_web_search(query: str, num_results: int = 15) -> str:
    try:
        _ws_cfg = config.get_tool_config("web_search")
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": _ws_cfg.get("api_key", ""),
                "query": query,
                "search_depth": "advanced",
                "max_results": num_results
            },
            timeout=_ws_cfg.get("timeout", 30)
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
    if tool_name == "edit_file":
        return execute_edit_file(tool_input["path"], tool_input["old_string"], tool_input["new_string"])
    if tool_name == "list_dir":
        return execute_list_dir(tool_input.get("path", "."))
    if tool_name == "grep_search":
        return execute_grep_search(tool_input["pattern"], tool_input.get("path", "."), tool_input.get("file_pattern"))
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

def truncate_tool_result(result: str, max_chars: int = None) -> str:
    """截断过长的工具返回结果，保留头尾各一半"""
    if max_chars is None:
        max_chars = config.get("context.max_tool_result_chars", 30000)
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


def trim_history(messages: list, system_prompt: str, token_budget: int = None) -> list:
    """超预算时裁剪早期历史，保留最近对话"""
    if token_budget is None:
        token_budget = config.get("context.token_budget", 150000)
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
        "6. 编辑文件时，优先使用 edit_file 进行精确修改，避免用 write_file 整文件重写\n"
        "7. 查看目录结构用 list_dir，搜索代码内容用 grep_search，比 bash 更稳定\n"
        "\n回复格式规则（终端可渲染Markdown）：\n"
        "- 使用 Markdown 格式回复（标题、列表、粗体、代码块等），终端会正确渲染\n"
        "- 代码块请标注语言（如 ```python），以便语法高亮\n"
    )


# ============================================================================
# 流式输出
# ============================================================================

def _print_stream_event_chat(event):
    """chat 模式：工具调用时打印工具名，文本不实时打印（收集后统一渲染）"""
    if event.type == "content_block_start":
        block = event.content_block
        if block.type == "tool_use":
            print(f"  {Colors.CYAN}◆ {block.name}...{Colors.ENDC}", end='', flush=True)


def _print_stream_event_json(event):
    """json 模式：打印每个 streaming event"""
    event_data = {"type": event.type}
    if event.type == "message_start":
        msg = event.message
        event_data["message"] = {"id": msg.id, "model": msg.model, "role": msg.role}
    elif event.type == "content_block_start":
        block = event.content_block
        event_data["content_block"] = {"type": block.type}
        if block.type == "tool_use":
            event_data["content_block"]["name"] = block.name
            event_data["content_block"]["id"] = block.id
    elif event.type == "content_block_delta":
        delta = event.delta
        event_data["delta"] = {"type": delta.type}
        if hasattr(delta, 'text'):
            event_data["delta"]["text"] = delta.text
        if hasattr(delta, 'partial_json'):
            event_data["delta"]["partial_json"] = delta.partial_json
    elif event.type == "message_delta":
        event_data["stop_reason"] = event.delta.stop_reason
        if hasattr(event, 'usage') and event.usage:
            event_data["usage"] = {"output_tokens": event.usage.output_tokens}
    else:
        return  # 跳过不关心的事件类型
    print(f"  {Colors.CYAN}[SSE]{Colors.ENDC} {json.dumps(event_data, ensure_ascii=False)}")


def _print_token_stats_streaming(message):
    """流式完成后打印 token 统计（json 模式）"""
    if not hasattr(message, 'usage'):
        return
    usage = message.usage
    print(f"\n{Colors.BOLD}{Colors.CYAN}【Token 统计】{Colors.ENDC}")
    print(f"  本次输入: {usage.input_tokens} tokens")
    print(f"  本次输出: {usage.output_tokens} tokens")
    print(f"  累计输入: {token_stats['total_input']} tokens")
    print(f"  累计输出: {token_stats['total_output']} tokens")
    print()


def stream_chat_response(model, all_tools, system_prompt, messages):
    """流式调用 API，chat模式等首token后收集完整响应再渲染，json模式实时打印event。"""
    global _streaming_available

    if not _streaming_available:
        return _fallback_non_stream(model, all_tools, system_prompt, messages)

    try:
        with client.messages.stream(
            model=model,
            max_tokens=config.get("api.max_tokens", 2048),
            system=system_prompt,
            tools=all_tools,
            messages=messages,
        ) as stream:
            first_token_received = False
            frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
            frame_idx = [0]
            last_frame_time = [time.time()]

            # chat 模式：等待首 token 时显示转圈
            if _display_mode != 'json':
                print(f"\r  {Colors.CYAN}{frames[0]} Agent 思考中...{Colors.ENDC}", end='', flush=True)

            for event in stream:
                if _interrupted.is_set():
                    if not first_token_received and _display_mode != 'json':
                        print("\r" + " " * 60 + "\r", end='', flush=True)
                    stream.close()
                    return None

                if _display_mode == 'json':
                    _print_stream_event_json(event)
                else:
                    # chat 模式：检测首个 text delta 到达
                    if not first_token_received:
                        is_text = (event.type == "content_block_delta"
                                   and hasattr(event.delta, 'text'))
                        is_tool = (event.type == "content_block_start"
                                   and event.content_block.type == "tool_use")
                        if is_text or is_tool:
                            first_token_received = True
                            print("\r" + " " * 60 + "\r", end='', flush=True)
                            if is_text:
                                print(f"  {Colors.GREEN}✓ Agent 思考完成{Colors.ENDC}")
                        else:
                            # 更新转圈动画
                            now = time.time()
                            if now - last_frame_time[0] >= 0.1:
                                frame_idx[0] = (frame_idx[0] + 1) % len(frames)
                                last_frame_time[0] = now
                                print(f"\r  {Colors.CYAN}{frames[frame_idx[0]]} Agent 思考中...{Colors.ENDC}", end='', flush=True)
                    _print_stream_event_chat(event)

            if not first_token_received and _display_mode != 'json':
                print("\r" + " " * 60 + "\r", end='', flush=True)
                print(f"  {Colors.GREEN}✓ Agent 思考完成{Colors.ENDC}")

            final_message = stream.get_final_message()
            if hasattr(final_message, 'usage'):
                token_stats["total_input"] += final_message.usage.input_tokens
                token_stats["total_output"] += final_message.usage.output_tokens
            if _display_mode == 'json':
                _print_token_stats_streaming(final_message)
            return final_message

    except Exception as e:
        err_msg = str(e).lower()
        if "stream" in err_msg or "not supported" in err_msg or "event" in err_msg:
            _streaming_available = False
            print(f"  {Colors.YELLOW}⚠ API 不支持 Streaming，已降级为普通模式{Colors.ENDC}")
            return _fallback_non_stream(model, all_tools, system_prompt, messages)
        raise


def _fallback_non_stream(model, all_tools, system_prompt, messages):
    """Streaming 不可用时的降级路径：阻塞调用 + 转圈动画"""
    def api_call():
        return client.messages.create(
            model=model,
            max_tokens=config.get("api.max_tokens", 2048),
            system=system_prompt,
            tools=all_tools,
            messages=messages,
        )
    response = show_loading_with_task(api_call, msg="Agent 思考中")
    if response and hasattr(response, 'usage'):
        token_stats["total_input"] += response.usage.input_tokens
        token_stats["total_output"] += response.usage.output_tokens
    if _display_mode == 'json' and response:
        print_response(response, call_count)
    return response


def _call_with_retry(model, all_tools, messages):
    """带重试的 API 调用，优先流式，失败降级"""
    system_prompt = build_system_prompt()
    max_retries = config.get("api.max_retries", 3)
    for attempt in range(max_retries + 1):
        try:
            return stream_chat_response(model, all_tools, system_prompt, messages)
        except anthropic.RateLimitError:
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                print(f"  {Colors.YELLOW}⚠ 限流，{wait}s 后重试...{Colors.ENDC}")
                time.sleep(wait)
                continue
            raise
        except anthropic.BadRequestError:
            messages[:] = trim_history(messages, system_prompt, token_budget=config.get("context.fallback_token_budget", 80000))
            return stream_chat_response(model, all_tools, system_prompt, messages)
        except anthropic.PermissionDeniedError:
            print(f"\n{Colors.RED}{Colors.BOLD}API欠费失效，请联系xiaoweihuacqu@gamil.com{Colors.ENDC}\n")
            return None


def chat(user_message: str, model: str = None) -> str:
    global call_count, conversation_history
    if model is None:
        model = config.get("api.model", "MiniMax-M2.7")
    all_tools = tools + mcp_manager.get_tool_definitions()

    conversation_history.append({"role": "user", "content": user_message})
    conversation_history = trim_history(conversation_history, build_system_prompt())

    _interrupted.clear()

    call_count += 1
    print_context(conversation_history, all_tools, call_count, model)
    response = _call_with_retry(model, all_tools, conversation_history)
    if response is None:
        return ""

    while response.stop_reason == "tool_use":
        if _interrupted.is_set():
            break

        conversation_history.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                if _interrupted.is_set():
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "用户已取消操作",
                        "is_error": True,
                    })
                    continue

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
                is_error = False
                if block.name == "bash":
                    try:
                        is_error = json.loads(tool_result).get("returncode", 0) != 0
                    except (json.JSONDecodeError, AttributeError):
                        pass
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tool_result,
                    "is_error": is_error,
                })

        conversation_history.append({"role": "user", "content": tool_results})

        if _interrupted.is_set():
            break

        conversation_history = trim_history(conversation_history, build_system_prompt())

        call_count += 1
        print_context(conversation_history, all_tools, call_count, model)
        response = _call_with_retry(model, all_tools, conversation_history)
        if response is None:
            return ""

    final_response = "".join(block.text for block in response.content if hasattr(block, "text"))
    conversation_history.append({"role": "assistant", "content": final_response})

    if _interrupted.is_set():
        print(f"\n  {Colors.YELLOW}⚠ 已中断{Colors.ENDC}")

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
        if _interrupted.is_set():
            print("\r" + " " * 60 + "\r", end='', flush=True)
            print(f"  {Colors.YELLOW}⚠ 等待当前请求完成...{Colors.ENDC}")
            task_thread.join(timeout=5)
            return result[0]
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
    print("  • 编辑文件（edit_file 精确替换，避免整文件重写）")
    print("  • 目录浏览（list_dir 列出目录内容，无编码问题）")
    print("  • 代码搜索（grep_search 正则搜索文件内容，支持文件名过滤）")
    print("  • 读取网页内容（Jina Reader API 转 Markdown）")
    print("  • 进行网络搜索（Tavily Search API）")
    print("  • 上下文管理（自动估算 token，超预算时裁剪早期历史）")
    print("  • 图片理解（MiniMAX MCP）")
    print("  • Markdown 渲染（rich 终端渲染，代码块语法高亮）")
    print("  • 多行输入（支持粘贴大段文本，Ctrl+Enter 换行，Enter 提交）")
    mcp_tools = mcp_manager.get_tool_definitions()
    if mcp_tools:
        print(f"  • MCP 工具（{len(mcp_tools)} 个来自远程服务器，动态注册到工具列表）")
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*80}{Colors.ENDC}\n")
    print("输入 'exit' 退出 | Ctrl+C 中断当前任务 | Ctrl+Enter 换行，Enter 提交\n")

    while True:
        try:
            user_input = _prompt_session.prompt(
                HTML("<b><green>你: </green></b>"),
            ).strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            print(f"{Colors.BOLD}{Colors.CYAN}再见!{Colors.ENDC}")
            break
        if user_input.lower() == "exit":
            print(f"{Colors.BOLD}{Colors.CYAN}再见!{Colors.ENDC}")
            break
        if not user_input:
            continue

        print()

        # 注册 Ctrl+C 信号处理：中断当前任务而非退出程序
        original_handler = signal.getsignal(signal.SIGINT)

        def interrupt_handler(signum, frame):
            _interrupted.set()
            print(f"\n  {Colors.YELLOW}⚠ 正在中断...{Colors.ENDC}", flush=True)

        signal.signal(signal.SIGINT, interrupt_handler)

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
        finally:
            signal.signal(signal.SIGINT, original_handler)
            _interrupted.clear()

        if not response:
            continue

        if _display_mode == 'chat':
            # 收集完整响应后 Markdown 渲染
            print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*80}")
            print("🎯 最终回复")
            print(f"{'='*80}{Colors.ENDC}")
            render_markdown(response)
            print()
        else:
            # json 模式保留完整最终回复
            print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*80}")
            print("🎯 最终回复")
            print(f"{'='*80}{Colors.ENDC}")
            print(f"{Colors.GREEN}{response}{Colors.ENDC}\n")

if __name__ == "__main__":
    main()
