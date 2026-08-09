# tcc-mcp-config — Hermes Agent plugin

หนึ่ง Hermes gateway ให้บริการ **3 สภาพแวดล้อมพร้อมกัน** (`local` / `stg` / `prod`) โดยสร้าง
**โปรไฟล์ Hermes หนึ่งอันต่อผู้ใช้หนึ่งคน** อัตโนมัติ เพื่อแยก memory ต่อคน และฉีด principal
ที่เชื่อถือได้ให้ทุก TCC MCP call

ปลั๊กอินนี้ทำ 3 หน้าที่รวมในตัวเดียว (จงใจให้อยู่ด้วยกันจะได้ไม่หลุด sync):

| module | หน้าที่ |
|--------|--------|
| `environments` | เก็บ MCP URL / MCP key / gateway key ของ `local`/`stg`/`prod` แล้ว materialize ลงทุกโปรไฟล์ (แก้ผ่าน dashboard tab ได้) |
| `provisioner` | สร้างโปรไฟล์ `<env>-<staff\|user>-<id>` ครั้งแรกที่ผู้ใช้แชท + เพิ่ม endpoint `/internal/tcc-ai-assistant/profiles/ensure` เข้า gateway |
| `principal_injector` | ประทับ session key ที่ authenticate แล้วลงทุก MCP call (ถ้าไม่มี = fail-closed ตอบว่าง) |

พร้อม **dashboard tab "TCC MCP Config"** สำหรับตั้งค่า env + ดูรายชื่อโปรไฟล์ (ค้นหา/แบ่งหน้า/นับ user ต่อ env)

---

## สถาปัตยกรรม (สำคัญมากก่อนติดตั้ง)

```
tcc-admin (widget)  ──►  tcc-ai-assistant  ──►  Hermes gateway (+ ปลั๊กอินนี้)  ──►  tcc-api /mcp  ──►  DB
                          HERMES_BASE_URL       /p/<profile>/v1/chat/completions   Bearer MCP_TOKEN
                          HERMES_API_KEY        /internal/.../profiles/ensure
                          AI_ASSISTANT_ENVIRONMENT
```

ปลั๊กอินไม่ได้รันแยกเป็น sidecar — มัน **patch gateway ตอนโหลด** (เพิ่ม route + auto-provision + `/p/` routing + sync MCP servers) ดังนั้น gateway เปล่าๆ ที่ลงปลั๊กอินนี้จะเสิร์ฟ chat ได้เลย

---

## สิ่งที่ต้องมีอยู่ก่อน (Requirements)

- Hermes Agent gateway (image `nousresearch/hermes-agent`) ที่รันอยู่
- `gateway.multiplex_profiles: true`
- ตั้ง model/provider แล้ว + มี `~/.hermes/auth.json` (OAuth ของ provider) mount เข้า container
- `tcc-ai-assistant` (ตัวเรียก) และ `tcc-api` (ตัวเสิร์ฟ `/mcp`) รันอยู่และเข้าถึงกันได้

---

## ขั้นตอนติดตั้ง

### 1) วางปลั๊กอินลง Hermes
```bash
# วิธี A — ผ่าน Hermes CLI (clone จาก git)
hermes plugins install weratad/hermes-agent-tcc-mcp-config

# วิธี B — วางเอง
git clone https://github.com/weratad/hermes-agent-tcc-mcp-config.git \
  "$HERMES_HOME/plugins/tcc-mcp-config"
```
> ชื่อโฟลเดอร์ปลั๊กอินต้องเป็น `tcc-mcp-config` (ตรงกับ `name:` ใน `plugin.yaml`)

### 2) เปิดใช้งานใน `config.yaml`
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

### 3) ตั้งค่า environments (ขั้นตอนหัวใจ)

ตั้งได้ **2 วิธี — ผลเหมือนกัน** เพราะเก็บที่เดียว (`$HERMES_HOME/.env` ของ default profile)
UI แค่เป็นตัวช่วยแก้ `.env` ให้ (กด Save → เขียนลง `.env` → plugin อ่านจาก `.env` เดิมนั้น)

