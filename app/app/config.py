# -*- coding: utf-8 -*-
"""配置加载：config.json 存非敏感设置，API Key 只从环境变量来。

密钥来源优先级：
  1. 环境变量 DASHSCOPE_API_KEY / LLM_API_KEY（推荐，线上用平台注入）
  2. 项目根目录 .env 文件（本地开发用，已加入 .gitignore）
  3. config.json 里的 apiKey 字段（仅为兼容旧部署保留，正式环境请留空）

config.json 中不含任何密钥，可以放心进版本库。
"""
import json
import os

from . import env as envmod

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_ROOT, "config.json")

KEY_ENVS = ("DASHSCOPE_API_KEY", "LLM_API_KEY")


def _resolve_key(cfg):
    """返回 (key, 来源标记)。来源仅用于健康检查排障，不暴露密钥本身。"""
    for name in KEY_ENVS:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v, "env:" + name
    v = (cfg.get("apiKey") or "").strip()
    if v:
        return v, "config.json"
    return "", "none"


def load_config():
    envmod.load()                     # 先吃 .env，再读 config.json
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    key, source = _resolve_key(cfg)
    cfg["apiKey"] = key
    cfg["keySource"] = source
    # 内部字段，不返回给前端
    cfg.pop("apiKeyRaw", None)
    if not cfg.get("baseUrl"):
        cfg["baseUrl"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cfg.setdefault("models", {})
    cfg.setdefault("options", {})
    cfg.setdefault("kb", {"top_k": 5, "max_context_chars": 4500})
    cfg.setdefault("memory", {"history_max": 30, "summary_keep": 20, "archive_days": 30})
    return cfg


def model(cfg, role):
    return cfg["models"].get(role, "qwen-flash")


def api_configured(cfg):
    return bool(cfg.get("apiKey"))


def key_hint(cfg):
    """给用户看的排障提示（不含密钥明文）。"""
    src = cfg.get("keySource") or "none"
    if src == "none":
        return ("未配置 API Key。请设置环境变量 DASHSCOPE_API_KEY"
                "（或在项目根目录新建 .env 写入 DASHSCOPE_API_KEY=sk-xxx）。")
    return "API Key 来源：" + src
