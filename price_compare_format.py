"""Deterministic price-compare marker formatting for AI Ask (Hermes plugin)."""

from __future__ import annotations

import re
from typing import Any

COMPARE_ASK_RE = re.compile(
    r"เทียบราคา|โซนไหนคุ้ม|compare\s+(?:ticket\s+)?prices?|ticket\s+prices?",
    re.IGNORECASE,
)
NIGHTLIFE_RE = re.compile(
    r"ร้าน|ผับ|บาร์|คาเฟ่|จองโต๊ะ|nightlife|"
    r"(?:compare|เทียบ)\s+(?:the\s+)?stores?\b|stores?\s+compare",
    re.IGNORECASE,
)
GENERIC_TITLE_TOKENS = {
    "concert",
    "event",
    "festival",
    "live",
    "music",
    "show",
}


def is_price_compare_ask(text: str) -> bool:
    value = str(text or "")
    return not NIGHTLIFE_RE.search(value) and bool(COMPARE_ASK_RE.search(value))


def _event_product_id(event: dict) -> int:
    for raw in (event.get("product_id"), event.get("id")):
        try:
            value = int(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    match = re.search(r"/concert/(\d+)", str(event.get("url") or ""))
    return int(match.group(1)) if match else 0


def matching_event(events: list[dict], user_text: str) -> dict | None:
    """Return an event explicitly identified by product id, URL, or title."""
    text = str(user_text or "")
    for event in events:
        product_id = _event_product_id(event)
        if product_id and re.search(rf"(?<!\d){product_id}(?!\d)", text):
            return event

    normalized = text.casefold()
    scored = []
    for event in events:
        tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9ก-๙]+", str(event.get("title") or ""))
            if len(token) >= 4
            and not token.isdigit()
            and token.casefold() not in GENERIC_TITLE_TOKENS
        }
        score = sum(len(token) for token in tokens if token in normalized)
        if score:
            scored.append((score, event))
    if not scored:
        return None
    best = max(score for score, _ in scored)
    matches = [event for score, event in scored if score == best]
    return matches[0] if len(matches) == 1 else None


def select_price_compare_event(events: list[dict], user_text: str) -> dict | None:
    """Return an explicitly named event that has ≥2 usable tiers.

    Bare asks (no product id / title / URL match) never auto-pick a show —
    that invents a fake compare table (eval P5).
    """
    candidates = [event for event in events if isinstance(event, dict)]
    matched = matching_event(candidates, user_text)
    if matched is None:
        return None
    if len(usable_tiers(matched.get("ticket_tiers") or [])) >= 2:
        return matched
    return None


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


