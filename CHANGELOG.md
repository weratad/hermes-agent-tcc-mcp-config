# Changelog

รูปแบบตาม [Keep a Changelog](https://keepachangelog.com/) · เวอร์ชันตาม [SemVer](https://semver.org/)

## [2.4.1] — 2026-08-25

### Added
- โปรไฟล์ `organizer-<id>[-store-<id>]` — memory แยกจาก user คนเดียวกัน; MCP principal ถูก rewrite เป็น `user-<id>` เพราะ tcc-api ไม่มี type organizer
- Dashboard แยกการ์ด MCP config กับการ์ด Profiles

### Tests
- `python -m pytest tests/` ได้จริง (`pytest.ini` + `_sandbox.py` กันไม่ให้เทสเขียน `$HERMES_HOME` จริง)

[2.4.1]: https://github.com/weratad/hermes-agent-tcc-mcp-config/releases/tag/v2.4.1

## [2.4.0] — 2026-08-25

### Changed
- **ยุบ Local/Staging/Production เป็น MCP ช่องเดียว** — คีย์ `TCC_MCP_URL` / `TCC_MCP_KEY` / `TCC_GATEWAY_KEY` (ไม่มี suffix)
- ชื่อโปรไฟล์เป็น `staff-<id>` / `user-<id>[-store-<id>]` (ไม่ใส่ `local-` / `stg-` / `prod-`)
- MCP server บน default profile ชื่อ `tcc-api` เสมอ
- Dashboard การ์ดเดียว; `GET/PUT /settings` ไม่มี `environment`; `POST /internal/.../profiles/ensure` รับ `environment` แต่ไม่ใช้
- migrate ตอนอ่าน: ถ้าคีย์ใหม่ว่าง จะ copy จาก LOCAL → STG → PROD (และคีย์ v1) โดยไม่ทับของใหม่ ไม่ลบของเก่า

### Removed
- `ENVIRONMENTS` / การ์ดสามใบ / เช็ค “stg key cannot create prod profile”

[2.4.0]: https://github.com/weratad/hermes-agent-tcc-mcp-config/releases/tag/v2.4.0

## [2.3.1] — 2026-08-09

### Docs
- **อธิบายลำดับ deploy แบบ config-first** — enable ใน `config.yaml` **ก่อน** boot Hermes ครั้งแรก เพื่อไม่ให้เจอ 404 บน dashboard tab (route mount ตอน start เท่านั้น)
- เพิ่ม section "Deploy บน stg/prod แบบ config-first" (checklist copy-paste)
- ขยาย Troubleshooting 404: root cause (`_mount_plugin_api_routes` mount ครั้งเดียว + SPA catch-all กลืน), `rescan`/"Restart Gateway" ไม่ช่วย, curl เห็นแค่ 401 (auth gate ตอบก่อน routing) ต้องดูจากเบราว์เซอร์ที่ login

## [2.3.0] — 2026-08-09

### Added
- **Dashboard: รายชื่อโปรไฟล์ต่อ environment** — endpoint `GET /profiles`
  (query: `env`, `q` ค้นหา, `page`, `page_size`) อ่านแบบ read-only จาก directory scan
- **Dashboard: แสดงรายชื่อโปรไฟล์ในการ์ดแต่ละ group** พร้อม **ค้นหา** และ **แบ่งหน้า**
- **Dashboard: แสดงจำนวน user ต่อ environment** ในหัวการ์ด
- ปรับ UI dashboard tab ให้เรียบขึ้น (โทน Apple, รองรับ light/dark, สไตล์ scoped)

### Docs
- เพิ่ม `README.md` (สถาปัตยกรรม + ขั้นตอนติดตั้ง + config + verify + gotchas)
- เพิ่ม `.env.example`, `config.example.yaml`

## [2.2.0]

### Core
- รวม 3 ความสามารถไว้ในปลั๊กอินเดียว:
  - `environments` — MCP URL/key/gateway key ของ `local`/`stg`/`prod`
  - `provisioner` — สร้างโปรไฟล์ `<env>-<staff|user>-<id>` อัตโนมัติ + endpoint `/internal/tcc-ai-assistant/profiles/ensure`
  - `principal_injector` — ฉีด session key ที่ authenticate แล้วลงทุก MCP call (fail-closed)
- patch gateway ตอนโหลด (route + auto-provision + `/p/` routing + sync MCP servers) — ไม่ต้องมี sidecar

[2.3.1]: https://github.com/weratad/hermes-agent-tcc-mcp-config/releases/tag/v2.3.1
[2.3.0]: https://github.com/weratad/hermes-agent-tcc-mcp-config/releases/tag/v2.3.0
[2.2.0]: https://github.com/weratad/hermes-agent-tcc-mcp-config/releases/tag/v2.2.0
