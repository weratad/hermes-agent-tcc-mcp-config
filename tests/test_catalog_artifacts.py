"""Unit tests for catalog artifact mapping (no Hermes gateway required)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_plugin():
    plugin_dir = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "tcc_mcp_catalog_artifacts_test", plugin_dir / "catalog_artifacts.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.is_ai_ask_user_profile = lambda: True
    return mod


def test_find_events_enriches_only_matching_event_for_price_compare(monkeypatch) -> None:
    mod = _load_plugin()
    monkeypatch.setenv("TCC_ACTIVE_MCP_URL", "http://127.0.0.1:3333/mcp")
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return json.dumps(
                {
                    "data": {
                        "product_id": 5973,
                        "title": "Sakon Festival 2026",
                        "ticket_tiers": [
                            {"zone": "Early Bird", "price_min": 888},
                            {"zone": "Regular", "price_min": 1288},
                        ],
                    }
                }
            ).encode()

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    mod.store_last_user_text(["enrich-bundled"], "เทียบราคาบัตร Sakon Festival")
    mod.on_post_tool_call(
        tool_name="mcp__tcc_api__find_events",
        result={
            "items": [
                {
                    "product_id": 9999,
                    "title": "Unrelated Festival",
                    "ticket_tiers": [],
                },
                {
                    "product_id": 5973,
                    "title": "Sakon Festival 2026",
                    "ticket_tiers": [],
                }
            ]
        },
        session_id="enrich-bundled",
    )

    events = mod.take_events("enrich-bundled")
    assert calls == [("http://127.0.0.1:3333/ai-ask/events/5973", 5.0)]
    assert events[1]["ticket_tier_count"] == 2
    assert len(events[1]["ticket_tiers"]) == 2


def test_find_events_skips_enrichment_for_nightlife_compare(monkeypatch) -> None:
    mod = _load_plugin()
    calls = []
    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    mod.store_last_user_text(["nightlife-bundled"], "เทียบราคาร้านในทองหล่อ")
    mod.on_post_tool_call(
        tool_name="mcp__tcc_api__find_events",
        result={"items": [{"product_id": 5973, "title": "Sakon Festival 2026"}]},
        session_id="nightlife-bundled",
    )
    assert calls == []


def test_wire_inject_prefers_user_named_event() -> None:
    mod = _load_plugin()
    key = "wire-match-bundled"
    mod.store_last_user_text([key], "เทียบราคาบัตร Sakon Festival")
    mod.store_events(
        key,
        [
            {
                "title": "Other Festival",
                "ticket_tiers": [
                    {"zone": "A", "price_min": 100},
                    {"zone": "B", "price_min": 200},
                    {"zone": "C", "price_min": 300},
                ],
            },
            {
                "title": "Sakon Festival 2026",
                "ticket_tiers": [
                    {"zone": "Early Bird", "price_min": 888},
                    {"zone": "Regular", "price_min": 1288},
                ],
            },
        ],
    )
    completion = {
        "object": "chat.completion",
        "choices": [{"message": {"content": "มีหลายราคา"}}],
    }
    mod.attach_catalog_to_payload(completion, key)
    content = completion["choices"][0]["message"]["content"]
    # Thin model prose: keep voice; never invent price-pattern 🎫 cells
    assert "มีหลายราคา" in content
    assert "เข้างานได้ในงบต่ำสุด" not in content
    assert "สมดุลราคากับประสบการณ์" not in content
    assert "จ่ายเพิ่มจากตัวเลือกถูกสุดประมาณ" not in content


def test_wire_inject_skips_ambiguous_unrelated_events() -> None:
    mod = _load_plugin()
    key = "wire-ambiguous-bundled"
    mod.store_last_user_text([key], "เทียบราคาบัตรงานนี้")
    mod.store_events(
        key,
        [
            {
                "title": title,
                "ticket_tiers": [
                    {"zone": "A", "price_min": 100},
                    {"zone": "B", "price_min": 200},
                ],
            }
            for title in ("Alpha Live", "Beta Live")
        ],
    )
    completion = {
        "object": "chat.completion",
        "choices": [{"message": {"content": "ยังเลือกงานไม่ได้"}}],
    }
    mod.attach_catalog_to_payload(completion, key)
    assert completion["choices"][0]["message"]["content"] == "ยังเลือกงานไม่ได้"


def test_wire_inject_bare_ask_strips_fake_table() -> None:
    """P5: bare ask + single eligible event must not inject 🎫 markers."""
    mod = _load_plugin()
    key = "wire-bare-bundled"
    mod.store_last_user_text([key], "เทียบราคาบัตร")
    mod.store_events(
        key,
        [
            {
                "title": "Orbit Indie Fest",
                "ticket_tiers": [
                    {"zone": "GA", "price_min": 1350},
                    {"zone": "VIP", "price_min": 2700},
                    {"zone": "VVIP", "price_min": 4990},
                ],
            }
        ],
    )
    completion = {
        "object": "chat.completion",
        "choices": [
            {
                "message": {
                    "content": (
                        "เลือกงานที่สนใจก่อนได้เลย\n"
                        "⚖️ เทียบ 3 ตัวเลือกที่น่าสนใจ\n"
                        "🎫 ฿1,350|GA|ไกล|คุ้มสุด\n"
                        "🎫 ฿2,700|VIP|กลาง\n"
                        "🎫 ฿4,990|VVIP|แพง\n"
                    )
                }
            }
        ],
    }
    mod.attach_catalog_to_payload(completion, key)
    content = completion["choices"][0]["message"]["content"]
    assert "🎫" not in content
    assert "⚖️" not in content
    assert "เลือกงานที่สนใจก่อนได้เลย" in content
    layout = (completion.get("hermes") or {}).get("layout")
    assert layout != {"mode": "compare_value"}


def test_wire_inject_thin_event_strips_malformed_markers() -> None:
    """P6: named event with 0 usable tiers → honest no-table, no 🎫."""
    mod = _load_plugin()
    key = "wire-thin-bundled"
    mod.store_last_user_text(
        [key],
        "เทียบราคาบัตร Young K Solo Tour in BANGKOK https://www.tcc-stg.com/concert/3059",
    )
    mod.store_events(
        key,
        [
            {
                "title": "Young K Solo Tour in BANGKOK",
                "product_id": 3059,
                "ticket_tiers": [],
            }
        ],
    )
    completion = {
        "object": "chat.completion",
        "choices": [
            {
                "message": {
                    "content": (
                        "Young K Solo Tour in BANGKOK\n\n"
                        "⚖️ ราคา: ยังไม่มีข้อมูลบัตร/โซนในระบบ\n"
                        "🎫 จุดเด่น: งานจัดที่ Samyan Mitrtown Hall\n"
                        "🎫 จุดที่ต้องคิด: ไม่มี ticket_tiers\n"
                    )
                }
            }
        ],
    }
    mod.attach_catalog_to_payload(completion, key)
    content = completion["choices"][0]["message"]["content"]
    assert "🎫" not in content
    assert "⚖️" not in content
    assert "ยังไม่มีราคาแยก" in content
    layout = (completion.get("hermes") or {}).get("layout")
    assert layout != {"mode": "compare_value"}


def test_wire_inject_creates_message_on_compare_finish_chunk() -> None:
    mod = _load_plugin()
    key = "wire-finish-bundled"
    mod.store_last_user_text([key], "เทียบราคาบัตร Orbit Indie Fest")
    mod.store_events(
        key,
        [
            {
                "title": "Orbit Indie Fest",
                "ticket_tiers": [
                    {"zone": "Early Bird", "price_min": 990},
                    {"zone": "Regular", "price_min": 1490},
                ],
            }
        ],
    )
    chunk = {
        "object": "chat.completion.chunk",
        "choices": [{"delta": {}, "finish_reason": "stop"}],
    }

    mod.attach_catalog_to_payload(chunk, key)

    content = chunk["choices"][0]["message"]["content"]
    assert chunk["choices"][0]["message"]["role"] == "assistant"
    # Empty finish: defer to layout retry — no invented intro/table
    assert content == ""
    assert "เข้างานได้ในงบต่ำสุด" not in content
    assert chunk["hermes"]["layout"] == {"mode": "compare_value"}


def test_compose_lifts_freeform_bullets_into_cells():
    from price_compare_format import compose_price_compare_reply

    model = (
        "Orbit Indie Fest เทียบแล้วครับ\n"
        "• ได้เข้าร่วมงานนี้ในราคาต่ำสุด\n"
        "• VIP สมดุลงบกับฟีลงานชัดกว่า GA\n"
        "• VVIP แพงขึ้นชัด เหมาะคนที่อยากจัดเต็ม\n"
    )
    tiers = [
        {"zone": "GA", "price_min": 550},
        {"zone": "VIP", "price_min": 1200},
        {"zone": "VVIP", "price_min": 2200},
    ]
    reply = compose_price_compare_reply(model, tiers, title="Orbit Indie Fest")
    assert "ได้เข้าร่วมงานนี้ในราคาต่ำสุด" in reply
    assert "สมดุลงบกับฟีลงานชัดกว่า GA" in reply
    assert "ตัวเลือกถูกสุดในงานนี้" not in reply


def test_wire_inject_uses_streamed_model_voice_not_empty_canned() -> None:
    mod = _load_plugin()
    key = "wire-stream-voice"
    mod.store_last_user_text([key], "เทียบราคาบัตร Orbit Indie Fest")
    mod.store_events(
        key,
        [
            {
                "title": "Orbit Indie Fest",
                "ticket_tiers": [
                    {"zone": "GA", "price_min": 550},
                    {"zone": "VIP", "price_min": 1200},
                    {"zone": "VVIP", "price_min": 2200},
                ],
            }
        ],
    )
    mod.append_assistant_stream_text(
        [key],
        "Orbit คุ้มแบบไล่ตามงบ\n"
        "GA 550 บาท — ถูกสุด เหมาะถ้าอยากลองงานนี้แบบคุมงบ\n"
        "VIP 1,200 บาท — จ่ายเพิ่มเพื่อความสบายขึ้น\n"
        "VVIP 2,200 บาท — แพงสุด เหมาะถ้าเน้นพรีเมียม\n",
    )
    chunk = {
        "object": "chat.completion.chunk",
        "choices": [{"delta": {}, "finish_reason": "stop"}],
    }
    mod.attach_catalog_to_payload(chunk, key)
    content = chunk["choices"][0]["message"]["content"]
    assert "ถูกสุด เหมาะถ้าอยากลองงานนี้แบบคุมงบ" in content
    assert "ตัวเลือกถูกสุดในงานนี้" not in content
    assert content.count("🎫") == 3


def test_payload_session_keys_reads_finish_chunk_hermes_aliases() -> None:
    mod = _load_plugin()

    keys = mod._payload_session_keys(
        {
            "session_id": "top-session",
            "hermes": {
                "session_id": "nested-session",
                "session_key": "nested-key",
                "api_request_id": "nested-request",
                "task_id": "nested-task",
            },
        }
    )

    assert keys == [
        "top-session",
        "nested-session",
        "nested-key",
        "nested-request",
        "nested-task",
    ]


def main() -> None:
    plugin_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("tcc_catalog_artifacts", plugin_dir / "catalog_artifacts.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.is_ai_ask_user_profile = lambda: True

    events = mod.map_catalog_items(
        [
            {
                "title": "Show A",
                "start_at": "2026-10-01T12:00:00.000Z",
                "venue": "Impact",
                "product_id": 42,
                "id": "evt-42",
                "poster_url": "https://cdn.example/a.jpg",
                "artists": [{"display_name": "Band"}],
                "price_min": 1000,
                "price_max": 3000,
            },
            {
                "title": "Show B",
                "ticket_url": "https://ticket.example/b",
            },
        ]
    )
    assert events[0]["title"] == "Show A"
    assert events[0]["meta"] == "2026-10-01 · Impact"
    assert events[0]["date"] == "2026-10-01"
    assert events[0]["venue"] == "Impact"
    assert events[0]["url"] == "/concert/42"
    assert events[0]["id"] == "evt-42"
    assert events[0]["product_id"] == 42
    assert events[0]["image"] == "https://cdn.example/a.jpg"
    assert events[0]["artists"] == ["Band"]
    assert events[0]["price"] == "1000–3000"
    assert events[1]["url"] == "https://ticket.example/b"

    # Prefer absolute ticket_url from tool (TCC concert page) over rebuilding path.
    with_ticket = mod.map_catalog_item(
        {
            "title": "NCT",
            "product_id": 3000,
            "ticket_url": "http://localhost:3334/concert/3000",
            "poster_url": "https://cdn.example/n.jpg",
        }
    )
    assert with_ticket["url"] == "http://localhost:3334/concert/3000"

    one = mod.events_from_tool_payload(
        {
            "id": 42,
            "product_id": 42,
            "title": "Detail Show",
            "venue_name": "Impact",
            "start_at": "2026-11-01T00:00:00.000Z",
            "poster_url": "https://cdn.example/d.jpg",
        },
        layout="poster",
    )
    assert len(one) == 1
    assert one[0]["title"] == "Detail Show"
    assert one[0]["image"] == "https://cdn.example/d.jpg"
    assert one[0]["layout"] == "poster"
    assert one[0]["url"] == "/concert/42"
    assert one[0]["venue"] == "Impact"
    assert mod.layout_for_tool("mcp__tcc_api__get_event") == "poster"
    assert mod.layout_for_tool("mcp__tcc_api__find_events") == "card"

    listed = mod.events_from_tool_payload(
        {"total": 1, "items": [{"title": "List", "product_id": 1, "poster_url": "https://cdn.example/l.jpg"}]},
        layout="card",
    )
    assert listed[0]["layout"] == "card"
    assert listed[0]["venue"] == "" or "venue" in listed[0]

    assert mod.events_from_tool_payload({"notFound": True}) == []

    payload = mod.parse_tool_payload('{"total":1,"items":[{"title":"X","product_id":9}]}')
    assert payload["items"][0]["title"] == "X"

    wrapped = mod.parse_tool_payload(
        '{"result":{"total":1,"items":[{"title":"Wrapped","product_id":7,"poster_url":"https://cdn.example/w.jpg"}]}}'
    )
    assert wrapped["items"][0]["title"] == "Wrapped"
    cards = mod.events_from_tool_payload(wrapped)
    assert cards[0]["image"] == "https://cdn.example/w.jpg"

    nested = mod.parse_tool_payload(
        '{"result":"{\\"total\\":1,\\"items\\":[{\\"title\\":\\"Nested\\",\\"product_id\\":8}]}"}'
    )
    assert nested["items"][0]["title"] == "Nested"

    assert mod._TOOL_RE.match("mcp__tcc_api__find_events")
    assert mod._TOOL_RE.match("mcp__tcc_api__get_event")
    assert mod._TOOL_RE.match("mcp__tcc_api_stg__search_events")

    mod.store_events("sess-1", events)
    out = {"object": "chat.completion", "choices": []}
    mod.attach_catalog_to_payload(out, "sess-1")
    assert out["hermes"]["catalog"]["events"][0]["title"] == "Show A"
    assert mod.take_events("sess-1") == []

    zero = mod.map_catalog_item(
        {
            "title": "STARRY",
            "product_id": 5961,
            "price_min": 790,
            "price_max": 2990,
            "ticket_tier_count": 0,
        }
    )
    assert zero["ticket_tier_count"] == 0

    from_tiers = mod.map_catalog_item(
        {
            "title": "Echo",
            "product_id": 3080,
            "ticket_tiers": [
                {"zone": "GA", "price_min": 1350},
                {"zone": "VIP", "price_min": 2700},
            ],
        }
    )
    assert from_tiers["ticket_tier_count"] == 2

    posters = mod.events_from_tool_payload(
        {
            "title": "Echo",
            "product_id": 3080,
            "poster_url": "https://cdn.example/e.jpg",
            "ticket_tiers": [
                {"zone": "GA", "price_min": 1},
                {"zone": "VIP", "price_min": 2},
            ],
        },
        layout="poster",
    )
    assert posters[0]["layout"] == "poster"
    assert posters[0]["ticket_tier_count"] == 2

    compare_events = [
        {
            "title": "Sakon Festival 2026",
            "venue": "Sakon Hall",
            "layout": "poster",
            "ticket_tiers": [
                {"zone": "Early Bird", "price_min": 888},
                {"zone": "Regular", "price_min": 1288},
            ],
        }
    ]
    mod.store_last_user_text(["compare-session"], "เทียบราคาบัตร Sakon Festival")
    mod.store_events("compare-session", compare_events)
    completion = {
        "object": "chat.completion",
        "choices": [{"message": {"content": "มีบัตร 2 ราคา"}}],
    }
    mod.attach_catalog_to_payload(completion, "compare-session")
    content = completion["choices"][0]["message"]["content"]
    assert "⚖️" in content
    assert content.count("🎫") == 2
    assert completion["hermes"]["layout"] == {"mode": "compare_value"}

    print("tcc-catalog-artifacts ok")


if __name__ == "__main__":
    main()
