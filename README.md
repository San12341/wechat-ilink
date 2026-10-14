# 微信 ClawBot ↔ Grok Bot 桥接（纯出站）

基于腾讯 `@tencent-weixin/openclaw-weixin` 2.4.10 的 iLink 协议：长轮询 `getupdates` 收消息，`sendmessage` 回纯文本。全程客户端主动 HTTPS，**不开公网端口**。

## 目录

| 路径 | 说明 |
| --- | --- |
| `bind.py` | 扫码绑定，写凭证 |
| `bridge.py` | 长轮询 → 白名单 → POST routine webhook |
| `reply.py` | 纯文本回微信 |
| `start.sh` / `stop.sh` / `status.sh` / `keepalive.sh` | 启停、状态与自动拉起 |
| `state/` | 凭证、游标、context_token、webhook.env（权限 700） |
| `logs/` | 运行日志：`bridge.log` / `reply.log` / `bind.log`（自动轮转，单文件约 2MB×5） |
| `research/` | 协议调研笔记与官方包（只读参考） |

## 一键依赖

```bash
cd /workspace/wechat-ilink
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 绑定

```bash
cd /workspace/wechat-ilink
.venv/bin/python3 bind.py
# 如遇 need_verifycode：
.venv/bin/python3 bind.py --verify-code 手机上的数字
```

会生成 `state/qrcode.png` 与备用链接 `state/qrcode_url.txt`。约 5 分钟有效。绑定成功后写 `credentials.json`、`bound_user.json`，并清空游标与 context_token。

**绑定后请先在微信 ClawBot 对话里发一句话**——回复必须带该消息的 `context_token`。

## 启动 / 停止 / 状态

```bash
./start.sh      # 若已在跑会先停再启；并确保 keepalive 在跑
./stop.sh
./status.sh
./keepalive.sh  # 也可单独启动守护（start.sh 会自动拉起）
```

`keepalive.sh` 每 60 秒检查 bridge：进程挂了且凭证仍有效时会自动 `./start.sh`。遇到错误码 -14（`STALE_TOKEN`）不会瞎重启，仍需重新扫码绑定。

云端电脑重启后：

```bash
cd /workspace/wechat-ilink
./start.sh
```

## 回复

```bash
.venv/bin/python3 reply.py --to '<ilink_user_id>' --text '你好'
# 或
.venv/bin/python3 reply.py --to '<ilink_user_id>' --text-file /tmp/reply.txt
```

只发纯文本（微信不渲染 Markdown）。若无 context_token 会报错退出。

## 错误码 -14（凭证过期）

`getupdates` / `sendmessage` 返回 `ret`/`errcode` = **-14** 时：

1. bridge 会写 `state/STALE_TOKEN` 标记并退出
2. **不要指望自动恢复**——请重新扫码：`.venv/bin/python3 bind.py`
3. 绑定成功后清空游标与 context_token，再 `./start.sh`，并在微信里再发一句

## 安全

- `state/webhook.env`、`credentials.json` 权限 600；目录 700
- 只处理 `bound_user.json` 中的那一个 `ilink_user_id`
- 切勿把 bot_token / webhook key 贴到聊天里

## 查看日志

```bash
tail -f logs/bridge.log   # 收消息 / 转发 webhook
tail -f logs/reply.log    # 回微信 sendmessage
tail -f logs/bind.log     # 扫码绑定
```

日志带级别（INFO/WARNING/ERROR）与 CST 时间戳，单文件超过约 2MB 会轮转保留最近 5 份。不写 token 等敏感内容。

## 已知限制

- 备用链接在微信内点开是否一定能完成绑定：官方未明确，需真机验证
- 服务端二维码真实有效期未公开（客户端本地 TTL 5 分钟）
- bot_token / context_token 有效期官方未公开
- 单条文本长度上限未找到官方数值