**วิธี A — ผ่าน Dashboard tab "TCC MCP Config" (แนะนำ)**
กรอก MCP URL / MCP API Key / Gateway Key ของแต่ละ env แล้วกด **Save** — ระบบเขียนลง `.env` ให้เอง
> badge **LIVE** = gateway ต่อ MCP ด้วยค่านี้อยู่ · ถ้าขึ้น **"รอ restart"** = บันทึกแล้วแต่ต้อง restart gateway ก่อน MCP ถึงจะต่อด้วยค่าใหม่ (Hermes ต่อ MCP ตอน start ครั้งเดียว)

**วิธี B — แก้ `$HERMES_HOME/.env` เอง** (เหมาะกับ script/bootstrap ครั้งแรก)
สำหรับแต่ละ env ที่จะใช้ (`LOCAL`/`STG`/`PROD`) ตั้ง 3 ค่า:
```dotenv
# ── ตัวอย่าง environment: local ──
TCC_MCP_URL_LOCAL=http://host.docker.internal:3363/mcp     # URL ของ tcc-api /mcp (ต้องเข้าถึงได้จากใน container)
TCC_MCP_KEY_LOCAL=<mcp-bearer>                             # bearer ที่ gateway ใช้ยิง tcc-api /mcp
TCC_GATEWAY_KEY_LOCAL=<gateway-key>                        # คีย์ที่ backend ผู้เรียกใช้เป็น HERMES_API_KEY
# ทำซ้ำสำหรับ STG / PROD ตามต้องการ (ดู .env.example)
```
> **ไม่ต้องทำทั้ง 2 วิธี** — เลือกอย่างใดอย่างหนึ่ง (ค่าลงที่ `.env` เดียวกัน) ใช้ UI อย่างเดียวก็พอ

### 4) Restart gateway
ปลั๊กอิน patch gateway ตอนโหลด (route + MCP sync ทำครั้งเดียวตอน start) ต้อง restart หลังเปิด/แก้ค่า
```bash
docker compose restart          # หรือวิธี restart gateway ของคุณ
```

---

## เชื่อมสองฝั่งให้ตรงกัน

### ฝั่งผู้เรียก — `tcc-ai-assistant` (`.env`)
```dotenv
HERMES_BASE_URL=http://<gateway-host>:<port>     # ชี้ gateway ที่ลงปลั๊กอินนี้
HERMES_API_KEY=<gateway-key>                     # = TCC_GATEWAY_KEY_<ENV> ของ env นั้น
AI_ASSISTANT_ENVIRONMENT=local                   # local | staging | production
```
มันจะเรียก `POST /p/<profile>/v1/chat/completions` และ `POST /internal/tcc-ai-assistant/profiles/ensure`
- ชื่อโปรไฟล์: `<env>-<staff|user>-<id>` เช่น `local-staff-3`
- header `X-Hermes-Session-Key`: `staff-<id>` หรือ `user-<id>-store-<storeId>`

### ฝั่งเสิร์ฟ — `tcc-api`
`POST /mcp` ต้อง guard ด้วย env `MCP_TOKEN`
```dotenv
MCP_TOKEN=<mcp-bearer>          # = TCC_MCP_KEY_<ENV> ของ env นั้น
```

---

## 🔑 3 คีย์ที่ต้องตรงกัน (จุดพลาดบ่อยสุด)

| ค่า | ตั้งที่ | ต้องเท่ากับ |
|-----|--------|------------|
| `TCC_MCP_URL_<ENV>` | gateway default `.env` | URL ของ tcc-api `/mcp` |
| `TCC_MCP_KEY_<ENV>` | gateway default `.env` | `MCP_TOKEN` ของ **tcc-api** |
| `TCC_GATEWAY_KEY_<ENV>` | gateway default `.env` | `HERMES_API_KEY` ของ **tcc-ai-assistant** |

ถ้า 3 ตัวนี้ไม่ตรง → chat จะ 401 หรือ "ไม่พร้อม" หรือ tool ตอบว่าง

---

## ตรวจสอบว่าใช้ได้ (Verify)

