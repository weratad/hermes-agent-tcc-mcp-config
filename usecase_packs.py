"""Hardcoded AI Ask chip packs keyed by layout + catalog shape.

Pure helpers — clarify/layout artifacts call these after each turn.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

_MAX_CHOICES = 4

HOME_SIMILAR_CHOICES = [
    "🔥 งานไหนกำลังมาแรง?",
    "🎫 งานราคาใกล้เคียงกัน",
    "🎶 คอนเสิร์ตแนวเพลงเดียวกันเพิ่มเติม",
    "📍 คืนนี้มีที่ไหนน่าไปใกล้ฉัน?",
]

DETAIL_CHOICES = [
    "ซื้อบัตร",
    "เทียบราคา",
    "ซื้อโซนไหนคุ้ม",
    "ส่งให้เพื่อน",
]

MOOD_CHOICES = [
    "สนใจแนว Live Band + Dinner",
    "สนใจแนว Trendy Bar + New Spot",
    "สนใจแนว Music Bar + Party Vibe",
]

STORE_PICK_CHOICES = [
    "จองโต๊ะ",
    "ส่งให้เพื่อน",
]

NIGHTLIFE_OPEN_RE = re.compile(
    r"กลางคืน|ไนท์ไลฟ์|nightlife|บาร์|ร้าน|ดนตรีสด|live\s*band|เที่ยว",
    re.I,
)
COMPARE_STORE_RE = re.compile(r"เปรียบเทียบ|เทียบ.*(ร้าน|เพลง|บรรยากาศ)|compare", re.I)
BUDGET_ZONE_RE = re.compile(r"งบ|โซนไหน|พัน|บาท", re.I)
SEATMAP_RE = re.compile(r"ผัง|ที่นั่ง|seat\s*map|seating", re.I)
SIMILAR_RE = re.compile(
    r"แนวเพลงเดียวกัน|คล้ายกัน|similar|งานราคาใกล้เคียง|กำลังมาแรง|ใกล้ฉัน",
    re.I,
)


def _norm(label: str) -> str:
    return re.sub(r"\s+", "", str(label or "").lower())


def is_store_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    url = str(event.get("url") or "")
    if re.search(r"/(?:store|nightlife)/", url, re.I):
        return True
    kind = str(event.get("kind") or event.get("type") or "").lower()
    return kind in {"store", "venue", "nightlife"}


def store_titles(events: Sequence[Any], limit: int = 3) -> List[str]:
    out: List[str] = []
    for event in events or []:
        if not is_store_event(event):
            continue
        title = str(event.get("title") or "").strip()
        if title and title not in out:
            out.append(title)
        if len(out) >= limit:
            break
    return out


def concert_events(events: Sequence[Any]) -> List[Dict[str, Any]]:
    return [
        e
        for e in (events or [])
        if isinstance(e, dict) and not is_store_event(e)
    ]


def has_seatmap(events: Sequence[Any]) -> bool:
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if event.get("has_seatmap") or event.get("seatmap"):
            return True
        blob = " ".join(
            str(event.get(k) or "")
            for k in ("meta", "description", "venue", "title")
        )
        if SEATMAP_RE.search(blob):
            return True
    return False


def price_labels(events: Sequence[Any], limit: int = 4) -> List[str]:
    """Build 'ซื้อบัตร {price}' chips from event price / tiers when present."""
    out: List[str] = []
    for event in events or []:
        if not isinstance(event, dict) or is_store_event(event):
            continue
        tiers = event.get("ticket_tiers")
        if isinstance(tiers, list):
            for tier in tiers:
                if not isinstance(tier, dict):
                    continue
                price = tier.get("price") or tier.get("price_min") or tier.get("price_max")
                if price is None:
                    continue
                try:
                    label = f"ซื้อบัตร {int(float(price))} บาท"
                except (TypeError, ValueError):
                    label = f"ซื้อบัตร {price} บาท"
                if label not in out:
                    out.append(label)
                if len(out) >= limit:
                    return out
        price = str(event.get("price") or "").strip()
        if price:
            # Split ranges like 299–1500 into endpoints when possible.
            parts = re.split(r"[–\-~ถึง]", price)
            for part in parts:
                digits = re.sub(r"[^\d]", "", part)
                if not digits:
                    continue
                label = f"ซื้อบัตร {int(digits)} บาท"
                if label not in out:
                    out.append(label)
                if len(out) >= limit:
                    return out
    return out[:limit]


def pack_matches(existing: Optional[Sequence[str]], expected: Sequence[str]) -> bool:
    if not expected:
        return True
    if not existing:
        return False
    got = {_norm(x) for x in existing}
    need = {_norm(x) for x in expected}
    # At least half of expected labels present (substring OK for filled templates).
    hits = 0
    for exp in need:
        if any(exp in g or g in exp for g in got):
            hits += 1
    return hits >= max(1, (len(need) + 1) // 2)


def booking_choices(titles: Sequence[str], *, compare_n: int = 0) -> List[str]:
    choices: List[str] = []
    if compare_n > 0:
        choices.append(f"เปรียบเทียบเพลงและบรรยากาศทั้ง {compare_n} ร้าน")
    for title in titles[:3]:
        choices.append(f"จอง {title}")
    return choices[:_MAX_CHOICES]


def select_pack(
    layout: Optional[str],
    events: Optional[Sequence[Any]] = None,
    user_text: str = "",
) -> Optional[Dict[str, Any]]:
    """Return {question, choices} or None when no pack applies."""
    layout = str(layout or "").strip() or None
    events = list(events or [])
    text = str(user_text or "")
    stores = store_titles(events)
    concerts = concert_events(events)

    if layout == "similar_cards":
        return {
            "question": "สนใจต่อยังไงดี?",
            "choices": list(HOME_SIMILAR_CHOICES),
        }

    if layout == "event_detail" or (
        layout in (None, "prose") and len(concerts) == 1 and concerts[0].get("layout") == "poster"
    ):
        return {
            "question": "สนใจต่อยังไงดี?",
            "choices": list(DETAIL_CHOICES),
        }

    if layout in ("compare_zone", "compare_value", "compare_matrix"):
        if BUDGET_ZONE_RE.search(text):
            choices = price_labels(concerts or events)[:3]
            if not choices:
                choices = ["ซื้อบัตร"]
            choices.append("ส่งให้เพื่อนช่วยเลือก")
            return {
                "question": "เลือกต่อยังไงดี?",
                "choices": choices[:_MAX_CHOICES],
            }
        choices = [
            "งบไม่เกินราคาเริ่มต้น",
            "อยากได้โซนคุ้มสุด",
        ]
        if has_seatmap(concerts or events):
            choices.extend(["อยากใกล้เวที", "ดูผังที่นั่ง"])
        else:
            choices.append("ซื้อบัตร")
            choices.append("ส่งให้เพื่อน")
        return {
            "question": "เลือกต่อยังไงดี?",
            "choices": choices[:_MAX_CHOICES],
        }

    if layout == "compare_store":
        titles = stores or [str(e.get("title") or "").strip() for e in events if isinstance(e, dict)]
        titles = [t for t in titles if t][:3]
        choices = booking_choices(titles)[:_MAX_CHOICES]
        if not choices:
            choices = ["จองร้านที่สนใจ", "ส่งให้เพื่อน"]
        return {
            "question": "จองร้านไหนดี?",
            "choices": choices,
        }

    if layout == "venue_cards":
        n = len(stores) or len(events)
        titles = stores or [
            str(e.get("title") or "").strip() for e in events if isinstance(e, dict)
        ]
        titles = [t for t in titles if t][:3]
        # Single-store detail / plan turn
        if n <= 1 or re.search(r"เพื่อน|วางแผน|เลือกร้าน|จองโต๊ะ", text):
            return {
                "question": "สนใจต่อยังไงดี?",
                "choices": list(STORE_PICK_CHOICES),
            }
        return {
            "question": "สนใจต่อยังไงดี?",
            "choices": booking_choices(titles, compare_n=n),
        }

    if layout in (None, "prose") and not events and NIGHTLIFE_OPEN_RE.search(text):
        return {
            "question": "สนใจแนวไหนเป็นพิเศษ?",
            "choices": list(MOOD_CHOICES),
        }

    if layout in (None, "prose") and stores:
        n = len(stores)
        return {
            "question": "สนใจต่อยังไงดี?",
            "choices": booking_choices(stores, compare_n=n),
        }

    if layout == "event_list" and concerts:
        return {
            "question": "สนใจต่อยังไงดี?",
            "choices": [
                "ดูรายละเอียดงาน",
                "เทียบราคาบัตร",
                "ซื้อบัตร",
                "ส่งให้เพื่อน",
            ][:_MAX_CHOICES],
        }

    return None


def suggested_layout(
    layout: Optional[str],
    events: Optional[Sequence[Any]] = None,
    user_text: str = "",
) -> Optional[str]:
    """Return a layout override when catalog shape contradicts prose/missing."""
    layout = str(layout or "").strip() or None
    events = list(events or [])
    text = str(user_text or "")
    stores = [e for e in events if is_store_event(e)]
    concerts = concert_events(events)

    if stores and layout in (None, "prose", "event_list", "event_detail"):
        if COMPARE_STORE_RE.search(text):
            return "compare_store"
        if len(stores) == 1:
            return "venue_cards"
        return "venue_cards"

    if (
        concerts
        and len(concerts) >= 2
        and SIMILAR_RE.search(text)
        and layout in (None, "prose", "event_list")
    ):
        return "similar_cards"

    if concerts and layout == "prose":
        if len(concerts) == 1 and concerts[0].get("layout") == "poster":
            return "event_detail"
        return "event_list"

    if not events and layout in (None, "prose") and NIGHTLIFE_OPEN_RE.search(text):
        return "prose"

    return None


def last_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for row in reversed(messages):
        if not isinstance(row, dict):
            continue
        if row.get("role") != "user":
            continue
        content = row.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            text = "\n".join(parts).strip()
            if text:
                return text
    return ""
