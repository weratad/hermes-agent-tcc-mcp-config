"""Deterministic price-compare marker formatting for AI Ask (Hermes plugin)."""

from __future__ import annotations

import re
from typing import Any

COMPARE_ASK_RE = re.compile(
    r"เทียบราคา|โซนไหนคุ้ม|compare\s+(?:ticket\s+)?prices?|ticket\s+prices?",
    re.IGNORECASE,
)


def is_price_compare_ask(text: str) -> bool:
    return bool(COMPARE_ASK_RE.search(str(text or "")))


def _finite_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not (price == price and abs(price) != float("inf")):  # NaN / inf
        return None
    return price


def usable_tiers(ticket_tiers: list) -> list[dict]:
    """Tiers with a finite price_min or price, sorted ascending by price."""
    out: list[dict] = []
    for tier in ticket_tiers or []:
        if not isinstance(tier, dict):
            continue
        price_min = _finite_price(tier.get("price_min"))
        if price_min is None:
            price_min = _finite_price(tier.get("price"))
        if price_min is None:
            continue
        price_max = _finite_price(tier.get("price_max"))
        if price_max is None:
            price_max = price_min
        zone = str(tier.get("zone") or "").strip()
        name = str(tier.get("name") or "").strip()
        out.append(
            {
                "zone": zone,
                "name": name,
                "price": price_min,
                "price_min": price_min,
                "price_max": price_max,
            }
        )
    out.sort(key=lambda t: t["price"])
    return out


def _format_baht(price: float) -> str:
    return "฿" + f"{round(price):,}"


def _format_tier_price(tier: dict) -> str:
    min_p = round(tier["price_min"])
    max_p = round(tier["price_max"])
    if max_p != min_p:
        return _format_baht(min_p) + "–" + _format_baht(max_p)
    return _format_baht(min_p)


def _tier_label(tier: dict) -> str:
    return tier.get("zone") or tier.get("name") or "โซนนี้"


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("|", " ")).strip()


def has_price_compare_markers(reply: str) -> bool:
    text = str(reply or "")
    if "⚖️" not in text:
        return False
    ticket_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("🎫")]
    if len(ticket_lines) < 2:
        return False
    for line in ticket_lines:
        rest = line.replace("🎫", "", 1).strip()
        cells = [c.strip() for c in rest.split("|")]
        if len(cells) < 3:
            return False
        if not cells[0] or not cells[1] or not cells[2]:
            return False
    return True


def format_price_compare_markers(
    tiers: list[dict],
    *,
    title: str = "",
    venue: str = "",
) -> str:
    rows = usable_tiers(tiers)
    if len(rows) < 2:
        return "ตอนนี้ยังไม่มีราคาแยกตามโซนพอให้เทียบครับ"

    event_title = str(title or "").strip() or "งานนี้"
    place = str(venue or "").strip()
    who = event_title
    best_index = len(rows) // 2

    if place:
        intro = f"ได้ครับ เทียบราคาบัตรของ {event_title} ที่ {place} ให้ดูหลายโซน"
    else:
        intro = f"ได้ครับ เทียบราคาบัตรของ {event_title} ให้ดูหลายโซน"

    lines = [
        intro,
        f"⚖️ เทียบ {len(rows)} ตัวเลือกที่น่าสนใจ",
    ]

    cheapest = rows[0]["price"]
    for index, tier in enumerate(rows):
        price = _format_tier_price(tier)
        label = _tier_label(tier)
        gap = _format_baht(tier["price"] - cheapest)
        where = f"ที่ {place}" if place else f"ของ {event_title}"
        pros = f"โซน {label} {where}"
        cons = f"แพงกว่าโซนถูกสุด {gap}"
        badge = ""
        if index == 0:
            pros = f"โซน {label} {where} ราคาถูกสุด สำหรับดู {who}"
            cons = f"ไม่ได้ใกล้ {who} กว่าโซนที่แพงกว่า"
            badge = "|คุ้มสุด"
        elif index == best_index:
            pros = f"โซน {label} {where} สมดุลราคากับมุมมอง"
            cons = f"แพงกว่า {_tier_label(rows[0])} อยู่ {gap}"
        elif index == len(rows) - 1:
            pros = f"โซน {label} {where} ราคาสูงสุดในรอบนี้"
            cons = f"แพงกว่าโซนถูกสุด {gap} ถ้าอยากอยู่ใกล้ {who}"

        lines.append(f"🎫 {price}|{_cell(pros)}|{_cell(cons)}{badge}")

    pick_tier = rows[best_index]
    pick = _format_tier_price(pick_tier)
    pick_where = f" ที่ {place}" if place else ""
    lines.append(
        f"🏁 AI Pick: แนะนำ {pick} โซน {_tier_label(pick_tier)} สำหรับไปดู {who}{pick_where}"
    )
    lines.append("เหตุผลคือ")
    lines.append(f"• สมดุลระหว่างราคาและประสบการณ์ของ {who}")
    lines.append(f"• อิงราคาจริงของ {event_title}")
    lines.append("• ยังมีทางเลือกถูกหรือแพงกว่าให้ขยับได้")
    lines.append("")
    if place:
        lines.append(f"ถ้าอยากใกล้เวทีกว่านี้ที่ {place} ค่อยขยับโซนบนได้ครับ")
    else:
        lines.append(f"ถ้าอยากใกล้เวทีกว่านี้ของ {event_title} ค่อยขยับโซนบนได้ครับ")
    lines.append(f"💬 เลือก {pick}")
    lines.append("💬 ดูโซนถูกสุด")
    lines.append("💬 ซื้อบัตร")
    return "\n".join(lines)
