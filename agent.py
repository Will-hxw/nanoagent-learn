"""
Agent 学习工具 - 可视化展示Agent工作原理

核心流程：
1. 用户输入 → 2. 发送给API（显示完整JSON请求）→ 3. 获取响应（显示完整JSON响应）
4. 如果响应是tool_use → 执行工具 → 反馈结果 → 回到步骤2
5. 如果响应是end_turn → 返回最终答案给用户
"""

import anthropic
import subprocess
import json
import os
import locale

class Colors:
    HEADER = '\033[95m'
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    ENDC   = '\033[0m'
    BOLD   = '\033[1m'

client = anthropic.Anthropic(
    api_key="sk-udhHZddO7Y79ZQEPhd3JJnIt6idrmn5FYoSVQIv8ZAYiJpNe",
    base_url="https://codeflow.asia"
)

conversation_history = []
call_count = 0

# 当前工作目录，跨命令持久化，支持 cd 切换
_cwd = os.getcwd()

# CMD 输出编码（GBK/CP936 等），避免乱码
_encoding = locale.getpreferredencoding(False) or 'utf-8'


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
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*80}")
    print(f"📤 第 {call_num} 次 API 调用 - 发送给API的完整请求")
    print(f"{'='*80}{Colors.ENDC}\n")
    serializable_messages = [
        {"role": m["role"], "content": serialize_content(m["content"])}
        for m in messages
    ]
    api_request = {"model": model, "max_tokens": 2048, "system": build_system_prompt(), "tools": tools, "messages": serializable_messages}
    print(f"{Colors.CYAN}{Colors.BOLD}【完整JSON请求】{Colors.ENDC}\n")
    print(json.dumps(api_request, ensure_ascii=False, indent=2))
    print()


def print_response(response, call_num: int):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*80}")
    print(f"📥 第 {call_num} 次 API 调用 - API的完整响应")
    print(f"{'='*80}{Colors.ENDC}\n")
    response_data = {"stop_reason": response.stop_reason, "content": []}
    for block in response.content:
        if block.type == "text":
            response_data["content"].append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            response_data["content"].append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    print(f"{Colors.YELLOW}{Colors.BOLD}【完整JSON响应】{Colors.ENDC}\n")
    print(json.dumps(response_data, ensure_ascii=False, indent=2))
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
            timeout=60,
            encoding=_encoding,
            errors='replace'
        )
        output = (result.stdout or "") + (result.stderr or "")
        return output.strip() or "命令执行成功，无输出"

    except subprocess.TimeoutExpired:
        return "命令执行超时（60s）"
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


def process_tool_call(tool_name: str, tool_input: dict) -> str:
    if tool_name == "bash":
        return execute_bash(tool_input["command"])
    if tool_name == "write_file":
        return execute_write_file(tool_input["path"], tool_input["content"])
    return "未知工具"


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


def chat(user_message: str, model: str = "claude-haiku-4-5-20251001") -> str:
    global call_count

    conversation_history.append({"role": "user", "content": user_message})

    call_count += 1
    print_context(conversation_history, tools, call_count, model)

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=build_system_prompt(),
        tools=tools,
        messages=conversation_history
    )
    print_response(response, call_count)

    while response.stop_reason == "tool_use":
        conversation_history.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"\n{Colors.BOLD}{Colors.CYAN}🔧 执行工具: {block.name}{Colors.ENDC}")
                print(f"{Colors.CYAN}   工作目录: {_cwd}{Colors.ENDC}")
                tool_result = process_tool_call(block.name, block.input)
                print(f"{Colors.GREEN}✓ 工具执行结果:{Colors.ENDC}\n{tool_result}\n")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tool_result
                })

        conversation_history.append({"role": "user", "content": tool_results})

        call_count += 1
        print_context(conversation_history, tools, call_count, model)

        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=build_system_prompt(),
            tools=tools,
            messages=conversation_history
        )
        print_response(response, call_count)

    final_response = "".join(block.text for block in response.content if hasattr(block, "text"))
    conversation_history.append({"role": "assistant", "content": final_response})
    return final_response


# ============================================================================
# 主程序
# ============================================================================

def print_environment_info():
    user = os.environ.get("USERNAME", os.environ.get("USER", "unknown"))
    print(f"\n{Colors.BOLD}{Colors.CYAN}📍 运行环境信息:{Colors.ENDC}")
    print(f"  {Colors.YELLOW}操作系统{Colors.ENDC}    : Windows")
    print(f"  {Colors.YELLOW}Shell{Colors.ENDC}       : CMD (cmd.exe)")
    print(f"  {Colors.YELLOW}工作目录{Colors.ENDC}    : {_cwd}")
    print(f"  {Colors.YELLOW}CMD编码{Colors.ENDC}     : {_encoding}")
    print(f"  {Colors.YELLOW}用户{Colors.ENDC}        : {user}\n")


def main():
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*80}")
    print("🤖 Agent 真实工作流学习工具 -HuaXiaowei")
    print(f"{'='*80}{Colors.ENDC}")
    print_environment_info()
    print("功能说明：")
    print("  • 每次API调用都会打印完整的JSON请求和响应")
    print("  • 支持多轮对话，维护完整对话历史")
    print("  • bash 工具执行 CMD 命令，write_file 工具写入文件")
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*80}{Colors.ENDC}\n")
    print("输入 'exit' 退出\n")

    while True:
        user_input = input(f"{Colors.BOLD}{Colors.GREEN}你: {Colors.ENDC}").strip()
        if user_input.lower() == "exit":
            print(f"{Colors.BOLD}{Colors.CYAN}再见!{Colors.ENDC}")
            break
        if not user_input:
            continue

        print(f"\n{Colors.BOLD}{Colors.YELLOW}Agent 处理中...{Colors.ENDC}")
        response = chat(user_input)

        print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*80}")
        print("🎯 最终回复")
        print(f"{'='*80}{Colors.ENDC}")
        print(f"{Colors.GREEN}{response}{Colors.ENDC}\n")

if __name__ == "__main__":
    main()
