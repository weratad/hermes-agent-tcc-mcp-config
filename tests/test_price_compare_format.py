"""Unit tests for deterministic price-compare marker formatting."""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from price_compare_format import (  # noqa: E402
    format_price_compare_markers,
    has_price_compare_markers,
    is_price_compare_ask,
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


def test_format_requires_two_usable_tiers():
    reply = format_price_compare_markers([{"zone": "Only", "price_min": 500}], title="Show")
    assert "⚖️" not in reply
    assert reply.count("🎫") < 2


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
