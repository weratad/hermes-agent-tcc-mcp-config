"""Unit tests for deterministic price-compare marker formatting."""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from price_compare_format import (  # noqa: E402
    compose_price_compare_reply,
    format_price_compare_markers,
    has_price_compare_markers,
    is_price_compare_ask,
    needs_price_compare_rewrite,
    select_price_compare_event,
    usable_tiers,
)


def test_detect_th_and_en():
    assert is_price_compare_ask("เทียบราคาบัตร Sakon Festival")
    assert is_price_compare_ask("โซนไหนคุ้มสุด")
    assert is_price_compare_ask("Compare Sakon Festival 2026 ticket prices")
    assert not is_price_compare_ask("เทียบราคาร้าน A กับร้าน B")
    assert not is_price_compare_ask("เทียบราคาร้านในทองหล่อ")
    assert not is_price_compare_ask("แชร์ให้เพื่อนดู")


def test_usable_tiers_sorts_and_filters():
    raw = [
        {"zone": "VIP", "price_min": 10000},
        {"zone": "Early Bird", "price": 888},
        {"zone": "TBD", "price_min": None},
        {"name": "GA", "price_min": "1800"},
    ]
    tiers = usable_tiers(raw)
    assert len(tiers) == 3
    assert [t["price"] for t in tiers] == [888, 1800, 10000]
    assert tiers[0]["zone"] == "Early Bird"


def test_format_emits_scale_and_tickets():
    tiers = [
        {"zone": "Early Bird", "price_min": 888, "price_max": 888},
        {"zone": "VIP", "name": "VIP", "price_min": 10000, "price_max": 10000},
    ]
    reply = format_price_compare_markers(tiers, title="Sakon Festival 2026", venue="สกล")
    assert "⚖️" in reply
    assert reply.count("🎫") == 2
    assert "฿888" in reply or "888" in reply
    assert has_price_compare_markers(reply)
    assert "คุ้มสุด" in reply
    assert reply.count("คุ้มสุด") == 1
    assert "ได้ครับ เทียบราคาบัตรของ" not in reply
    assert "เหตุผลคือ" not in reply


def test_format_requires_two_usable_tiers():
    reply = format_price_compare_markers([{"zone": "Only", "price_min": 500}], title="Show")
    assert "⚖️" not in reply
    assert reply.count("🎫") < 2


def test_compose_keeps_full_model_prose_and_adds_markers():
    tiers = [
        {"zone": "GA", "price_min": 550},
        {"zone": "VIP", "price_min": 1500},
    ]
    model = (
        "คุ้มแบบไล่ตามงบ เลือกจากตำแหน่งที่อยากยืนได้เลย\n"
        "GA เหมาะกับคนงบน้อย\n"
        "VIP สมดุลกว่าถ้าอยากสบายขึ้น\n"
        "ราคา | จุดเด่น | จุดที่ต้องคิด\n"
        "550 | x | y"
    )
    reply = compose_price_compare_reply(model, tiers, title="Orbit Indie Fest")
    assert "คุ้มแบบไล่ตามงบ" in reply
    assert "GA เหมาะกับคนงบน้อย" in reply
    assert "VIP สมดุลกว่าถ้าอยากสบายขึ้น" in reply
    assert "ราคา | จุดเด่น" not in reply
    assert reply.count("🎫") == 2
    assert has_price_compare_markers(reply)


