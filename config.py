"""
统一配置中心 - 加载 config.yaml，提供全局访问
"""
import os
import sys
import copy
import yaml


# ============================================================================
# 默认配置（config.yaml 缺失或某字段缺失时的兜底）
# ============================================================================

DEFAULTS = {
    "api": {
        "key": "",
        "base_url": "https://api.minimaxi.com/anthropic",
        "model": "MiniMax-M2.7",
        "max_tokens": 2048,
        "max_retries": 3,
    },
    "display": {
        "mode": "chat",
    },
    "context": {
        "token_budget": 150000,
        "fallback_token_budget": 80000,
        "max_tool_result_chars": 30000,
    },
    "tools_default_timeout": 60,
    "tools": {
        "bash":        {"enabled": True, "timeout": 30},
        "write_file":  {"enabled": True, "timeout": 30},
        "read_file":   {"enabled": True, "timeout": 30},
        "edit_file":   {"enabled": True, "timeout": 30},
        "list_dir":    {"enabled": True, "timeout": 15},
        "grep_search": {"enabled": True, "timeout": 45, "max_matches": 100, "max_file_size": 262144},
        "web_fetch":   {"enabled": True, "timeout": 30},
        "web_search":  {"enabled": True, "timeout": 30, "api_key": ""},
    },
    "mcp_servers": [],
    "mcp_timeout": 30,
}


# ============================================================================
# 内部工具函数
# ============================================================================

def _find_config_path() -> str:
    """
    查找 config.yaml，优先级：
    1. 环境变量 AGENT_CONFIG
    2. exe 所在目录（PyInstaller 打包后，允许外部覆盖）
    3. 当前工作目录
    4. PyInstaller _MEIPASS 临时目录（内嵌配置）
    5. 脚本所在目录（开发时）
    """
    env_path = os.environ.get("AGENT_CONFIG")
    if env_path and os.path.isfile(env_path):
        return env_path

    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.dirname(sys.executable))
    candidates.append(os.getcwd())

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(meipass)

    candidates.append(os.path.dirname(os.path.abspath(__file__)))

    for d in candidates:
        p = os.path.join(d, "config.yaml")
        if os.path.isfile(p):
            return p
    return None



def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 覆盖 base"""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ============================================================================
# 加载配置
# ============================================================================

def _load():
    path = _find_config_path()
    user_cfg = {}
    if path:
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULTS, user_cfg), path


_cfg, _config_path = _load()


# ============================================================================
# 公开接口
# ============================================================================

def get(key: str, default=None):
    """点分路径访问，如 get("api.model")"""
    keys = key.split(".")
    val = _cfg
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return default
    return val


def get_tool_config(tool_name: str) -> dict:
    """获取某个工具的完整配置 dict"""
    return _cfg.get("tools", {}).get(tool_name, {"enabled": True})


def is_tool_enabled(tool_name: str) -> bool:
    """判断工具是否启用"""
    return get_tool_config(tool_name).get("enabled", True)


def get_mcp_servers() -> list:
    """获取已启用的 MCP 服务器配置列表"""
    return [s for s in _cfg.get("mcp_servers", []) if s.get("enabled", True)]


def get_config_path() -> str:
    """返回实际加载的配置文件路径，未找到返回 None"""
    return _config_path
