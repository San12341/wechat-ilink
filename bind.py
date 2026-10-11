#!/usr/bin/env python3
"""扫码绑定微信 ClawBot（iLink）。跟官方 openclaw-weixin 2.4.10 流程。"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests

from common import (
    BOUND_USER_PATH,
    CREDENTIALS_PATH,
    DEFAULT_ILINK_BOT_TYPE,
    FIXED_BASE_URL,
    QR_LONG_POLL_TIMEOUT_S,
    QRCODE_PNG_PATH,
    QRCODE_URL_PATH,
    atomic_write_json,
    build_common_headers,
    build_post_headers,
    clear_cursor_and_context,
    clear_stale_token_marker,
    ensure_dirs,
    get_logger,
    normalize_baseurl,
)

# 本地会话 TTL（官方 ACTIVE_LOGIN_TTL_MS = 5 分钟）
ACTIVE_LOGIN_TTL_S = 5 * 60
MAX_QR_REFRESH_COUNT = 3
POLL_INTERVAL_S = 1.0


_log_impl = get_logger("bind")


def _log(msg: str) -> None:
    _log_impl.info("%s", msg)


def fetch_qrcode(session: requests.Session) -> dict:
    url = f"{FIXED_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type={quote(DEFAULT_ILINK_BOT_TYPE)}"
    headers = build_post_headers(token=None)  # 官方：不带 Authorization
    resp = session.post(
        url,
        headers=headers,
        json={"local_token_list": []},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("qrcode") or not data.get("qrcode_img_content"):
        raise RuntimeError(f"get_bot_qrcode 响应缺字段: keys={list(data.keys())}")
    return data


def write_qr_assets(qrcode_img_content: str) -> None:
    ensure_dirs()
    QRCODE_URL_PATH.write_text(qrcode_img_content + "\n", encoding="utf-8")
    try:
        import qrcode
    except ImportError as e:
        raise RuntimeError("缺少 qrcode 库，请先: .venv/bin/pip install -r requirements.txt") from e
    img = qrcode.make(qrcode_img_content)
    img.save(QRCODE_PNG_PATH)
    _log(f"二维码已写入: {QRCODE_PNG_PATH}")
    _log(f"备用链接（可在微信内尝试打开）: {qrcode_img_content}")
    _log("二维码大约 5 分钟有效（官方客户端本地 TTL）；过期会自动刷新，最多 3 次。")


def poll_status(
    session: requests.Session,
    base: str,
    qrcode: str,
    verify_code: str | None,
) -> dict:
    q = f"qrcode={quote(qrcode)}"
    if verify_code:
        q += f"&verify_code={quote(verify_code)}"
    url = f"{base.rstrip('/')}/ilink/bot/get_qrcode_status?{q}"
    headers = build_common_headers()  # 官方：仅应用头，无 Authorization / UIN
    try:
        resp = session.get(url, headers=headers, timeout=QR_LONG_POLL_TIMEOUT_S)
        # 网关超时等：当 wait
        if resp.status_code >= 500:
            _log(f"get_qrcode_status HTTP {resp.status_code}，当作 wait 继续")
            return {"status": "wait"}
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout:
        return {"status": "wait"}
    except requests.RequestException as e:
        _log(f"轮询网络错误，当作 wait: {e}")
        return {"status": "wait"}


def save_credentials(status: dict) -> None:
    bot_token = status.get("bot_token")
    ilink_bot_id = status.get("ilink_bot_id")
    ilink_user_id = status.get("ilink_user_id")
    baseurl = normalize_baseurl(status.get("baseurl"))
    if not bot_token or not ilink_bot_id:
        raise RuntimeError("confirmed 但缺少 bot_token 或 ilink_bot_id，绑定失败")
    if not ilink_user_id:
        raise RuntimeError("confirmed 但缺少 ilink_user_id，无法建立白名单")

    bound_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
    atomic_write_json(
        CREDENTIALS_PATH,
        {
            "bot_token": bot_token,
            "baseurl": baseurl,
            "ilink_bot_id": ilink_bot_id,
            "ilink_user_id": ilink_user_id,
            "bound_at": bound_at,
        },
    )
    atomic_write_json(BOUND_USER_PATH, {"ilink_user_id": ilink_user_id})
    clear_cursor_and_context()
    clear_stale_token_marker()
    _log("绑定成功。已写入 credentials.json / bound_user.json，并清空游标与 context_token。")
    _log(f"绑定用户 ilink_user_id 已保存（不在此打印完整 id）。")
    _log("请先在微信 ClawBot 对话里发一句话，回复需要用到该消息的 context_token。")


def run_bind(verify_code: str | None = None) -> int:
    ensure_dirs()
    session = requests.Session()
    refresh = 0
    pending_verify = verify_code

    while refresh <= MAX_QR_REFRESH_COUNT:
        _log(f"获取二维码（第 {refresh + 1}/{MAX_QR_REFRESH_COUNT + 1} 次）…")
        qr = fetch_qrcode(session)
        qrcode = qr["qrcode"]
        write_qr_assets(qr["qrcode_img_content"])

        base = FIXED_BASE_URL
        started = time.time()
        while time.time() - started < ACTIVE_LOGIN_TTL_S + 30:
            status = poll_status(session, base, qrcode, pending_verify)
            st = status.get("status") or "wait"
            if st == "wait":
                time.sleep(POLL_INTERVAL_S)
                continue
            if st == "scaned":
                _log("已扫码，等待手机确认…")
                time.sleep(POLL_INTERVAL_S)
                continue
            if st == "scaned_but_redirect":
                host = status.get("redirect_host")
                if host:
                    base = normalize_baseurl(host if str(host).startswith("http") else f"https://{host}")
                    _log(f"IDC 重定向，切换轮询 base → {base}")
                time.sleep(POLL_INTERVAL_S)
                continue
            if st == "need_verifycode":
                if not pending_verify:
                    _log("需要验证码：请用 --verify-code N 重新运行，或在下方输入手机上显示的数字。")
                    try:
                        pending_verify = input("验证码: ").strip() or None
                    except EOFError:
                        pending_verify = None
                    if not pending_verify:
                        _log("未提供验证码，退出。")
                        return 2
                time.sleep(POLL_INTERVAL_S)
                continue
            if st == "verify_code_blocked":
                _log("验证码错误次数过多（verify_code_blocked），请稍后再试。")
                return 3
            if st == "binded_redirect":
                _log("服务端返回 binded_redirect：本端可能已绑定过，未下发新凭证。请清除旧凭证后重试，或换账号。")
                return 4
            if st == "expired":
                _log("二维码已过期，尝试刷新…")
                break
            if st == "confirmed":
                save_credentials(status)
                return 0
            _log(f"未知状态 {st!r}，继续等待…")
            time.sleep(POLL_INTERVAL_S)
        else:
            _log("等待超时（约 5 分钟），刷新二维码…")

        refresh += 1
        pending_verify = verify_code  # 刷新后仍可用命令行验证码

    _log("二维码刷新次数用尽，绑定失败。请重新运行 python3 bind.py")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="微信 ClawBot 扫码绑定")
    parser.add_argument("--verify-code", default=None, help="手机上显示的验证码（need_verifycode 时）")
    args = parser.parse_args()
    try:
        return run_bind(verify_code=args.verify_code)
    except KeyboardInterrupt:
        _log("已取消")
        return 130
    except Exception as e:
        _log(f"绑定失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
