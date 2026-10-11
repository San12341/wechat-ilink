#!/usr/bin/env python3
"""纯文本 sendmessage 回微信。必须带对方最近一条消息的 context_token。"""

from __future__ import annotations

import argparse
import sys

import requests

from common import (
    SENDMESSAGE_TIMEOUT_S,
    STALE_TOKEN_ERRCODE,
    base_info,
    build_post_headers,
    generate_client_id,
    get_logger,
    is_stale_errcode,
    load_context_tokens,
    load_credentials,
    mark_stale_token,
    normalize_baseurl,
)

log = get_logger("reply")


def main() -> int:
    parser = argparse.ArgumentParser(description="向微信用户发送纯文本回复")
    parser.add_argument("--to", required=True, help="对方 ilink_user_id")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="纯文本内容")
    group.add_argument("--text-file", help="从文件读取纯文本（UTF-8）")
    args = parser.parse_args()

    if args.text_file:
        try:
            text = open(args.text_file, encoding="utf-8").read()
        except OSError as e:
            print(f"无法读取文本文件: {e}", file=sys.stderr)
            return 1
    else:
        text = args.text or ""

    # 微信不渲染 Markdown：调用方应传入纯文本；此处不做转换，原样发送
    text = text.strip("\n")
    if not text.strip():
        log.error("回复内容为空")
        print("回复内容为空", file=sys.stderr)
        return 1

    log.info("准备回复 to=%s chars=%d", args.to[:12] + "***", len(text))

    creds = load_credentials()
    if not creds:
        log.error("缺少凭证，无法 sendmessage")
        print("缺少凭证。请先运行: python3 bind.py", file=sys.stderr)
        return 1

    tokens = load_context_tokens()
    ctx = tokens.get(args.to)
    if not ctx:
        log.error("缺少 context_token，无法回复 to=%s***", args.to[:12])
        print(
            "没有该用户的 context_token，无法回复。"
            "请先在微信 ClawBot 对话里向机器人发一句话，等 bridge 收到后再试。",
            file=sys.stderr,
        )
        return 2

    base = normalize_baseurl(creds.get("baseurl"))
    url = f"{base}/ilink/bot/sendmessage"
    headers = build_post_headers(token=str(creds["bot_token"]))
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": args.to,
            "client_id": generate_client_id("grokbot-relay"),
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
            "context_token": ctx,
        },
        "base_info": base_info(),
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=SENDMESSAGE_TIMEOUT_S)
    except requests.RequestException as e:
        log.error("sendmessage 网络错误: %s", e)
        print(f"sendmessage 网络错误: {e}", file=sys.stderr)
        return 1

    try:
        data = resp.json()
    except ValueError:
        log.error("sendmessage 非 JSON HTTP %s: %s", resp.status_code, resp.text[:200])
        print(f"sendmessage 非 JSON HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return 1

    if is_stale_errcode(data):
        mark_stale_token()
        log.error("sendmessage 凭证失效 errcode=%s", STALE_TOKEN_ERRCODE)
        print(
            f"错误码 {STALE_TOKEN_ERRCODE}：凭证失效。请重新扫码绑定: python3 bind.py",
            file=sys.stderr,
        )
        return 14

    ret = data.get("ret", data.get("errcode"))
    if ret not in (0, None):
        log.error("sendmessage 失败 ret=%s errmsg=%r", ret, data.get("errmsg"))
        print(f"sendmessage 失败 ret={ret} errmsg={data.get('errmsg')!r}", file=sys.stderr)
        return 1

    log.info("sendmessage OK to=%s*** chars=%d", args.to[:12], len(text))
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
