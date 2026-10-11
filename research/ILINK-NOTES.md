# 微信 ClawBot / iLink 接口调研笔记（只读调研，2026-10-11）

> 结论优先级：①腾讯官方 npm 包 `@tencent-weixin/openclaw-weixin@2.4.10` 源码（已 `npm pack` 解包到 `./package/`）> ②官方仓库协议文档 `docs/protocol_zh_CN.md`（已下载到 `./protocol_zh_CN.md`）> ③社区逆向文档（仅作补充，已下载为 `community-*.md`）。
> 本次未请求二维码、未发消息、未绑定。唯一的探测是不带任何凭证的 GET（见第 6 节）。

## 0. 出处

| 编号 | 来源 | URL / 本地路径 |
| --- | --- | --- |
| S1 | 官方 npm 包（MIT, author Tencent, v2.4.10, 2026-10-10 发布） | https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin ；tarball https://registry.npmjs.org/@tencent-weixin/openclaw-weixin/-/openclaw-weixin-2.4.10.tgz ；本地 `./package/` |
| S1a | 请求封装、请求头、getupdates/sendmessage | `package/src/api/api.ts` |
| S1b | 协议类型（字段、枚举） | `package/src/api/types.ts` |
| S1c | 扫码登录 | `package/src/auth/login-qr.ts` |
| S1d | -14 处理 | `package/src/api/session-guard.ts`、`package/src/monitor/monitor.ts` |
| S1e | 文本发送 | `package/src/messaging/send.ts` |
| S1f | context_token 持久化 | `package/src/messaging/inbound.ts` |
| S1g | 绑定后保存凭证 | `package/src/channel.ts`（loginWithQrWait） |
| S1h | 变更日志（-14 改名为 STALE_TOKEN） | `package/CHANGELOG.zh_CN.md` 2.4.6 节 |
| S2 | 官方协议文档 | https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md （英文版 protocol.md） |
| S3 | 社区博客：WeChat ClawBot iLink 协议解析 | https://www.chihao.li/blog/zh/wechat-clawbot-ilink-protocol |
| S4 | 社区协议规范 | https://github.com/Chrysochrome/ilink-bot-server/blob/main/docs/protocol-spec.md |
| S5 | 社区协议说明 | https://github.com/wuzeru/weixin-ilink-bot/blob/main/docs/PROTOCOL.md |
| S6 | 社区 API 参考 | https://github.com/qufei1993/cc-weixin/blob/main/docs/API-REFERENCE.md |

## 1. 公共约定

- Base URL：`https://ilinkai.weixin.qq.com`（S1 `src/auth/accounts.ts` DEFAULT_BASE_URL；S2）。扫码成功后服务端返回 `baseurl`，官方插件保存并在后续 Bot API 中优先使用它（S1g、`accounts.ts:393`）。S5 称不用返回的 baseurl 可能"静默收不到消息"（社区经验，未经官方证实）。
- 媒体 CDN：`https://novac2c.cdn.weixin.qq.com/c2c`（纯文本方案用不到）。
- HTTPS + JSON；普通接口 POST；二维码状态轮询 GET（S2）。
- 超时（S1a）：getupdates 客户端 35 000 ms；sendmessage 15 000 ms；getconfig/sendtyping/notify 10 000 ms。

### 请求头（S1a `buildHeaders`/`buildCommonHeaders`，S2 表格）

| Header | 值 | 用于 |
| --- | --- | --- |
| `Content-Type` | `application/json` | 所有 POST |
| `AuthorizationType` | `ilink_bot_token` | 所有 POST（含 get_bot_qrcode） |
| `Authorization` | `Bearer <bot_token>` | 鉴权的 Bot API（getupdates/sendmessage/…）；get_bot_qrcode 不带 |
| `X-WECHAT-UIN` | 随机 uint32 → 十进制字符串 → base64（每次请求新生成） | 所有 POST |
| `iLink-App-Id` | `bot`（package.json `ilink_appid`） | 所有请求（含 GET） |
| `iLink-App-ClientVersion` | 版本按 `0x00MMNNPP` 编码的十进制，如 2.4.10 → `132106` | 所有请求 |
| `SKRouteTag` | 可选，部署路由标签 | 可省略 |

