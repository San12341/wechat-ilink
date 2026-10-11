#!/usr/bin/env python3
"""Shared helpers for WeChat iLink ClawBot bridge. Fields follow official openclaw-weixin 2.4.10."""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
LOGS_DIR = ROOT / "logs"

CREDENTIALS_PATH = STATE_DIR / "credentials.json"
BOUND_USER_PATH = STATE_DIR / "bound_user.json"
CURSOR_PATH = STATE_DIR / "cursor.json"
CONTEXT_TOKENS_PATH = STATE_DIR / "context_tokens.json"
SEEN_IDS_PATH = STATE_DIR / "seen_ids.json"
WEBHOOK_ENV_PATH = STATE_DIR / "webhook.env"
STALE_TOKEN_PATH = STATE_DIR / "STALE_TOKEN"
BRIDGE_PID_PATH = STATE_DIR / "bridge.pid"
QRCODE_PNG_PATH = STATE_DIR / "qrcode.png"
QRCODE_URL_PATH = STATE_DIR / "qrcode_url.txt"

FIXED_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "2.4.10"
# 2.4.10 -> 0x00MMNNPP = (2<<16)|(4<<8)|10 = 132106
ILINK_APP_CLIENT_VERSION = "132106"
ILINK_APP_ID = "bot"
BOT_AGENT = "GrokBotRelay/0.1"
DEFAULT_ILINK_BOT_TYPE = "3"
STALE_TOKEN_ERRCODE = -14

GETUPDATES_TIMEOUT_S = 35.0
SENDMESSAGE_TIMEOUT_S = 15.0
QR_LONG_POLL_TIMEOUT_S = 35.0


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE_DIR, 0o700)
    except OSError:
        pass


def atomic_write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    tmp.replace(path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def load_webhook_env() -> dict[str, str]:
    """Load WECHAT_ROUTINE_WEBHOOK_URL / KEY from state/webhook.env (never print values)."""
    ensure_dirs()
    out: dict[str, str] = {}
    if WEBHOOK_ENV_PATH.is_file():
        for line in WEBHOOK_ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    # Prefer file; fall back to process env if file missing keys
    for key in ("WECHAT_ROUTINE_WEBHOOK_URL", "WECHAT_ROUTINE_WEBHOOK_KEY"):
        if not out.get(key):
            env_v = os.environ.get(key, "").strip()
            if env_v:
                out[key] = env_v
    if not out.get("WECHAT_ROUTINE_WEBHOOK_URL") or not out.get("WECHAT_ROUTINE_WEBHOOK_KEY"):
        raise RuntimeError(
            "缺少 webhook 配置：请确认 state/webhook.env 含 WECHAT_ROUTINE_WEBHOOK_URL 与 WECHAT_ROUTINE_WEBHOOK_KEY"
        )
    return out


def random_wechat_uin() -> str:
    """X-WECHAT-UIN: random uint32 -> decimal string -> base64 (official api.ts)."""
    uint32 = secrets.randbits(32)
    return base64.b64encode(str(uint32).encode("utf-8")).decode("ascii")


def build_common_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
    }


def build_post_headers(*, token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": random_wechat_uin(),
        **build_common_headers(),
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT}


def generate_client_id(prefix: str = "grokbot-relay") -> str:
    """Format: {prefix}:{ms}-{8hex} (official generateId)."""
    return f"{prefix}:{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def load_credentials() -> dict[str, Any] | None:
    data = read_json(CREDENTIALS_PATH)
    if not isinstance(data, dict):
        return None
    if not data.get("bot_token") or not data.get("ilink_bot_id"):
        return None
    return data


def load_bound_user_id() -> str | None:
    data = read_json(BOUND_USER_PATH)
    if isinstance(data, dict) and data.get("ilink_user_id"):
        return str(data["ilink_user_id"])
    creds = load_credentials()
    if creds and creds.get("ilink_user_id"):
        return str(creds["ilink_user_id"])
    return None


def load_cursor() -> str:
    data = read_json(CURSOR_PATH, default={})
    if isinstance(data, dict):
        buf = data.get("get_updates_buf")
        return buf if isinstance(buf, str) else ""
    return ""