def test_compose_strips_zone_essay_fake_table():
    """Model reinvented Figma columns as prose — Hermes injects structural markers."""
    tiers = [
        {"zone": "GA", "price_min": 550},
        {"zone": "VIP", "price_min": 1200},
        {"zone": "VVIP", "price_min": 2200},
    ]
    model = (
        "เปรียบเทียบราคา Orbit Indie Fest\n\n"
        "GA: 550 บาท\n"
        "จุดเด่น: ราคาต่ำสุด เข้าถึงงานได้คุ้มสุด\n"
        "จุดที่ต้องคิด: สิทธิ์และมุมมองมักน้อยกว่าโซนสูง\n\n"
        "VIP: 1,200 บาท\n"
        "จุดเด่น: กลางๆ สมดุลระหว่างราคาและประสบการณ์\n"
        "จุดที่ต้องคิด: จ่ายเพิ่มจาก GA พอสมควร\n"
    )
    reply = compose_price_compare_reply(model, tiers, title="Orbit Indie Fest")
    assert "จุดเด่น:" not in reply
    assert "จุดที่ต้องคิด:" not in reply
    assert "GA: 550" not in reply
    assert has_price_compare_markers(reply)
    assert reply.count("🎫") == 3
    assert "⚖️" in reply
    # Model voice lifted into cells (Hermes finalize)
    assert "ราคาต่ำสุด เข้าถึงงานได้คุ้มสุด" in reply
    assert "สิทธิ์และมุมมองมักน้อยกว่าโซนสูง" in reply
    assert "กลางๆ สมดุลระหว่างราคาและประสบการณ์" in reply


def test_compose_keeps_model_markers_with_filled_cells():
    """Hermes principle: if model already emitted clean markers, do not rewrite."""
    model = (
        "งบไม่เยอะแนะนำเริ่ม GA ก่อน\n"
        "⚖️ เทียบ 2 ตัวเลือกที่น่าสนใจ\n"
        "🎫 ฿550|ถูกสุด เข้างานได้|มุมมองไกล|คุ้มสุด\n"
        "🎫 ฿1,200|สมดุลกว่|จ่ายเพิ่มจาก GA\n"
    )
    tiers = [
        {"zone": "GA", "price_min": 550},
        {"zone": "VIP", "price_min": 1200},
    ]
    reply = compose_price_compare_reply(model, tiers, title="Orbit")
    assert reply == model
    assert "ถูกสุด เข้างานได้" in reply


def test_compose_fills_blank_cons_cells():
    """Markers with จุดที่ต้องคิด = — must be rewritten with real copy."""
    model = (
        "เทียบราคาบัตร Night Market Live\n"
        "⚖️ เทียบ 3 ตัวเลือกที่น่าสนใจ\n"
        "🎫 ฿950|โซน GA|—|คุ้มสุด\n"
        "🎫 ฿2,100|โซน VIP|—\n"
        "🎫 ฿3,900|โซน VVIP|—\n"
    )
    tiers = [
        {"zone": "GA", "price_min": 950},
        {"zone": "VIP", "price_min": 2100},
        {"zone": "VVIP", "price_min": 3900},
    ]
    reply = compose_price_compare_reply(model, tiers, title="Night Market Live")
    assert has_price_compare_markers(reply)
    assert reply.count("🎫") == 3
    # No blank cons
    for line in reply.splitlines():
        if not line.strip().startswith("🎫"):
            continue
        cells = [c.strip() for c in line.replace("🎫", "", 1).strip().split("|")]
        assert len(cells) >= 3
        assert cells[2] not in ("", "—", "-")
    assert "สิทธิ์/มุมมอง" in reply
    assert "แพงกว่าโซนถูกสุด" in reply or "จ่ายเพิ่มจากโซนถูกสุด" in reply
    # Keep model intro above markers
    assert reply.splitlines()[0] == "เทียบราคาบัตร Night Market Live"


def test_compose_keeps_filled_pros_when_cons_blank():
    """Keep model จุดเด่น when already filled; only fill blank จุดที่ต้องคิด."""
    model = (
        "งบไม่เยอะแนะนำเริ่ม GA ก่อน\n"
        "⚖️ เทียบ 2 ตัวเลือกที่น่าสนใจ\n"
        "🎫 ฿550|ถูกสุด เข้างานได้|—|คุ้มสุด\n"
        "🎫 ฿1,200|สมดุลกว่|—\n"
    )
    tiers = [
        {"zone": "GA", "price_min": 550},
        {"zone": "VIP", "price_min": 1200},
    ]
    assert needs_price_compare_rewrite(model) is True
    reply = compose_price_compare_reply(model, tiers, title="Orbit")
    assert "ถูกสุด เข้างานได้" in reply
    assert "สมดุลกว่" in reply
    assert "ราคาเริ่มต้น" not in reply
    for line in reply.splitlines():
        if not line.strip().startswith("🎫"):
            continue
        cells = [c.strip() for c in line.replace("🎫", "", 1).strip().split("|")]
        assert cells[2] not in ("", "—", "-")