> 社区实现（S3/S4/S5）常用 `iLink-App-ClientVersion: 1` 且不带 `iLink-App-Id`，也能工作——说明服务端对这两项可能不严格，但以官方为准。

### base_info（所有鉴权 POST 的 body 都带，S1a `buildBaseInfo`）

```json
{ "base_info": { "channel_version": "2.4.10", "bot_agent": "OpenClaw" } }
```
`bot_agent` 为 UA 风格自报标识（如 `GrokBotRelay/0.1`），仅用于观测，不参与鉴权（S2、README）。S5 称 channel_version 用 `2.0.0` 曾出现异常——社区说法，未验证。

### 返回码（S2）
- `ret: 0` 表示成功；失败时有 `ret`/`errcode`/`errmsg`，不是每个接口都有这些字段。
- HTTP 非 2xx 由 fetch 包装抛错；业务错误在 HTTP 200 里返回（第 6 节实测验证）。

## 2. 扫码绑定

### 2.1 获取二维码
官方（S1c `fetchQRCode`，S2）：
```http
POST https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3
Content-Type: application/json
AuthorizationType: ilink_bot_token
X-WECHAT-UIN: <random>
iLink-App-Id: bot
iLink-App-ClientVersion: 132106

{"local_token_list": []}
```
- `bot_type=3`：官方常量 `DEFAULT_ILINK_BOT_TYPE = "3"`，含义未公开（S3 同样指出）。
- `local_token_list`：本地已有的最近 ≤10 个 bot_token，可为空；用于服务端识别"已绑定本端"并返回 `binded_redirect`。
- 永远从固定地址 `https://ilinkai.weixin.qq.com` 发起（`FIXED_BASE_URL`）。
- 社区实现（S3/S4/S5/S6）用 **GET** 同一路径，也能拿到结果；官方 2.4.10 用 POST。建议跟官方。
- 官方不对此请求设客户端超时（CHANGELOG 2.1.4/2.3.1）。

响应：
```json
{ "qrcode": "<轮询用的二维码标识>", "qrcode_img_content": "<二维码内容 URL>" }
```

### 2.2 二维码内容是否是 URL、能否在微信内打开
- 官方：`qrcode_img_content` 被当作字符串传给 `qrcode-terminal` 渲染成二维码，同时原样打印为"若二维码未能显示或无法使用，你可以访问以下链接以继续：<url>"（S1c `displayQRCode`）。所以**它就是二维码要编码的内容本身，且官方把它当可访问的链接作为备用**。
- 形态：S3 观察为 `https://liteapp.weixin.qq.com/q/...`（S4 示例写作 `https://weixin.qq.com/x/...`，像是示意值）。
- "在手机微信里直接点开这个链接能否完成绑定"：**官方未明确说明，未找到权威验证**。官方文案只说"访问以下链接以继续"。实际可行性需在真实绑定时验证（把链接发给自己后在微信内点击）。
- 生成二维码图片：用 qrencode / Python `qrcode` 把 `qrcode_img_content` 字符串编码即可（不是 base64 图片）。

### 2.3 轮询扫码状态
官方（S1c `pollQRStatus`，S2）：
```http
GET {currentBase}/ilink/bot/get_qrcode_status?qrcode=<urlencoded qrcode>[&verify_code=<code>]
iLink-App-Id: bot
iLink-App-ClientVersion: 132106
```
- 只带应用头，**不带** Authorization/AuthorizationType/X-WECHAT-UIN（S2 明确）。
- 长轮询：客户端超时 35 s；超时或网络/网关错误（如 524）都当作 `wait` 继续；每轮间隔 1 s。
- 状态：`wait` / `scaned` / `confirmed` / `expired` / `need_verifycode`（需在 URL 追加 `verify_code=手机上显示的数字`）/ `verify_code_blocked` / `scaned_but_redirect`（把 base 切到 `https://<redirect_host>` 继续轮询）/ `binded_redirect`（已绑定过本端，不再下发新凭证）。