def save_cursor(buf: str) -> None:
    atomic_write_json(CURSOR_PATH, {"get_updates_buf": buf})


def load_context_tokens() -> dict[str, str]:
    data = read_json(CONTEXT_TOKENS_PATH, default={})
    return data if isinstance(data, dict) else {}


def save_context_token(user_id: str, token: str) -> None:
    tokens = load_context_tokens()
    tokens[user_id] = token
    atomic_write_json(CONTEXT_TOKENS_PATH, tokens)


def clear_cursor_and_context() -> None:
    atomic_write_json(CURSOR_PATH, {"get_updates_buf": ""})
    atomic_write_json(CONTEXT_TOKENS_PATH, {})


def mark_stale_token() -> None:
    ensure_dirs()
    STALE_TOKEN_PATH.write_text(
        f"STALE_TOKEN (-14) at {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        "凭证失效，请重新运行: python3 bind.py\n",
        encoding="utf-8",
    )
    try:
        os.chmod(STALE_TOKEN_PATH, 0o600)
    except OSError:
        pass


def clear_stale_token_marker() -> None:
    if STALE_TOKEN_PATH.is_file():
        STALE_TOKEN_PATH.unlink()


def is_stale_errcode(body: dict[str, Any] | None) -> bool:
    if not isinstance(body, dict):
        return False
    for key in ("ret", "errcode"):
        val = body.get(key)
        if val == STALE_TOKEN_ERRCODE or val == str(STALE_TOKEN_ERRCODE):
            return True
    return False


def normalize_baseurl(url: str | None) -> str:
    if not url or not str(url).strip():
        return FIXED_BASE_URL
    u = str(url).strip().rstrip("/")
    if not u.startswith("http"):
        u = "https://" + u
    return u


def extract_message_text(msg: dict[str, Any]) -> str:
    """Join text from item_list: type 1 text_item.text, type 3 voice_item.text."""
    parts: list[str] = []
    items = msg.get("item_list") or []
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == 1:
            ti = item.get("text_item") or {}
            if isinstance(ti, dict) and ti.get("text"):
                parts.append(str(ti["text"]))
        elif t == 3:
            vi = item.get("voice_item") or {}
            if isinstance(vi, dict) and vi.get("text"):
                parts.append(str(vi["text"]))
    return "\n".join(parts).strip()


def message_id_str(msg: dict[str, Any]) -> str:
    for key in ("message_id", "msg_id", "svr_id"):
        if key in msg and msg[key] is not None:
            return str(msg[key])
    return ""


def load_seen_ids() -> list[str]:
    data = read_json(SEEN_IDS_PATH, default=[])
    if isinstance(data, list):
        return [str(x) for x in data]
    if isinstance(data, dict) and isinstance(data.get("ids"), list):
        return [str(x) for x in data["ids"]]
    return []


def remember_seen_id(msg_id: str, keep: int = 500) -> bool:
    """Return True if this id is new (not seen before). Persist recent 500."""
    if not msg_id:
        return True
    ids = load_seen_ids()
    if msg_id in ids:
        return False
    ids.append(msg_id)
    if len(ids) > keep:
        ids = ids[-keep:]
    atomic_write_json(SEEN_IDS_PATH, {"ids": ids})
    return True



_LOGGER_CACHE: dict[str, logging.Logger] = {}


class _CstFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.fromtimestamp(record.created, timezone(timedelta(hours=8)))
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S CST")


def get_logger(name: str, *, filename: str | None = None) -> logging.Logger:
    """按组件返回带轮转的运行日志。daemon（非 tty）只写文件，避免与 start.sh 重定向重复。"""
    if name in _LOGGER_CACHE:
        return _LOGGER_CACHE[name]

    ensure_dirs()
    logger = logging.getLogger(f"wechat_ilink.{name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    fmt = _CstFormatter("[%(asctime)s] %(levelname)s %(message)s")
    log_path = LOGS_DIR / (filename or f"{name}.log")
    fh = RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if sys.stdout.isatty():
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    _LOGGER_CACHE[name] = logger
    return logger
