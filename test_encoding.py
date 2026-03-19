import sys
import os

# 切换编码
os.system("chcp 65001 >nul 2>&1")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
sys.stdin.reconfigure(encoding='utf-8', errors='replace')

# 导入 agent 的工具函数
sys.path.insert(0, 'D:\\Desktop\\Agent-Cquclaw')

import chardet

# 测试读取 GBK 文件
file_path = 'test_gbk.txt'

print("=" * 80)
print("测试编码功能")
print("=" * 80)

# 读取原始字节
with open(file_path, 'rb') as f:
    raw_bytes = f.read()

print(f"\n原始字节（前50个）: {raw_bytes[:50]}")

# 检测编码
detected = chardet.detect(raw_bytes)
print(f"\nchardet 检测结果: {detected}")

# 尝试用检测到的编码解码
detected_encoding = detected.get('encoding', 'utf-8')
try:
    content = raw_bytes.decode(detected_encoding)
    print(f"\n用 {detected_encoding} 解码成功:")
    print(f"内容: {content}")
except Exception as e:
    print(f"\n用 {detected_encoding} 解码失败: {e}")
    content = raw_bytes.decode('utf-8', errors='replace')
    print(f"用 utf-8 (replace) 解码: {content}")

print("\n" + "=" * 80)
print("测试完成")
print("=" * 80)