成功响应（S2；S3 给出了真实形态）：
```json
{
  "status": "confirmed",
  "bot_token": "xxxxxxxx@im.bot:0600....",
  "ilink_bot_id": "xxxxxxxx@im.bot",
  "baseurl": "https://ilinkai.weixin.qq.com",
  "ilink_user_id": "o9cq....@im.wechat"
}
```
- `bot_token`：后续 `Authorization: Bearer` 用。官方要求 `ilink_bot_id` 必须存在否则视为失败。
- `ilink_user_id`：扫码人的微信用户 id。官方注释"add to allowFrom"，即用它做"只认这一个人"的白名单（S1c `WeixinQrWaitResult.userId`）。官方保存 `{token, baseUrl, userId}`（S1g）。
- `bot_token` 有效期：**官方未公开**；S3 观察"一次扫码，长期有效"。

### 2.4 二维码有效期
- 官方客户端：`ACTIVE_LOGIN_TTL_MS = 5 * 60_000`（5 分钟，本地会话 TTL）；等待总时长默认 480 s；服务端返回 `expired` 时自动刷新，最多刷新到第 3 次即放弃（`MAX_QR_REFRESH_COUNT = 3`）。
- 服务端二维码真实有效期：**未找到官方数值**，响应中没有过期字段（S4 也确认）。以 `status: "expired"` 为准。

## 3. getupdates 长轮询收消息

```http
POST {baseurl}/ilink/bot/getupdates
Content-Type: application/json
AuthorizationType: ilink_bot_token
Authorization: Bearer <bot_token>
X-WECHAT-UIN: <random base64>
iLink-App-Id: bot
iLink-App-ClientVersion: 132106

{"get_updates_buf": "", "base_info": {"channel_version": "2.4.10", "bot_agent": "GrokBotRelay/0.1"}}
```
- 超时：客户端默认 35 s；若响应带 `longpolling_timeout_ms`（>0），下一轮用它（S1d monitor）。客户端超时（AbortError）当作 `{ret:0, msgs:[]}` 正常，直接下一轮。
- `sync_buf` 已废弃，只发 `get_updates_buf`。

响应（S1b 类型 + S2 + S3 示例）：
```json
{
  "ret": 0,
  "msgs": [
    {
      "seq": 1,
      "message_id": 7412345678901234567,
      "from_user_id": "o9cq....@im.wechat",
      "to_user_id": "xxxxxxxx@im.bot",
      "create_time_ms": 1760000000000,
      "session_id": "...",
      "message_type": 1,
      "message_state": 2,
      "item_list": [ { "type": 1, "text_item": { "text": "你好" } } ],
      "context_token": "AARz...=="
    }
  ],
  "get_updates_buf": "<不透明游标>",
  "longpolling_timeout_ms": 35000
}
```
- 文本：`item_list[i].type == 1` 时读 `text_item.text`；语音 `type == 3` 时 `voice_item.text` 是转写文字（S1b）。其他 type：2 图片、4 文件、5 视频、11/12 工具调用进度。
- `message_type`：1 = 用户，2 = Bot（自己发出的回显如出现需过滤）。
- `message_id`/`msg_id`/`svr_id` 是 uint64，官方用无损解析转字符串（S1a `parseWeixinApiJson`）——Python 原生 int 无此问题，JS 需注意。
- **游标推进**（S1d monitor、S2）：只有返回的 `get_updates_buf` 非空时才保存并替换；首次/重置传 `""`；官方把游标持久化到磁盘，重启后续传。游标是不透明 blob，不要解码修改（S4）。
- 失败重试（官方）：非 -14 失败等 2 s 重试，连续 3 次失败退避 30 s。
- 白名单：官方 monitor 注释 "allowFrom: only messages whose from_user_id is in this list are processed"。

## 4. sendmessage 回消息

