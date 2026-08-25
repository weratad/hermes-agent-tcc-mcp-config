# tcc-mcp-config — Hermes Agent plugin

หนึ่ง Hermes gateway พูดกับ **tcc-api `/mcp` ช่องเดียว** และสร้าง **โปรไฟล์ Hermes หนึ่งอันต่อผู้ใช้หนึ่งคน**
อัตโนมัติ เพื่อแยก memory ต่อคน และฉีด principal ที่เชื่อถือได้ให้ทุก TCC MCP call

ชื่อโปรไฟล์:

- `staff-<id>` เช่น `staff-3`
- `user-<id>[-store-<storeId>]` เช่น `user-2520153-store-5002047`

ปลั๊กอินนี้ทำ 3 หน้าที่รวมในตัวเดียว (จงใจให้อยู่ด้วยกันจะได้ไม่หลุด sync):

| module | หน้าที่ |
|--------|--------|
| `environments` | เก็บ MCP URL / MCP key / gateway key แล้ว materialize ลงทุกโปรไฟล์ (แก้ผ่าน dashboard tab ได้) |
| `provisioner` | สร้างโปรไฟล์ `staff-<id>` / `user-<id>[-store-<id>]` ครั้งแรกที่ผู้ใช้แชท + เพิ่ม endpoint `/internal/tcc-ai-assistant/profiles/ensure` เข้า gateway |
| `principal_injector` | ประทับ session key ที่ authenticate แล้วลงทุก MCP call (ถ้าไม่มี = fail-closed ตอบว่าง) |

พร้อม **dashboard tab "TCC MCP Config"** สำหรับตั้งค่า MCP + ดูรายชื่อโปรไฟล์ (ค้นหา/แบ่งหน้า)

---

## สถาปัตยกรรม (สำคัญมากก่อนติดตั้ง)

```
tcc-admin (widget)  ──►  tcc-ai-assistant  ──►  Hermes gateway (+ ปลั๊กอินนี้)  ──►  tcc-api /mcp  ──►  DB
                          HERMES_BASE_URL       /v1/chat/completions               Bearer MCP_TOKEN
                          HERMES_API_KEY        /internal/.../profiles/ensure
```

`tcc-ai-assistant` ยิง `/v1/chat/completions` (ไม่ใช่ `/p/<env>-…/`). Isolation ของ memory มาจาก Hermes profile ที่ provisioner สร้างให้แต่ละ user

ปลั๊กอินไม่ได้รันแยกเป็น sidecar — มัน **patch gateway ตอนโหลด** (เพิ่ม route + auto-provision + `/p/` routing + sync MCP servers) ดังนั้น gateway เปล่าๆ ที่ลงปลั๊กอินนี้จะเสิร์ฟ chat ได้เลย

---

## สิ่งที่ต้องมีอยู่ก่อน (Requirements)

- Hermes Agent gateway (image `nousresearch/hermes-agent`) ที่รันอยู่
- `gateway.multiplex_profiles: true`
- ตั้ง model/provider แล้ว + มี `~/.hermes/auth.json` (OAuth ของ provider) mount เข้า container
- `tcc-ai-assistant` (ตัวเรียก) และ `tcc-api` (ตัวเสิร์ฟ `/mcp`) รันอยู่และเข้าถึงกันได้

---

## ขั้นตอนติดตั้ง

> ### ⚠️ ลำดับสำคัญที่สุด — enable ใน `config.yaml` **ก่อน** boot Hermes ครั้งแรก
> Hermes mount route ของปลั๊กอิน (`/api/plugins/tcc-mcp-config/…`) **ตอน start เท่านั้น** และเช็ค allow-list `plugins.enabled` ณ ตอนนั้น — เป็นพฤติกรรมของ Hermes core เอง (กระทบทุกปลั๊กอินที่เพิ่ม backend route ไม่ใช่แค่ตัวนี้)
> - **ทำถูกลำดับ** (ใส่ปลั๊กอิน + `plugins.enabled` ใน config **ก่อน** process เกิด) → mount ตั้งแต่ boot แรก → **ไม่ต้อง restart เพิ่ม ไม่เจอ 404**
> - **ทำผิดลำดับ** (enable บน instance ที่รันอยู่แล้ว) → tab โผล่แต่ `/settings` **404** จนกว่าจะ restart ทั้ง process (ปุ่ม "Restart Gateway" ในหน้า UI **ไม่ช่วย** — อันนั้น restart gateway ไม่ใช่ web-server ของ dashboard)
>
> **จึงควรใส่ config ให้ครบก่อน แล้วค่อย start** ตามลำดับข้อ 1→2→3 ด้านล่าง ไม่ใช่ start ก่อนแล้วเปิดทีหลัง

