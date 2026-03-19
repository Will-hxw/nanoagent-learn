import sys
import os

os.system("chcp 65001 >nul 2>&1")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
sys.stdin.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, 'D:\\Desktop\\Agent-Cquclaw')

from agent import chat

print("=" * 80)
print("通过 Agent 工具调用测试编码")
print("=" * 80)
print()

# 让 agent 读取 GBK 文件
user_query = "请读取 test_gbk.txt 文件的内容，并告诉我你看到了什么"

print(f"用户输入: {user_query}")
print()

try:
    response = chat(user_query)
    print()
    print("=" * 80)
    print("Agent 最终回复:")
    print("=" * 80)
    print(response)
    print()
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