```http
POST {baseurl}/ilink/bot/sendmessage
（请求头同 getupdates）

{
  "msg": {
    "from_user_id": "",
    "to_user_id": "o9cq....@im.wechat",
    "client_id": "grokbot-relay:1760000000000-1a2b3c4d",
    "message_type": 2,
    "message_state": 2,
    "item_list": [ { "type": 1, "text_item": { "text": "纯文本回复" } } ],
    "context_token": "AARz...=="
  },
  "base_info": { "channel_version": "2.4.10", "bot_agent": "GrokBotRelay/0.1" }
}
```
- 字段来源：S1e `buildTextMessageReq`（`from_user_id` 固定空串，`message_type: 2`=BOT，`message_state: 2`=FINISH，`client_id` 格式 `{prefix}:{毫秒时间戳}-{8位hex}`，每条消息唯一），`run_id` 可选。
- 响应：`{"ret":0,"errmsg":""}`，可能带 `message_id`；官方在 `ret` 非 0 时抛错（S1a）。
- 长文本：每条消息只放一个 item；媒体+说明文字拆成两次请求、各自新的 client_id（S1e、S2）。文本分片长度上限：**未找到官方数值**。
- **context_token 是否必须**：
  - 官方源码：缺少时只 `warn` 后照发（"sending without context"），S2 明确说"这不能证明服务端一定接受该请求"，并建议"回复对应会话时回传入站消息中的 context_token"。
  - 官方会以 `(accountId, userId)` 为 key 缓存**最近一条**入站消息的 context_token，并持久化到磁盘 `<stateDir>/openclaw-weixin/accounts/<accountId>.context-tokens.json` 以便重启后继续回复（S1f）。
  - 社区（S3/S4）：称"必须携带"，缺失时"通常直接拒绝或无法关联会话"；未观察到单独"打开会话"的 API。
  - 结论：实践上必须带对方最近一条消息的 context_token；绑定后需要用户先发一句话。context_token 的有效期：**未找到**。
- Markdown：官方对模型输出使用 `StreamingMarkdownFilter` 去掉部分不支持的标记（S1 `markdown-filter.ts`、CHANGELOG 2.1.7/2.1.8）——印证微信端不渲染 Markdown，本方案直接发纯文本即可。
- 可选：输入中提示 = `getconfig`(ilink_user_id, context_token) 取 `typing_ticket` → `sendtyping`(status 1/2)（S2）。非必需。
- 可选：启动/停止时 `POST /ilink/bot/msg/notifystart`、`/msg/notifystop`（body 只有 base_info），官方失败只记警告（S2、S1g）。

## 5. 错误码

| 码 | 出处 | 含义 / 处理 |
| --- | --- | --- |
| `ret: 0` | S2 | 成功 |
| `errcode`/`ret` = **-14**, `errmsg: "session timeout"` | S1b 注释 "-14 = session timeout"；S1d `STALE_TOKEN_ERRCODE = -14` 注释 "bot token is stale / expired"；S1h（2.4.6）"-14 表示 token 失效，而非 session 过期"；本次实测（第 6 节） | **bot_token 失效/无效**。官方处理：该账号所有收发暂停 1 小时（`SESSION_PAUSE_DURATION_MS`）后再试，不自动重新扫码。社区（S4/S5）建议：立刻停止、清除凭证与游标、重新扫码。**本方案建议**：收到 -14 → 停止轮询、提醒用户重新扫码；重新绑定后清空 get_updates_buf 与 context_token 缓存。 |
| `ret: -2` | 仅 S4 社区测试（GENERATING 状态 + 空 item_list） | 参数错误。官方未列出。 |
| 二维码 `status: expired` | S1c、S2 | 二维码过期，重新获取 |
| `need_verifycode` / `verify_code_blocked` | S1c、S2 | 需输入手机上显示的数字 / 输错太多次 |
| 其他非 0 | S2、S4 | **官方未公开错误码表**；原样记录、按 2 s / 30 s 退避重试 |
| HTTP 非 2xx（含 Cloudflare 524 等网关超时） | S1a、S1c | fetch 层抛错；QR 轮询当 wait，getupdates 计入失败重试 |

## 6. 只读探测（本机，2026-10-11 约 18:05 北京时间）

