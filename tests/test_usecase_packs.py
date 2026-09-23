from pathlib import Path
import importlib.util
import sys


def load():
    root = Path(__file__).resolve().parent.parent
    name = "tcc_usecase_packs_test"
    spec = importlib.util.spec_from_file_location(name, root / "usecase_packs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_detail_pack():
    m = load()
    pack = m.select_pack("event_detail", [{"title": "LOSO", "url": "/concert/1"}])
    assert pack is not None
    assert pack["choices"] == m.DETAIL_CHOICES


def test_similar_pack_is_home_shortcuts():
    m = load()
    pack = m.select_pack("similar_cards", [{"title": "A", "url": "/concert/1"}])
    assert pack["choices"] == m.HOME_SIMILAR_CHOICES


def test_compare_zone_pack():
    m = load()
    pack = m.select_pack("compare_zone", [{"title": "A", "url": "/concert/1", "price": "1000–4000"}])
    assert "อยากได้โซนคุ้มสุด" in pack["choices"]


def test_budget_pack_uses_prices():
    m = load()
    pack = m.select_pack(
        "compare_value",
        [{"title": "A", "url": "/concert/1", "price": "3000–4000"}],
        user_text="ถ้างบประมาณ 3,000-4,000 ซื้อโซนไหนดี",
    )
    assert any("ซื้อบัตร" in c and "บาท" in c for c in pack["choices"])
    assert any("ส่งให้เพื่อน" in c for c in pack["choices"])


def test_budget_pack_without_prices():
    m = load()
    pack = m.select_pack(
        "compare_zone",
        [{"title": "A", "url": "/concert/1"}],
        user_text="ถ้างบประมาณ 3,000-4,000 ซื้อโซนไหนดี",
    )
    assert any("ซื้อบัตร" in c for c in pack["choices"])
    assert any("ส่งให้เพื่อน" in c for c in pack["choices"])


def test_nightlife_mood_pack():
    m = load()
    pack = m.select_pack(
        "prose",
        [],
        user_text="ศุกร์นี้อยากหาที่เที่ยวกลางคืน มีดนตรีสด",
    )
    assert pack["choices"] == m.MOOD_CHOICES


def test_venue_cards_booking_pack():
    m = load()
    events = [
        {"title": "Alpha Bar", "url": "/store/1"},
        {"title": "Beta Club", "url": "/store/2"},
        {"title": "Gamma Live", "url": "/store/3"},
    ]
    pack = m.select_pack("venue_cards", events)
    assert any("เปรียบเทียบเพลงและบรรยากาศทั้ง 3 ร้าน" == c for c in pack["choices"])
    assert any(c.startswith("จอง Alpha") for c in pack["choices"])


def test_compare_store_pack():
    m = load()
    events = [
        {"title": "Alpha Bar", "url": "/store/1"},
        {"title": "Beta Club", "url": "/store/2"},
    ]
    pack = m.select_pack("compare_store", events)
    assert all(c.startswith("จอง ") for c in pack["choices"])


def test_compare_store_pack_without_titles():
    m = load()
    pack = m.select_pack("compare_store", [])
    assert pack["choices"]
    assert any("จอง" in c for c in pack["choices"])


def test_suggested_layout_stores_from_prose():
    m = load()
    events = [{"title": "Alpha Bar", "url": "/store/1"}]
    assert m.suggested_layout("prose", events) == "venue_cards"
    assert (
        m.suggested_layout("prose", events, user_text="เปรียบเทียบเพลงและบรรยากาศ")
        == "compare_store"
    )


def test_suggested_layout_similar_from_list():
    m = load()
    events = [
        {"title": "A", "url": "/concert/1"},
        {"title": "B", "url": "/concert/2"},
    ]
    assert (
        m.suggested_layout("event_list", events, user_text="คอนเสิร์ตแนวเพลงเดียวกัน")
        == "similar_cards"
    )


def test_pack_matches_half():
    m = load()
    assert m.pack_matches(["ซื้อบัตรเลย", "เทียบราคาบัตร"], m.DETAIL_CHOICES)
    assert not m.pack_matches(["ดูอากาศ"], m.DETAIL_CHOICES)


def test_last_user_text():
    m = load()
    assert (
        m.last_user_text(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "ศุกร์นี้เที่ยวกลางคืน"},
            ]
        )
        == "ศุกร์นี้เที่ยวกลางคืน"
    )


if __name__ == "__main__":
    test_detail_pack()
    test_similar_pack_is_home_shortcuts()
    test_compare_zone_pack()
    test_budget_pack_uses_prices()
    test_budget_pack_without_prices()
    test_nightlife_mood_pack()
    test_venue_cards_booking_pack()
    test_compare_store_pack()
    test_compare_store_pack_without_titles()
    test_suggested_layout_stores_from_prose()
    test_suggested_layout_similar_from_list()
    test_pack_matches_half()
    test_last_user_text()
    print("usecase_packs ok")
