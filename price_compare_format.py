"""Deterministic price-compare marker formatting for AI Ask (Hermes plugin)."""

from __future__ import annotations

import re
from typing import Any

COMPARE_ASK_RE = re.compile(
    r"เทียบราคา|"
    r"โซนไหนคุ้ม|"
    r"(?:งบ|งบประมาณ).{0,48}โซนไหน|"
    r"โซนไหน.{0,48}(?:งบ|งบประมาณ|คุ้ม)|"
    r"ซื้อโซนไหน|"
    r"ควรซื้อโซน|"
    r"compare\s+(?:ticket\s+)?prices?|"
    r"ticket\s+prices?",
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
    value = str(text or "").replace("|", " ").strip()
    value = re.sub(
        r"^(?:ราคา|จุดเด่น|จุดที่ต้องคิด|เหมาะกับ)\s*[:：]\s*",
        "",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


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


def _short_intro(prose: str, *, title: str = "", tier_labels: list[str] | None = None) -> str:
    """Keep a short non-table intro; never keep zone-essay / zone-price / insight lines."""
    labels = [str(x).strip() for x in (tier_labels or []) if str(x).strip()]
    kept: list[str] = []
    for raw in str(prose or "").splitlines():
        line = raw.strip()
        if not line or _is_structured_noise(line):
            continue
        if "จุดเด่น" in line or "จุดที่ต้องคิด" in line:
            continue
        if re.match(r"^(?:AI\s*Insight|💡|insight)\s*[:：]", line, flags=re.IGNORECASE):
            continue
        if labels and any(
            re.match(rf"^{re.escape(lab)}\b", line, flags=re.IGNORECASE) for lab in labels
        ):
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
        labels = re.findall(r"(?:^|\n)\s*([A-Za-z0-9ก-๙]{1,24})\s*[:：]", cleaned)
        return _short_intro(cleaned, title=title, tier_labels=labels[:8])
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


_CANNED_CELL_RE = re.compile(
    r"โซน\s+\S+\s*·\s*(?:ราคาเริ่มต้น|สมดุลราคากับประสบการณ์|ระดับบนสุด)"
    r"|ตัวเลือกถูกสุดในงานนี้|ตัวเลือกกลาง|ตัวเลือกบนสุดในงานนี้"
    r"|สิทธิ์/มุมมองมักน้อยกว่าโซนบน|สิทธิ์หรือมุมมักน้อยกว่าโซนบน"
    r"|จ่ายเพิ่มจากโซนถูกสุดประมาณ|จ่ายเพิ่มจากตัวเลือกถูกสุดประมาณ"
    r"|แพงกว่าโซนถูกสุดประมาณ|แพงกว่าตัวเลือกถูกสุดประมาณ",
)


def _is_canned_cell(text: str) -> bool:
    """True for structural zone+price templates — not model-written advice."""
    return bool(_CANNED_CELL_RE.search(str(text or "")))


def _tier_blurbs(index: int, rows: list[dict], tier: dict) -> tuple[str, str]:
    """Last-resort fill only — never 'โซน X · ราคา…' tautology (model should fill)."""
    n = len(rows)
    cheapest = float(rows[0]["price_min"])
    price = float(tier["price_min"])
    delta = max(0, int(round(price - cheapest)))
    if index == 0:
        return ("ตัวเลือกถูกสุดในงานนี้", "สิทธิ์หรือมุมมักน้อยกว่าโซนบน")
    if index == n - 1:
        cons = (
            f"แพงกว่าตัวเลือกถูกสุดประมาณ {_format_baht(delta)}"
            if delta
            else "ราคาสูงสุดในตารางนี้"
        )
        return ("ตัวเลือกบนสุดในงานนี้", cons)
    return (
        "ตัวเลือกกลาง สมดุลราคาและประสบการณ์",
        f"จ่ายเพิ่มจากตัวเลือกถูกสุดประมาณ {_format_baht(delta)}",
    )


def _extract_zone_blurbs(text: str, tiers: list[dict]) -> dict[str, tuple[str, str]]:
    """Pull จุดเด่น/จุดที่ต้องคิด from a model zone-essay into marker cells.

    Supports both `GA:` and screenshot-style `GA — 950 บาท` headers.
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

    label_alt = "|".join(re.escape(l) for l in labels)
    parts = re.split(
        rf"(?=(?:^|\n)\s*(?:{label_alt})\b)",
        body,
        flags=re.IGNORECASE,
    )
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        matched = None
        for label in labels:
            if re.match(
                rf"^{re.escape(label)}\b(?:\s*[—–\-:：]|\s|$)",
                chunk,
                re.IGNORECASE,
            ):
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
            out[matched.casefold()] = (pros, cons)
    return out


def _extract_marker_blurbs(reply: str, tiers: list[dict]) -> dict[str, tuple[str, str]]:
    """Keep non-blank non-canned 🎫 pros/cons; blank/`—`/canned left empty."""
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
        if _is_blank_cell(pros) or _is_canned_cell(pros):
            pros = ""
        if _is_blank_cell(cons) or _is_canned_cell(cons):
            cons = ""
        label = _tier_label(rows[index])
        if not label:
            continue
        if pros or cons:
            out[label.casefold()] = (pros, cons)
    return out


def _extract_dash_zone_blurbs(
    text: str, tiers: list[dict]
) -> dict[str, tuple[str, str]]:
    """Lift model lines like 'GA 550 บาท — …' or 'GA 550 บาท: …' into cells."""
    body = str(text or "")
    out: dict[str, tuple[str, str]] = {}
    rows = usable_tiers(tiers)
    for index, tier in enumerate(rows):
        label = _tier_label(tier)
        if not label:
            continue
        match = re.search(
            rf"(?:^|\n)\s*{re.escape(label)}\b[^\n]{{0,48}}?"
            rf"(?:—|–|-|[:：])\s*(.+?)(?=\n|$)",
            body,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        reason = _cell(match.group(1))
        # Drop leading price residue if separator was weak ("550 บาท: advice").
        reason = re.sub(r"^(?:\d[\d,]*\s*บาท\s*[:：—–-]?\s*)", "", reason).strip()
        if not reason or _is_canned_cell(reason):
            continue
        if len(reason) > 100:
            reason = reason[:100].rstrip() + "…"
        split = re.split(r"\s+(?:แต่|อย่างไรก็ตาม)\s+", reason, maxsplit=1)
        pros = _cell(split[0])
        cons = _cell(split[1]) if len(split) > 1 else ""
        if _is_blank_cell(pros):
            continue
        if _is_blank_cell(cons):
            cheapest = float(rows[0]["price_min"])
            delta = max(0, int(round(float(tier["price_min"]) - cheapest)))
            if index == 0:
                cons = "สิทธิ์หรือมุมมักน้อยกว่าโซนบน"
            elif delta:
                cons = f"จ่ายเพิ่มจากตัวเลือกถูกสุดประมาณ {_format_baht(delta)}"
            else:
                cons = "พิจารณางบก่อนตัดสินใจ"
        out[label.casefold()] = (pros, cons)
    return out


def _extract_insight_blurbs(
    text: str, tiers: list[dict]
) -> dict[str, tuple[str, str]]:
    """Lift ordered 'AI Insight:' / 💡 lines into tiers (model-written, not templates)."""
    rows = usable_tiers(tiers)
    if not rows:
        return {}
    insights: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(
            r"^(?:AI\s*Insight|💡|insight)\s*[:：]\s*(.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            tip = _cell(match.group(1))
            if tip and not _is_canned_cell(tip):
                insights.append(tip[:100].rstrip("…") + ("…" if len(tip) > 100 else ""))
    if len(insights) < 2:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for index, tier in enumerate(rows):
        label = _tier_label(tier)
        if not label or index >= len(insights):
            continue
        pros = insights[index]
        cons = ""
        cheapest = float(rows[0]["price_min"])
        delta = max(0, int(round(float(tier["price_min"]) - cheapest)))
        if index == 0:
            cons = "สิทธิ์หรือมุมมักน้อยกว่าโซนบน"
        elif delta:
            cons = f"จ่ายเพิ่มจากตัวเลือกถูกสุดประมาณ {_format_baht(delta)}"
        else:
            cons = "พิจารณางบก่อนตัดสินใจ"
        out[label.casefold()] = (pros, cons)
    return out


def _merge_cell_blurbs(
    *sources: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    """Prefer non-blank, non-canned cells; later sources fill gaps only."""
    out: dict[str, tuple[str, str]] = {}
    for src in sources:
        for key, (pros, cons) in (src or {}).items():
            prev = out.get(key, ("", ""))
            next_pros = prev[0]
            next_cons = prev[1]
            if (not next_pros or _is_canned_cell(next_pros)) and pros and not _is_canned_cell(
                pros
            ):
                next_pros = pros
            if (not next_cons or _is_canned_cell(next_cons)) and cons and not _is_canned_cell(
                cons
            ):
                next_cons = cons
            if next_pros or next_cons:
                out[key] = (next_pros, next_cons)
    return out


def _looks_like_zone_price_lines(text: str, tiers: list[dict]) -> bool:
    """True when model listed zones with prices in prose (not 🎫 markers)."""
    body = str(text or "")
    hits = 0
    for tier in usable_tiers(tiers):
        label = _tier_label(tier)
        if not label:
            continue
        if re.search(
            rf"(?:^|\n)\s*{re.escape(label)}\b[^\n]{{0,40}}บาท",
            body,
            flags=re.IGNORECASE,
        ):
            hits += 1
    return hits >= 2


def _markers_need_cell_fill(reply: str) -> bool:
    """True when 🎫 rows exist but จุดเด่น/จุดที่ต้องคิด blank/—/canned."""
    for raw in str(reply or "").splitlines():
        line = raw.strip()
        if not line.startswith("🎫"):
            continue
        rest = line.replace("🎫", "", 1).strip()
        cells = [c.strip() for c in rest.split("|")]
        if len(cells) < 3:
            return True
        if (
            _is_blank_cell(cells[1])
            or _is_blank_cell(cells[2])
            or _is_canned_cell(cells[1])
            or _is_canned_cell(cells[2])
        ):
            return True
    return False


def _marker_table(tiers: list[dict], *, blurbs: dict[str, tuple[str, str]] | None = None) -> str:
    """Figma markers from catalog tiers; prefer model blurbs (never leave —)."""
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
            if _is_blank_cell(pros) or _is_canned_cell(pros):
                pros = default_pros
            if _is_blank_cell(cons) or _is_canned_cell(cons):
                cons = default_cons
        else:
            pros, cons = default_pros, default_cons
        badge = "|คุ้มสุด" if index == 0 else ""
        lines.append(f"🎫 {price}|{_cell(pros)}|{_cell(cons)}{badge}")
    return "\n".join(lines)


def _pick_tier_from_reply(reply: str, rows: list[dict]) -> dict | None:
    """Prefer the zone the model cheered/recommended."""
    text = str(reply or "")
    for tier in rows:
        label = _tier_label(tier)
        if not label:
            continue
        if re.search(
            rf"(?:เชียร์|แนะนำ|คุ้มสุด(?:คือ)?|recommend(?:s|ed)?)\s*{re.escape(label)}\b",
            text,
            re.IGNORECASE,
        ):
            return tier
    # Mid tier matches Figma “คุ้มสุด” balance default when model is silent.
    if len(rows) >= 3:
        return rows[len(rows) // 2]
    return rows[0] if rows else None


def _extract_existing_pick(reply: str) -> str:
    lines = []
    capture = False
    for raw in str(reply or "").splitlines():
        stripped = raw.strip()
        if stripped.startswith("🏁"):
            capture = True
            lines.append(stripped)
            continue
        if capture:
            if stripped.startswith(("⚖️", "🎫", "💬")):
                break
            if not stripped and lines:
                # blank ends pick unless next is a bullet
                continue
            if stripped.startswith(("•", "-", "–")) or stripped:
                lines.append(stripped)
    return "\n".join(lines).strip()


def _recommendation_block(
    reply: str,
    rows: list[dict],
    blurbs: dict[str, tuple[str, str]],
) -> str:
    """Figma section under the table: ผมแนะนำ ฿… / เหตุผลคือ / bullets."""
    existing = _extract_existing_pick(reply)
    if existing:
        return existing

    pick = _pick_tier_from_reply(reply, rows)
    if not pick:
        return ""
    label = _tier_label(pick) or "โซนนี้"
    price = _format_tier_price(pick)
    pros, cons = blurbs.get(label.casefold(), ("", ""))
    if _is_blank_cell(pros) or _is_canned_cell(pros):
        pros = ""
    if _is_blank_cell(cons) or _is_canned_cell(cons):
        cons = ""

    # Lift free-form cheer sentence if present.
    cheer = ""
    cheer_m = re.search(
        rf"([^\n]*(?:เชียร์|แนะนำ)\s*{re.escape(label)}[^\n]*)",
        str(reply or ""),
        re.IGNORECASE,
    )
    if cheer_m:
        cheer = _cell(cheer_m.group(1))

    lines = [
        f"🏁 ผมแนะนำ {price} เป็นตัวเลือกคุ้มสุดครับ"
        if re.search(rf"คุ้ม|เชียร์\s*{re.escape(label)}", str(reply or ""), re.I)
        or not cheer
        else f"🏁 {cheer}",
        "เหตุผลคือ",
    ]
    bullets: list[str] = []
    if pros:
        bullets.append(f"• {pros}")
    if cons:
        bullets.append(f"• {cons}")
    # Extra bullets already in model prose
    for raw in str(reply or "").splitlines():
        s = raw.strip()
        if s.startswith(("•", "-")) and len(s) > 2:
            tip = _cell(s.lstrip("•-– ").strip())
            if tip and tip not in pros and tip not in cons:
                bullets.append(f"• {tip}")
        if len(bullets) >= 4:
            break
    if not bullets:
        bullets.append(f"• โซน {label} สมดุลราคากับประสบการณ์ของงานนี้")
    lines.extend(bullets[:4])
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
    allow_structural_fallback: bool = True,
) -> str:
    """Hermes finalize: keep model voice + ensure filled ⚖️/🎫 markers for web.

    - Model markers with real จุดเด่น/จุดที่ต้องคิด → keep (only when ≥2 tiers)
    - Zone-essay / dash prose (GA 550 บาท — …) → lift into cells
    - Missing markers: lift model blurbs into table; structural fallback only when
      ``allow_structural_fallback`` (after a model retry) — never invent zone+price copy first
    - <2 usable tiers → honest no-table (never keep invented ⚖️/🎫)
    """
    reply = str(model_reply or "")
    rows = usable_tiers(tiers)
    if len(rows) < 2:
        return _honest_no_table_reply(reply, title=title)
    if (
        has_price_compare_markers(reply)
        and not _is_zone_essay(reply)
        and not _looks_like_zone_price_lines(reply, rows)
        and not _markers_need_cell_fill(reply)
    ):
        if "🏁" not in reply:
            blurbs_keep = _merge_cell_blurbs(
                _extract_marker_blurbs(reply, rows),
                _extract_dash_zone_blurbs(reply, rows),
                _extract_insight_blurbs(reply, rows),
                _extract_zone_blurbs(reply, rows),
            )
            pick = _recommendation_block(reply, rows, blurbs_keep)
            if pick:
                return f"{reply.rstrip()}\n{pick}"
        return reply

    blurbs = _merge_cell_blurbs(
        _extract_marker_blurbs(reply, rows),
        _extract_dash_zone_blurbs(reply, rows),
        _extract_insight_blurbs(reply, rows),
        _extract_zone_blurbs(reply, rows),
    )
    model_pros = sum(
        1
        for _, (pros, _) in blurbs.items()
        if pros and not _is_blank_cell(pros) and not _is_canned_cell(pros)
    )
    if model_pros < 2 and not allow_structural_fallback:
        # Let layout retry ask the model for real 🎫 cells — do not stuff templates yet.
        cleaned = _model_prose(reply)
        return cleaned or _short_intro("", title=title)

    table = _marker_table(rows, blurbs=blurbs)
    labels = [_tier_label(t) for t in rows if _tier_label(t)]
    if _is_zone_essay(reply) or _looks_like_zone_price_lines(reply, rows) or len(blurbs) >= 2:
        voice = _short_intro(_model_prose(reply), title=title, tier_labels=labels)
    else:
        voice = _keep_model_voice(reply, title=title)
    pick = _recommendation_block(reply, rows, blurbs)
    if pick:
        return f"{voice}\n{table}\n{pick}"
    return f"{voice}\n{table}"

def format_price_compare_markers(
    tiers: list[dict],
    *,
    title: str = "",
    venue: str = "",
) -> str:
    return compose_price_compare_reply("", tiers, title=title, venue=venue)