### 1) วางปลั๊กอินลง Hermes
```bash
# วิธี A — ผ่าน Hermes CLI (clone จาก git)
hermes plugins install weratad/hermes-agent-tcc-mcp-config

# วิธี B — วางเอง
git clone https://github.com/weratad/hermes-agent-tcc-mcp-config.git \
  "$HERMES_HOME/plugins/tcc-mcp-config"
```
> ชื่อโฟลเดอร์ปลั๊กอินต้องเป็น `tcc-mcp-config` (ตรงกับ `name:` ใน `plugin.yaml`)

### 2) เปิดใช้งานใน `config.yaml`  ← ทำ**ก่อน** start (ดู callout ด้านบน)
```yaml
plugins:
  enabled:
    - tcc-mcp-config
gateway:
  multiplex_profiles: true          # จำเป็น
  api_server:
    max_concurrent_runs: 16          # ปรับตามจำนวนคนแชทพร้อมกัน (ดู Notes)
model:
  default: gpt-5.6-sol               # ตัวอย่าง — ใช้ค่าของคุณ
  provider: openai-codex
platform_toolsets:
  api_server: '["memory"]'
```

### 3) ตั้งค่า MCP (ขั้นตอนหัวใจ)

ตั้งได้ **2 วิธี — ผลเหมือนกัน** เพราะเก็บที่เดียว (`$HERMES_HOME/.env` ของ default profile)
UI แค่เป็นตัวช่วยแก้ `.env` ให้ (กด Save → เขียนลง `.env` → plugin อ่านจาก `.env` เดิมนั้น)

**วิธี A — ผ่าน Dashboard tab "TCC MCP Config" (แนะนำ)**
กรอก MCP URL / MCP API Key / Gateway Key แล้วกด **Save** — ระบบเขียนลง `.env` ให้เอง
> badge **LIVE** = gateway ต่อ MCP ด้วยค่านี้อยู่ · ถ้าขึ้น **"Restart"** = บันทึกแล้วแต่ต้อง restart gateway ก่อน MCP ถึงจะต่อด้วยค่าใหม่ (Hermes ต่อ MCP ตอน start ครั้งเดียว)

**วิธี B — แก้ `$HERMES_HOME/.env` เอง** (เหมาะกับ script/bootstrap ครั้งแรก)
```dotenv
TCC_MCP_URL=https://api.example.com/mcp     # URL ของ tcc-api /mcp (ต้องเข้าถึงได้จากใน container)
TCC_MCP_KEY=<mcp-bearer>                    # bearer ที่ gateway ใช้ยิง tcc-api /mcp
TCC_GATEWAY_KEY=<gateway-key>               # คีย์ที่ backend ผู้เรียกใช้เป็น HERMES_API_KEY
```
> **ไม่ต้องทำทั้ง 2 วิธี** — เลือกอย่างใดอย่างหนึ่ง (ค่าลงที่ `.env` เดียวกัน) ใช้ UI อย่างเดียวก็พอ

ถ้ายังมีคีย์เก่า `TCC_MCP_URL_LOCAL` / `_STG` / `_PROD` ปลั๊กอินจะ **migrate ตอนอ่าน** ไปยังคีย์ไม่มี suffix (ไม่ลบของเก่า ไม่ทับคีย์ใหม่ที่ตั้งแล้ว)

### 4) Restart gateway
ปลั๊กอิน patch gateway ตอนโหลด (route + MCP sync ทำครั้งเดียวตอน start) ต้อง restart หลังเปิด/แก้ค่า
```bash
docker restart tcc-hermes          # หรือวิธี restart gateway ของคุณ
```

---

## 🚀 Deploy แบบ config-first (ไม่เจอ 404 ไม่ต้อง restart เพิ่ม)

ทำ **ตอน Hermes ยังไม่รัน / ก่อน start** เสมอ — เตรียมทุกอย่างให้ครบก่อน boot ครั้งแรก:

```bash
# 1) วางปลั๊กอิน (ตอน process ยังไม่ขึ้น)
git clone https://github.com/weratad/hermes-agent-tcc-mcp-config.git \
  "$HERMES_HOME/plugins/tcc-mcp-config"

# 2) enable ใน config.yaml — ต้องมีก่อน boot (ไม่งั้น route ไม่ mount → 404)
#    plugins.enabled: [tcc-mcp-config] + gateway.multiplex_profiles: true
#    (ดูตัวอย่างครบใน config.example.yaml)

# 3) ใส่ 3 คีย์ลง $HERMES_HOME/.env (ดู .env.example)
#    TCC_MCP_URL / TCC_MCP_KEY / TCC_GATEWAY_KEY

# 4) ค่อย start — boot แรกจะ mount route + sync MCP ให้เลย
hermes gateway run ...      # + hermes dashboard ...   (หรือ docker compose up -d)
```