```bash
GW=http://127.0.0.1:18644
GK=$TCC_GATEWAY_KEY_LOCAL

# 1) provision profile
curl -s -X POST -H "Authorization: Bearer $GK" -H "Content-Type: application/json" \
  -d '{"environment":"local","type":"staff","id":1}' \
  "$GW/internal/tcc-ai-assistant/profiles/ensure"
# → {"profile":"local-staff-1","created":true}

# 2) chat จริง
curl -s -X POST -H "Authorization: Bearer $GK" -H "Content-Type: application/json" \
  -H "X-Hermes-Session-Key: staff-1" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"สวัสดี"}]}' \
  "$GW/p/local-staff-1/v1/chat/completions"
# → คำตอบจริง
```
และเปิด **dashboard tab "TCC MCP Config"** — การ์ด env ควรขึ้น "ใช้งานอยู่" + จำนวน user

---

## Dashboard tab

เสิร์ฟจาก `dashboard/` (`plugin_api.py` = FastAPI router + `dist/index.js` = UI แบบ plain IIFE)
รัน `hermes dashboard` แล้วจะเห็นแท็บ **TCC MCP Config**: ตั้งค่า env, รายชื่อโปรไฟล์ (ค้นหา/แบ่งหน้า), นับ user ต่อ env

> **Gotcha:** `hermes dashboard` มัก restart แล้วเหลือ process เก่าค้าง ทำให้ backend เสิร์ฟโค้ดเก่า
> รัน `hermes dashboard --stop` ให้หมดก่อน แล้วค่อยรันใหม่ (แก้ `plugin_api.py` ต้อง restart, แก้ `dist/index.js` แค่ reload หน้า)

---

## Troubleshooting

### Dashboard tab ขึ้น "Failed to load: 404 … /api/plugins/tcc-mcp-config/settings"
tab โหลด (เห็นหัวข้อ) แต่ API 404 = **dashboard ยังไม่ได้ mount `plugin_api.py`**
เพราะ dashboard mount plugin API ตอน **start เท่านั้น** — ถ้า dashboard รันอยู่ก่อน enable plugin จะยังไม่มี route
**แก้:** restart dashboard (ต้อง `--stop` ก่อน ไม่งั้น process เก่าค้างเสิร์ฟของเดิม)
```bash
hermes dashboard --stop
hermes dashboard --host 0.0.0.0 --port <port> --skip-build   # หรือ restart service/docker ของ dashboard
```
แล้ว hard-refresh หน้า (เช็คได้: `curl <dash>/api/plugins/tcc-mcp-config/settings` ควรเป็น **401** ไม่ใช่ 404)

### Chat ตอบ "ระบบผู้ช่วยยังไม่พร้อม" / 401
3 คีย์ไม่ตรงกัน (ดูตารางด้านบน) — เช็ค `HERMES_API_KEY` = `TCC_GATEWAY_KEY_<ENV>` และ `MCP_TOKEN` (tcc-api) = `TCC_MCP_KEY_<ENV>`

### เปลี่ยน MCP URL/key แล้วแต่ tool ยังใช้ค่าเก่า
ต้อง **restart gateway** (Hermes ต่อ MCP ตอน start ครั้งเดียว) — badge จะขึ้น "รอ restart"

---

## หมายเหตุ / ข้อจำกัด

- **เพดานโปรไฟล์:** `MAX_PROFILES = 5000` (ใน `environments.py`) — เกินนี้ ensure จะปฏิเสธ เพิ่มค่าถ้าต้องการ
- **การ resolve โปรไฟล์เป็น live directory scan** — เร็วมากที่หลักพัน แต่ที่หลักหมื่น+ scan/disk จะเริ่มมีผล ควรมี retention/archive โปรไฟล์ที่ inactive
- **คอขวด throughput = `max_concurrent_runs`** (จำนวนแชทพร้อมกัน) ไม่ใช่จำนวนโปรไฟล์รวม — เกิน limit จะได้ 429
- โปรไฟล์แต่ละคนมี memory/sessions/state.db แยกกันสมบูรณ์ (ไม่รั่วข้ามผู้ใช้)

---

## ทดสอบ (tests)
```bash
python -m pytest tests/          # ต้องมี HERMES_HOME ชี้ profile ทดสอบ
```

## License
Internal use — TCC
