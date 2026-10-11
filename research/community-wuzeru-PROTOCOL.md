# iLink Bot Protocol Notes

Observed surface of WeChat's iLink Bot API (`https://ilinkai.weixin.qq.com`).
This is empirical — gathered from real traffic during 2026, cross-checked
against public community references. It may drift as Tencent updates the
service.

## Base URL

- Default: `https://ilinkai.weixin.qq.com`
- After a successful QR confirmation, the response may include a `baseurl`
  field. **You must use this URL** for subsequent `getupdates` / `sendmessage`.
  Continuing to hit the default host will silently return empty `msgs`.
- Historic docs mentioning `api.ilink.bot` are wrong — that hostname typically
  fails DNS. `normalizeILinkBaseUrl()` forces it back to the canonical host.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET`  | `/ilink/bot/get_bot_qrcode?bot_type=3` | — | Get QR-code URL + qrcode token |
| `GET`  | `/ilink/bot/get_qrcode_status?qrcode=…` | — | Poll scan status |
| `POST` | `/ilink/bot/getupdates` | Bearer | Long-poll new messages |
| `POST` | `/ilink/bot/sendmessage` | Bearer | Send text or file message |
| `POST` | `/ilink/bot/upload` | Bearer (multipart) | Upload a file, get `media_id` |

## Headers (POST requests)

```
Content-Type: application/json            (or multipart/form-data for upload)
AuthorizationType: ilink_bot_token
Authorization: Bearer <bot_token>
X-WECHAT-UIN: <random base64>
```

All request bodies must include:

```json
{ "base_info": { "channel_version": "1.0.0" } }
```

Using `2.0.0` has been observed to cause abnormal behaviour.

## Error envelope

Every successful JSON response has `ret: 0`. Failures:

| Field | Meaning |
|---|---|
| `ret != 0` | Generic failure; `errmsg` carries detail. |
| `errcode: -14` | **Session expired.** The bot token is invalid; trigger re-login. This library throws `ILinkSessionExpiredError`. |

## Login flow

1. `GET /ilink/bot/get_bot_qrcode?bot_type=3`
   → `{ ret: 0, qrcode: "<nonce>", qrcode_img_content: "<URL>" }`
2. Display `qrcode_img_content` in the terminal; user scans in WeChat.
3. `GET /ilink/bot/get_qrcode_status?qrcode=<nonce>` (long-poll ~45s)
   → `{ status: "wait" | "scaned" | "confirmed" | "expired", … }`
4. On `confirmed`:
   ```json
   {
     "status": "confirmed",
     "bot_token": "…",
     "baseurl": "https://xxxx.weixin.qq.com",
     "ilink_user_id": "…",
     "ilink_bot_id": "…"
   }
   ```
   Use `bot_token` + `baseurl` for all subsequent calls.

## Message sync

```http
POST /ilink/bot/getupdates
{
  "get_updates_buf": "<cursor-or-empty>",
  "base_info": { "channel_version": "1.0.0" }
}
```

Response:

```json
{
  "ret": 0,
  "msgs": [ /* inbound messages */ ],
  "get_updates_buf": "<next-cursor>"
}
```

The field is sometimes `msgs`, `messages`, or `Msgs` depending on the lane —
this library reads all three. `next_key` has also been observed as an alias
for `get_updates_buf`.

## Inbound message shape

Core fields (with camelCase aliases also accepted):

```ts
{
  message_type: 1,            // 1 = user message; other values are ignored.
                              // Some lanes omit this — the library treats
                              // missing `message_type` as user message.
  message_id: "…" | <number>,
  from_user_id: "wxid_…",
  to_user_id: "<bot id>",
  context_token: "…",         // echo back on reply
  create_time_ms: 1234567890,
  item_list: [ /* see below */ ]
}
```

### Item types

Items inside `item_list` each have a `type`:

| `type` | Payload key | Meaning |
|---|---|---|
| `1` | `text_item.text` | Plain text |
| `2` | `image_item` | Image (CDN + AES) |
| `3` | `voice_item` | Voice; may carry transcribed `.text` |
| `4` | `file_item` | File (CDN + AES) |

Some payloads omit `type` but still include `file_item` / `image_item`. The
parser is shape-tolerant — presence of the payload key is sufficient.

### Media reference

`file_item` / `image_item` / `video_item` / `voice_item.media` all share:

```ts
{
  file_name: "a.pdf",
  aes_key: "<…>",
  media: { url: "<CDN URL>" } | { full_url: "<CDN URL>" } | { download_url: "…" }
}
```

Key fields may live on the item itself or inside `media.*`. The AES key has
been observed in three wire shapes:

1. 32-char hex string
2. Base64 of 16 raw bytes
3. **Base64 of the hex string** (double-encoded) — surprisingly common

`decodeWeChatAesKey` handles all three.

## Sending

### Text

```json
{
  "msg": {
    "from_user_id": "",
    "to_user_id": "<recipient>",
    "client_id": "<unique>",
    "message_type": 2,
    "message_state": 2,
    "context_token": "<echo from inbound>",
    "item_list": [ { "type": 1, "text_item": { "text": "hi" } } ]
  },
  "base_info": { "channel_version": "1.0.0" }
}
```

### File (two-step)

1. `POST /ilink/bot/upload` with `multipart/form-data` field `file` →
   `{ ret: 0, media_id: "…" }` (also seen as `mediaId`)
2. `sendmessage` with a `type=4` item:
   ```json
   {
     "type": 4,
     "file_item": {
       "file_name": "report.docx",
       "file_size": 12345,
       "file_ext": "docx",
       "media_id": "<from step 1>"
     }
   }
   ```

Filenames in multipart `Content-Disposition` must be stripped of CRLF / quotes
to prevent header injection (`sanitizeMultipartFileName`).

## CDN media download

The URL from inbound items points to a WeChat CDN (commonly
`novac2c.cdn.weixin.qq.com`). Response body is raw ciphertext; decrypt with
AES-128-ECB, PKCS#7 padding, using the decoded `aesKey`.

## Rate / size limits

Empirical only (not documented):

- Single file upload: ~80 MiB (`DEFAULT_MAX_DOWNLOAD_BYTES` caps the other
  direction too).
- Reply chunking: around 2000 chars per `sendText` call works reliably.
- Long-poll `getupdates` holds the connection up to ~30 s.

## Changelog of observed quirks

- **2026-Q1**: `msgs` field sometimes spelled `messages` depending on lane.
- **2026-Q1**: `file_item.media.full_url` supplants `file_item.url` for
  CDN-served files (`novac2c`).
- **2026-Q1**: AES keys observed as base64-of-hex more often than plain hex.