> **ตรวจว่าถูก:** เปิด dashboard tab **TCC MCP Config** จากเบราว์เซอร์ที่ login แล้ว — เห็นการ์ด MCP + badge LIVE = mount สำเร็จตั้งแต่ boot แรก ไม่ต้อง restart ซ้ำ
> **ถ้าต้องแก้ config/คีย์ทีหลัง** = แก้แล้ว restart process ตามปกติ (`docker restart tcc-hermes`) — ไม่ใช่กดปุ่มใน UI

---

## เชื่อมสองฝั่งให้ตรงกัน

### ฝั่งผู้เรียก — `tcc-ai-assistant` (`.env`)
```dotenv
HERMES_BASE_URL=http://<gateway-host>:<port>     # ชี้ gateway ที่ลงปลั๊กอินนี้
HERMES_API_KEY=<gateway-key>                     # = TCC_GATEWAY_KEY
```
มันจะเรียก `POST /p/<profile>/v1/chat/completions` และ `POST /internal/tcc-ai-assistant/profiles/ensure`
- ชื่อโปรไฟล์: `staff-<id>` / `user-<id>[-store-<storeId>]` เช่น `staff-3`
- header `X-Hermes-Session-Key`: `staff-<id>` หรือ `user-<id>-store-<storeId>`

### ฝั่งเสิร์ฟ — `tcc-api`
`POST /mcp` ต้อง guard ด้วย env `MCP_TOKEN`
```dotenv
MCP_TOKEN=<mcp-bearer>          # = TCC_MCP_KEY
```

---

## 🔑 3 คีย์ที่ต้องตรงกัน (จุดพลาดบ่อยสุด)

| ค่า | ตั้งที่ | ต้องเท่ากับ |
|-----|--------|------------|
| `TCC_MCP_URL` | gateway default `.env` | URL ของ tcc-api `/mcp` |
| `TCC_MCP_KEY` | gateway default `.env` | `MCP_TOKEN` ของ **tcc-api** |
| `TCC_GATEWAY_KEY` | gateway default `.env` | `HERMES_API_KEY` ของ **tcc-ai-assistant** |

ถ้า 3 ตัวนี้ไม่ตรง → chat จะ 401 หรือ "ไม่พร้อม" หรือ tool ตอบว่าง

---

## ตรวจสอบว่าใช้ได้ (Verify)

```bash
GW=http://127.0.0.1:18644
GK=$TCC_GATEWAY_KEY

# 1) provision profile
curl -s -X POST -H "Authorization: Bearer $GK" -H "Content-Type: application/json" \
  -d '{"type":"staff","id":1}' \
  "$GW/internal/tcc-ai-assistant/profiles/ensure"
# → {"profile":"staff-1","created":true}

# 2) chat จริง
curl -s -X POST -H "Authorization: Bearer $GK" -H "Content-Type: application/json" \
  -H "X-Hermes-Session-Key: staff-1" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"สวัสดี"}]}' \
  "$GW/v1/chat/completions"
# → คำตอบจริง
```
และเปิด **dashboard tab "TCC MCP Config"** — การ์ดควรขึ้น "Live" + จำนวน user

---

## Dashboard tab

เสิร์ฟจาก `dashboard/` (`plugin_api.py` = FastAPI router + `dist/index.js` = UI แบบ plain IIFE)
รัน `hermes dashboard` แล้วจะเห็นแท็บ **TCC MCP Config**: ตั้งค่า MCP, รายชื่อโปรไฟล์ (ค้นหา/แบ่งหน้า)

> **Gotcha:** `hermes dashboard` มัก restart แล้วเหลือ process เก่าค้าง ทำให้ backend เสิร์ฟโค้ดเก่า
> รัน `hermes dashboard --stop` ให้หมดก่อน แล้วค่อยรันใหม่ (แก้ `plugin_api.py` ต้อง restart, แก้ `dist/index.js` แค่ reload หน้า)

---

## Troubleshooting

### Dashboard tab ขึ้น "Failed to load: 404 … /api/plugins/tcc-mcp-config/settings"
tab โหลด (เห็นหัวข้อ) แต่ API 404 = **dashboard ยังไม่ได้ mount `plugin_api.py`**