```
$ curl -s -o /dev/null -w '%{http_code}' https://ilinkai.weixin.qq.com/        -> 404（TLS 正常，域名可达）
$ curl -s -i https://ilinkai.weixin.qq.com/ilink/bot/getupdates  (GET，无任何凭证/请求体)
HTTP/1.1 200 OK
Content-Type: application/octet-stream
{"errcode":-14,"errmsg":"session timeout"}
```
- 证实：①云端电脑可出站访问 ilinkai.weixin.qq.com（经本机出站代理，DNS 解析为 198.18.0.1 代理地址）；②业务错误用 HTTP 200 + JSON 返回（Content-Type 为 octet-stream，解析时不要依赖 Content-Type）；③没有有效 token 时就是 -14 "session timeout"——即 -14 本质是"凭证无效/过期"。
- 未调用 get_bot_qrcode、未调用 sendmessage。

## 7. 是否只需出站 HTTPS
是。整套协议：扫码（POST get_bot_qrcode + GET get_qrcode_status 长轮询）、收消息（POST getupdates 长轮询）、发消息（POST sendmessage）全部是客户端主动发起的 HTTPS 请求（S2 "API 使用 HTTPS 传输 JSON"；S4 "getupdates 长轮询，不是 WebSocket，也不是双工流"；官方插件无任何 HTTP server/webhook 监听代码）。**不需要公网端口、不需要回调地址**。转发到 Grok Bot routine webhook 同样是出站 POST。

## 8. 可直接编码的最小流程（伪代码）

```text
# 绑定
r = POST /ilink/bot/get_bot_qrcode?bot_type=3  {"local_token_list":[]}
show QR(r.qrcode_img_content) + 备用链接 r.qrcode_img_content
loop (每轮 GET 超时 35s，间隔 1s，最长约 5 分钟):
  s = GET {base}/ilink/bot/get_qrcode_status?qrcode=<r.qrcode>
  wait/scaned -> 继续; scaned_but_redirect -> base = https://<redirect_host>
  expired -> 重新取码（最多 3 次）
  confirmed -> 保存 bot_token, baseurl, ilink_bot_id, ilink_user_id（chmod 600）; break

# 收
buf = load() or ""
loop:
  resp = POST {baseurl}/ilink/bot/getupdates {"get_updates_buf":buf,"base_info":{...}}  (timeout = resp.longpolling_timeout_ms or 35s)
  if resp.ret==-14 or resp.errcode==-14: 停止并提示重新扫码
  elif 非0: 2s 重试 / 连续3次退避30s
  if resp.get_updates_buf: buf = 它; save(buf)
  for m in resp.msgs:
     if m.message_type != 1 or m.from_user_id != 绑定的 ilink_user_id: 丢弃
     save context_token[m.from_user_id] = m.context_token
     text = join(item.text_item.text for type==1, item.voice_item.text for type==3)
     forward(text)

# 发
POST {baseurl}/ilink/bot/sendmessage {"msg":{from_user_id:"",to_user_id,client_id(唯一),message_type:2,message_state:2,item_list:[{type:1,text_item:{text}}],context_token:最近一条},"base_info":{...}}
检查 ret==0
```

## 9. 不确定点 / 未找到
1. `qrcode_img_content` 链接在手机微信内直接点开是否能完成绑定：官方只说"访问以下链接以继续"，未找到明确验证。
2. 服务端二维码真实有效期：未找到（官方客户端本地 TTL 为 5 分钟）。
3. bot_token、context_token 的有效期：未找到官方说明（社区观察 bot_token 长期有效）。
4. 不带 context_token 的 sendmessage 服务端是否接受：官方文档明确说"不能证明"；社区称会失败。按必须携带处理。
5. 完整错误码表：官方未公开；仅 -14 有官方定义，-2 为社区观测。
6. get_bot_qrcode 方法：官方 POST，社区 GET 也可用；以官方为准。
7. `iLink-App-Id` / `iLink-App-ClientVersion` 的严格程度未知；照官方发送最稳。
8. 单条文本长度上限：未找到。
9. ClawBot 插件可用范围：S3 称 iOS 微信 8.0.70+、逐步放量——社区说法，非官方。