def test_needs_rewrite_for_missing_markers_or_blank_cells():
    filled = (
        "เกริ่น\n"
        "⚖️ เทียบ 2 ตัวเลือกที่น่าสนใจ\n"
        "🎫 ฿550|ถูกสุด|มุมมองไกล|คุ้มสุด\n"
        "🎫 ฿1,200|สมดุล|จ่ายเพิ่ม\n"
    )
    blank_cons = (
        "เกริ่น\n"
        "⚖️ เทียบ 2 ตัวเลือกที่น่าสนใจ\n"
        "🎫 ฿550|ถูกสุด|—|คุ้มสุด\n"
        "🎫 ฿1,200|สมดุล|—\n"
    )
    assert needs_price_compare_rewrite("plain prose only") is True
    assert needs_price_compare_rewrite(blank_cons) is True
    assert needs_price_compare_rewrite(filled) is False


def test_compose_varies_with_model_prose_for_same_tiers():
    tiers = [
        {"zone": "GA", "price_min": 550},
        {"zone": "VIP", "price_min": 1500},
    ]
    first = compose_price_compare_reply("เริ่มจากงบก่อน แล้วค่อยขยับโซน", tiers)
    second = compose_price_compare_reply("ถ้าเน้นบรรยากาศ ลองเทียบสองระดับนี้", tiers)
    assert first != second
    assert first.splitlines()[0] != second.splitlines()[0]


def test_compose_uses_short_default_without_usable_intro():
    tiers = [
        {"zone": "GA", "price_min": 550},
        {"zone": "VIP", "price_min": 1500},
    ]
    reply = compose_price_compare_reply("", tiers, title="Orbit Indie Fest", venue="Bravo BKK")
    assert reply.startswith("เทียบราคาบัตร Orbit Indie Fest\n⚖️")
    assert "ได้ครับ เทียบราคาบัตรของ" not in reply
    assert has_price_compare_markers(reply)


def test_compose_leaves_existing_markers_unchanged():
    existing = "เกริ่นเอง\n⚖️ เทียบ 2 ตัวเลือก\n🎫 ฿550|GA|ประหยัด\n🎫 ฿1,500|VIP|จ่ายเพิ่ม"
    assert compose_price_compare_reply(existing, []) == existing


def test_has_price_compare_markers_requires_structure():
    assert not has_price_compare_markers("plain prose")
    assert not has_price_compare_markers("⚖️ เทียบ 1 ตัวเลือก\n🎫 ฿100|a|b")
    good = format_price_compare_markers(
        [
            {"zone": "A", "price_min": 100},
            {"zone": "B", "price_min": 200},
        ],
        title="T",
        venue="V",
    )
    assert has_price_compare_markers(good)


def test_select_event_prefers_product_id_then_title():
    events = [
        {
            "title": "Other Festival",
            "product_id": 111,
            "ticket_tiers": [{"price_min": 100}, {"price_min": 200}],
        },
        {
            "title": "Sakon Festival",
            "product_id": 5973,
            "ticket_tiers": [{"price_min": 888}, {"price_min": 1288}],
        },
    ]
    assert select_price_compare_event(events, "เทียบ /concert/5973") is events[1]
    assert select_price_compare_event(events, "เทียบราคาบัตร Sakon") is events[1]
    assert select_price_compare_event(events, "เทียบราคาบัตรงานนี้") is None