**Root cause:** Hermes core mount plugin API route ที่ `_mount_plugin_api_routes()` **ครั้งเดียวตอน web-server start** และวาง route ไว้ก่อน SPA catch-all `/{full_path:path}` — ถ้าตอน start ยังไม่ได้ enable plugin route จะไม่ถูก mount และ catch-all จะกลืน request เป็น 404 · `rescan` ทำให้ tab โผล่ (re-scan manifest) แต่ **ไม่ re-mount API** · ปุ่ม **"Restart Gateway"** ก็ไม่ช่วย (restart คนละ process)

**ป้องกัน (แนะนำ — วิธีที่ถูก):** enable ใน `config.yaml` **ก่อน** boot ครั้งแรก (ดู callout บนสุดของหัวข้อติดตั้ง) → mount ตั้งแต่ boot แรก ไม่มี 404 ไม่ต้อง restart เพิ่ม ให้ ship config พร้อม deploy แล้วค่อย start

**แก้เมื่อเผลอ enable บน instance ที่รันอยู่แล้ว:** restart **ทั้ง process ของ dashboard web-server** (`--stop` ก่อน ไม่งั้น process เก่าค้างเสิร์ฟของเดิม)
```bash
hermes dashboard --stop
hermes dashboard --host 0.0.0.0 --port <port> --skip-build   # หรือ restart service/docker ของ dashboard
```
แล้ว hard-refresh หน้า

> **เช็คสถานะไม่ได้ด้วย curl เปล่าๆ:** auth gate ตอบ **401** ก่อน routing เสมอ (กันคนนอก fingerprint ว่ามีปลั๊กอินอะไร) — ทั้งตอน 404 และตอนปกติ curl จะได้ 401 เหมือนกัน ต้องดูจาก **เบราว์เซอร์ที่ login แล้ว** (session ผ่าน auth) ถึงจะแยก 404 (ยังไม่ mount) กับ 200 (mount แล้ว) ได้จริง

### Chat ตอบ "ระบบผู้ช่วยยังไม่พร้อม" / 401
3 คีย์ไม่ตรงกัน (ดูตารางด้านบน) — เช็ค `HERMES_API_KEY` = `TCC_GATEWAY_KEY` และ `MCP_TOKEN` (tcc-api) = `TCC_MCP_KEY`

### เปลี่ยน MCP URL/key แล้วแต่ tool ยังใช้ค่าเก่า
ต้อง **restart gateway** (Hermes ต่อ MCP ตอน start ครั้งเดียว) — badge จะขึ้น "Restart"

---

## หมายเหตุ / ข้อจำกัด

- **เพดานโปรไฟล์:** `MAX_PROFILES = 5000` (ใน `environments.py`) — เกินนี้ ensure จะปฏิเสธ เพิ่มค่าถ้าต้องการ
- **การ resolve โปรไฟล์เป็น live directory scan** — เร็วมากที่หลักพัน แต่ที่หลักหมื่น+ scan/disk จะเริ่มมีผล ควรมี retention/archive โปรไฟล์ที่ inactive
- **คอขวด throughput = `max_concurrent_runs`** (จำนวนแชทพร้อมกัน) ไม่ใช่จำนวนโปรไฟล์รวม — เกิน limit จะได้ 429
- โปรไฟล์แต่ละคนมี memory/sessions/state.db แยกกันสมบูรณ์ (ไม่รั่วข้ามผู้ใช้)
- โปรไฟล์เก่าชื่อ `local-staff-*` / `stg-user-*` **ไม่ถูกลบ** — ปลั๊กอินชุดนี้แค่ไม่สร้างชื่อแบบนั้นอีก

---

## ทดสอบ (tests)

สคริปต์ใน `tests/` รันด้วย path ของปลั๊กอิน (ไม่ใช่ pytest fixtures). ชี้ `HERMES_HOME` ไปที่ temp profile เพื่อไม่แตะของจริง:

```bash
PLUGIN="$(pwd)"
HERMES_HOME="$(mktemp -d)" python tests/test_plugin.py "$PLUGIN"
HERMES_HOME="$(mktemp -d)" python tests/test_live.py "$PLUGIN"
HERMES_HOME="$(mktemp -d)" python tests/test_principal.py "$PLUGIN"
python -m pytest tests/test_suite.py          # ห่อสคริปต์ด้านบน; ข้าม patch/startup ถ้าไม่มี hermes
# ใน container Hermes:
python tests/test_patch.py "$PLUGIN"
python tests/test_startup.py "$PLUGIN"
```

## License
Internal use — TCC