def _is_structured_noise(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if text.startswith(("⚖️", "🎫", "🏁", "💬")):
        return True
    if re.match(r"^ราคา\s*\|", text, re.IGNORECASE):
        return True
    if text.startswith("|") or re.match(r"^:?-{3,}\s*\|", text):
        return True
    if text.count("|") >= 2:
        return True
    # Model often reinvents the Figma table as prose labels — drop those lines.
    if re.match(r"^(?:จุดเด่น|จุดที่ต้องคิด|เหมาะกับ)\s*[:：]", text):
        return True
    if re.match(
        r"^(?:GA|VIP|VVIP|Standing|Seat|โซน\b)[^\n]{0,40}[:：].*(?:บาท|฿|\d)",
        text,
        re.IGNORECASE,
    ):
        return True
    return False


def _is_zone_essay(text: str) -> bool:
    """True when the model pasted a fake zone table in prose."""
    body = str(text or "")
    pros = len(re.findall(r"จุดเด่น\s*[:：]", body))
    cons = len(re.findall(r"จุดที่ต้องคิด\s*[:：]", body))
    return pros >= 2 or (pros >= 1 and cons >= 1)


def _short_intro(prose: str, *, title: str = "") -> str:
    """Keep a short non-table intro; never keep zone-essay lines."""
    kept: list[str] = []
    for raw in str(prose or "").splitlines():
        line = raw.strip()
        if not line or _is_structured_noise(line):
            continue
        if "จุดเด่น" in line or "จุดที่ต้องคิด" in line:
            continue
        kept.append(line)
        if sum(len(x) for x in kept) >= 140:
            break
    intro = "\n".join(kept[:2]).strip()
    if intro:
        return intro
    title_s = str(title or "").strip() or "งานนี้"
    return f"เทียบราคาบัตร {title_s}"


def _keep_model_voice(prose: str, *, title: str = "", limit: int = 500) -> str:
    """Hermes principle: model owns the advice; plugin only owns table markers.

    Keep natural intro sentences; drop zone-essay / marker noise.
    """
    cleaned = _model_prose(prose)
    if not cleaned:
        title_s = str(title or "").strip() or "งานนี้"
        return f"เทียบราคาบัตร {title_s}"
    # Prefer fuller voice when it is not a fake table.
    if _is_zone_essay(cleaned):
        return _short_intro(cleaned, title=title)
    if len(cleaned) > limit:
        # Trim on sentence boundary when possible.
        cut = cleaned[:limit].rstrip()
        for sep in ("।", "。", ".", "!", "?", "\n"):
            idx = cut.rfind(sep)
            if idx >= 80:
                cut = cut[: idx + 1].rstrip()
                break
        return cut
    return cleaned


def _model_prose(reply: str) -> str:
    """Keep the model's own wording; drop pipe-tables / marker / fake-table lines."""
    kept: list[str] = []
    blank_run = 0
    for raw_line in str(reply or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            if kept and blank_run <= 1:
                kept.append("")
            continue
        blank_run = 0
        if _is_structured_noise(stripped):
            continue
        kept.append(stripped)
    prose = "\n".join(kept).strip()
    if len(prose) > 1200:
        prose = prose[:1200].rstrip()
    return prose


def _is_blank_cell(text: str) -> bool:
    return str(text or "").strip() in ("", "—", "-", "–", "−", "–")


def _tier_blurbs(index: int, rows: list[dict], tier: dict) -> tuple[str, str]:
    """Short จุดเด่น / จุดที่ต้องคิด from tier rank + price gap (not empty —)."""
    label = _tier_label(tier) or "โซนนี้"
    n = len(rows)
    cheapest = float(rows[0]["price_min"])
    price = float(tier["price_min"])
    delta = max(0, int(round(price - cheapest)))
    if index == 0:
        return (
            f"โซน {label} · ราคาเริ่มต้น",
            "สิทธิ์/มุมมองมักน้อยกว่าโซนบน",
        )
    if index == n - 1:
        cons = (
            f"แพงกว่าโซนถูกสุดประมาณ {_format_baht(delta)}"
            if delta
            else "ราคาสูงสุดในตารางนี้"
        )
        return (f"โซน {label} · ระดับบนสุด", cons)
    return (
        f"โซน {label} · สมดุลราคากับประสบการณ์",
        f"จ่ายเพิ่มจากโซนถูกสุดประมาณ {_format_baht(delta)}",
    )


def _extract_zone_blurbs(text: str, tiers: list[dict]) -> dict[str, tuple[str, str]]:
    """Pull จุดเด่น/จุดที่ต้องคิด from a model zone-essay into marker cells.

    Hermes finalize: model voice fills cells; plugin owns ⚖️/🎫 shape.
    """
    body = str(text or "")
    out: dict[str, tuple[str, str]] = {}
    labels = []
    for tier in usable_tiers(tiers):
        label = _tier_label(tier)
        if label:
            labels.append(label)
    if not labels:
        return out

    parts = re.split(
        r"(?=(?:^|\n)\s*(?:"
        + "|".join(re.escape(l) for l in labels)
        + r")\s*[:：])",
        body,
        flags=re.IGNORECASE,
    )
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        matched = None
        for label in labels:
            if re.match(rf"^{re.escape(label)}\s*[:：]", chunk, re.IGNORECASE):
                matched = label
                break
        if not matched:
            continue
        pros_m = re.search(r"จุดเด่น\s*[:：]\s*(.+)", chunk)
        cons_m = re.search(r"จุดที่ต้องคิด\s*[:：]\s*(.+)", chunk)
        pros = _cell(pros_m.group(1)) if pros_m else ""
        cons = _cell(cons_m.group(1)) if cons_m else ""
        if len(pros) > 80:
            pros = pros[:80].rstrip() + "…"
        if len(cons) > 80:
            cons = cons[:80].rstrip() + "…"
        if pros or cons:
            # Leave blank cons for _marker_table defaults to fill.
            out[matched.casefold()] = (pros, cons)
    return out


def _extract_marker_blurbs(reply: str, tiers: list[dict]) -> dict[str, tuple[str, str]]:
    """Keep non-blank 🎫 pros/cons; blank/`—` left empty for `_tier_blurbs` defaults."""
    rows = usable_tiers(tiers)
    out: dict[str, tuple[str, str]] = {}
    ticket_lines = [
        ln.strip() for ln in str(reply or "").splitlines() if ln.strip().startswith("🎫")
    ]
    for index, line in enumerate(ticket_lines):
        if index >= len(rows):
            break
        rest = line.replace("🎫", "", 1).strip()
        cells = [c.strip() for c in rest.split("|")]
        pros = cells[1] if len(cells) > 1 else ""
        cons = cells[2] if len(cells) > 2 else ""
        if _is_blank_cell(pros):
            pros = ""
        if _is_blank_cell(cons):
            cons = ""
        label = _tier_label(rows[index])
        if not label:
            continue
        if pros or cons:
            out[label.casefold()] = (pros, cons)
    return out


def _markers_need_cell_fill(reply: str) -> bool:
    """True when 🎫 rows exist but จุดที่ต้องคิด (or จุดเด่น) is blank/—."""
    for raw in str(reply or "").splitlines():
        line = raw.strip()
        if not line.startswith("🎫"):
            continue
        rest = line.replace("🎫", "", 1).strip()
        cells = [c.strip() for c in rest.split("|")]
        if len(cells) < 3:
            return True
        if _is_blank_cell(cells[1]) or _is_blank_cell(cells[2]):
            return True
    return False


def _marker_table(tiers: list[dict], *, blurbs: dict[str, tuple[str, str]] | None = None) -> str:
    """Figma markers from catalog tiers; fill จุดเด่น/จุดที่ต้องคิด (never leave —)."""
    rows = usable_tiers(tiers)
    if len(rows) < 2:
        return "ตอนนี้ยังไม่มีราคาแยกตามโซนพอให้เทียบครับ"
    blurbs = blurbs or {}

    lines = [f"⚖️ เทียบ {len(rows)} ตัวเลือกที่น่าสนใจ"]
    for index, tier in enumerate(rows):
        price = _format_tier_price(tier)
        label = _tier_label(tier)
        key = (label or "").casefold()
        default_pros, default_cons = _tier_blurbs(index, rows, tier)
        if key in blurbs:
            pros, cons = blurbs[key]
            if _is_blank_cell(pros):
                pros = default_pros
            if _is_blank_cell(cons):
                cons = default_cons
        else:
            pros, cons = default_pros, default_cons
        badge = "|คุ้มสุด" if index == 0 else ""
        lines.append(f"🎫 {price}|{_cell(pros)}|{_cell(cons)}{badge}")
    return "\n".join(lines)


def has_price_compare_marker_noise(reply: str) -> bool:
    """True when reply contains any ⚖️ / 🎫 lines (valid or malformed)."""
    text = str(reply or "")
    if "⚖️" in text:
        return True
    return any(ln.strip().startswith("🎫") for ln in text.splitlines())


def _honest_no_table_reply(model_reply: str, *, title: str = "") -> str:
    """Strip fake table markers; keep clarify voice or honest thin-tier copy."""
    prose = _model_prose(model_reply)
    honest = "ตอนนี้ยังไม่มีราคาแยกตามโซนพอให้เทียบครับ"
    if not prose:
        return honest
    if honest in prose:
        return prose
    # Bare ask already clarifying which show — keep that voice, no table.
    if re.search(
        r"เลือกงาน|งาน(?:ไหน|ใด)|which\s+(?:show|event|concert)",
        prose,
        re.IGNORECASE,
    ):
        return prose
    return f"{prose}\n{honest}"


def needs_price_compare_rewrite(reply: str) -> bool:
    """True when wire-inject should finalize markers (Hermes owns table UI)."""
    text = str(reply or "")
    if _is_zone_essay(text):
        return True
    if not has_price_compare_markers(text):
        return True
    if _markers_need_cell_fill(text):
        return True
    return False


def compose_price_compare_reply(
    model_reply: str,
    tiers: list[dict],
    *,
    title: str = "",
    venue: str = "",
) -> str:
    """Hermes finalize: keep model voice + ensure filled ⚖️/🎫 markers for web.

    - Model markers with real จุดเด่น/จุดที่ต้องคิด → keep (only when ≥2 tiers)
    - Zone-essay → lift blurbs into cells
    - Missing markers or blank/— cells → rebuild table from tiers (+ defaults)
    - <2 usable tiers → honest no-table (never keep invented ⚖️/🎫)
    """
    reply = str(model_reply or "")
    rows = usable_tiers(tiers)
    if len(rows) < 2:
        return _honest_no_table_reply(reply, title=title)
    if (
        has_price_compare_markers(reply)
        and not _is_zone_essay(reply)
        and not _markers_need_cell_fill(reply)
    ):
        return reply

    blurbs = (
        _extract_zone_blurbs(reply, rows)
        if _is_zone_essay(reply)
        else _extract_marker_blurbs(reply, rows)
    )
    table = _marker_table(rows, blurbs=blurbs)
    voice = _keep_model_voice(reply, title=title)
    return f"{voice}\n{table}"

def format_price_compare_markers(
    tiers: list[dict],
    *,
    title: str = "",
    venue: str = "",
) -> str:
    return compose_price_compare_reply("", tiers, title=title, venue=venue)
