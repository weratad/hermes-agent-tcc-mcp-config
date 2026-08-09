# Changelog

รูปแบบตาม [Keep a Changelog](https://keepachangelog.com/) · เวอร์ชันตาม [SemVer](https://semver.org/)

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

[2.3.0]: https://github.com/weratad/hermes-agent-tcc-mcp-config/releases/tag/v2.3.0
[2.2.0]: https://github.com/weratad/hermes-agent-tcc-mcp-config/releases/tag/v2.2.0
