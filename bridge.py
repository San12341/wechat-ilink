#!/usr/bin/env python3
"""长轮询 getupdates → 白名单过滤 → POST routine webhook。纯出站。"""

from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from common import (
    GETUPDATES_TIMEOUT_S,
    STALE_TOKEN_ERRCODE,
    base_info,
    build_post_headers,
    ensure_dirs,
    extract_message_text,
    get_logger,
    is_stale_errcode,
    load_bound_user_id,
    load_credentials,
    load_cursor,
    load_webhook_env,
    mark_stale_token,
    message_id_str,
    normalize_baseurl,
    remember_seen_id,
    save_context_token,
    save_cursor,
)

FAIL_SLEEP_S = 2.0
BACKOFF_SLEEP_S = 30.0
MAX_CONSEC_FAILS = 3
log = get_logger("bridge")


def post_webhook(url: str, key: str, payload: dict) -> None:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code >= 400:
        # 再试常见变体（部分网关用 X-Api-Key / raw key）
        alt_headers = [
            {"Content-Type": "application/json", "Authorization": key},
            {"Content-Type": "application/json", "X-Api-Key": key},
        ]
        last_err = f"HTTP {r.status_code} body={r.text[:200]}"
        for h in alt_headers:
            r2 = requests.post(url, headers=h, json=payload, timeout=30)
            if r2.status_code < 400:
                log.info(f"webhook 备用鉴权成功 status={r2.status_code}")
                return
            last_err = f"HTTP {r2.status_code} body={r2.text[:200]}"
        raise RuntimeError(f"webhook POST 失败: {last_err}")
    log.info(f"webhook OK status={r.status_code} msg_id={payload.get('msg_id')}")


def get_updates(session: requests.Session, creds: dict, buf: str, timeout_s: float) -> dict:
    base = normalize_baseurl(creds.get("baseurl"))
    url = f"{base}/ilink/bot/getupdates"
    headers = build_post_headers(token=str(creds["bot_token"]))
    body = {"get_updates_buf": buf or "", "base_info": base_info()}
    try:
        resp = session.post(url, headers=headers, json=body, timeout=timeout_s)
    except requests.Timeout:
        # 客户端超时当作空结果（官方 AbortError → {ret:0, msgs:[]}）
        return {"ret": 0, "msgs": []}
    except requests.RequestException as e:
        raise RuntimeError(f"getupdates 网络错误: {e}") from e

    # 业务错误常在 HTTP 200 + octet-stream；仍尝试解析 JSON
    try:
        data = resp.json()
    except ValueError:
        if not resp.ok:
            raise RuntimeError(f"getupdates HTTP {resp.status_code}: {resp.text[:300]}")
        raise RuntimeError(f"getupdates 非 JSON: {resp.text[:300]}")

    if not resp.ok and not is_stale_errcode(data):
        raise RuntimeError(f"getupdates HTTP {resp.status_code}: {resp.text[:300]}")
    return data if isinstance(data, dict) else {"ret": -1, "msgs": []}


def handle_msgs(msgs: list, bound_uid: str, webhook_url: str, webhook_key: str) -> None:
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        mtype = msg.get("message_type")
        if mtype != 1:
            log.info(f"跳过非用户消息 message_type={mtype}")
            continue
        from_uid = str(msg.get("from_user_id") or "")
        if from_uid != bound_uid:
            log.info(f"跳过非白名单用户 from_user_id=***")
            continue
        text = extract_message_text(msg)
        ctx = msg.get("context_token")
        if ctx:
            save_context_token(from_uid, str(ctx))
        mid = message_id_str(msg)
        if mid and not remember_seen_id(mid):
            log.info(f"去重跳过 msg_id={mid}")
            continue
        if not text:
            log.info(f"空文本，仅保存 context_token msg_id={mid}")
            continue
        payload = {
            "from_user_id": from_uid,
            "text": text,
            "msg_id": mid,
            "context_token": str(ctx) if ctx else "",
            "received_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }
        try:
            post_webhook(webhook_url, webhook_key, payload)
        except Exception as e:
            log.error(f"转发 webhook 失败: {e}")


def main() -> int:
    ensure_dirs()
    creds = load_credentials()
    if not creds:
        log.error("缺少凭证。请先运行: python3 bind.py")
        return 1
    bound_uid = load_bound_user_id()
    if not bound_uid:
        log.error("缺少绑定用户 id（bound_user.json）。请重新 bind。")
        return 1
    try:
        wh = load_webhook_env()
    except RuntimeError as e:
        log.error(str(e))
        return 1
    webhook_url = wh["WECHAT_ROUTINE_WEBHOOK_URL"]
    webhook_key = wh["WECHAT_ROUTINE_WEBHOOK_KEY"]

    buf = load_cursor()
    timeout_s = GETUPDATES_TIMEOUT_S
    consec_fails = 0
    session = requests.Session()
    log.info("bridge 启动，开始长轮询 getupdates…")

    while True:
        try:
            data = get_updates(session, creds, buf, timeout_s)
        except Exception as e:
            consec_fails += 1
            log.error(f"getupdates 失败 ({consec_fails}): {e}")
            time.sleep(BACKOFF_SLEEP_S if consec_fails >= MAX_CONSEC_FAILS else FAIL_SLEEP_S)
            continue

        if is_stale_errcode(data):
            mark_stale_token()
            log.error(f"收到错误码 {STALE_TOKEN_ERRCODE}（凭证失效）。已写 STALE_TOKEN 标记。请重新扫码: python3 bind.py")
            return 14

        ret = data.get("ret", 0)
        if ret not in (0, None):
            consec_fails += 1
            log.warning(f"getupdates ret={ret} errmsg={data.get('errmsg')!r} ({consec_fails})")
            time.sleep(BACKOFF_SLEEP_S if consec_fails >= MAX_CONSEC_FAILS else FAIL_SLEEP_S)
            continue

        consec_fails = 0
        new_buf = data.get("get_updates_buf")
        if isinstance(new_buf, str) and new_buf:
            buf = new_buf
            save_cursor(buf)

        lp = data.get("longpolling_timeout_ms")
        if isinstance(lp, (int, float)) and lp > 0:
            timeout_s = float(lp) / 1000.0 + 1.0  # 略大于服务端建议

        msgs = data.get("msgs") or []
        if isinstance(msgs, list) and msgs:
            log.info(f"收到 {len(msgs)} 条消息")
            try:
                handle_msgs(msgs, bound_uid, webhook_url, webhook_key)
            except Exception:
                log.error("处理消息异常:\n" + traceback.format_exc())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("bridge 已停止")
        sys.exit(0)
