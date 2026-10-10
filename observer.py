from __future__ import annotations

import json
import re
from typing import Any

from playwright.sync_api import Page


def _safe_attr(el, name: str) -> str:
    try:
        return (el.get_attribute(name) or "").strip()
    except Exception:
        return ""


def _safe_text(el, limit: int = 240) -> str:
    try:
        return re.sub(r"\s+", " ", el.inner_text() or "").strip()[:limit]
    except Exception:
        return ""


def _selector_for(el) -> str:
    try:
        return el.evaluate(
            """e => {
                if (e.id) return '#' + CSS.escape(e.id);
                let p = e;
                const parts = [];
                for (let i=0; i<5 && p && p.nodeType===1; i++, p=p.parentElement) {
                    let s = p.tagName.toLowerCase();
                    if (p.classList && p.classList.length) {
                        s += '.' + [...p.classList].slice(0,2).map(CSS.escape).join('.');
                    }
                    if (p.parentElement) {
                        const sib = [...p.parentElement.children].filter(x => x.tagName === p.tagName);
                        if (sib.length > 1) s += ':nth-of-type(' + (sib.indexOf(p)+1) + ')';
                    }
                    parts.unshift(s);
                }
                return parts.join(' > ');
            }"""
        )
    except Exception:
        return ""


def _unique_selectors(page: Page, selectors: list[str]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for sel in selectors:
        if not sel or sel in seen:
            continue
        seen.add(sel)
        try:
            count = page.locator(sel).count()
            out.append({"selector": sel, "count": count})
        except Exception as exc:
            out.append({"selector": sel, "count": 0, "error": str(exc)[:160]})
    return out


def observe(page: Page, max_nodes: int = 180) -> dict[str, Any]:
    """Collect compact, measurable evidence. No LLM reasoning happens here."""
    metrics = page.evaluate(
        """() => ({
            url: location.href,
            title: document.title,
            viewport: {width: innerWidth, height: innerHeight},
            scroll: {
                y: scrollY,
                height: document.documentElement.scrollHeight,
                viewportHeight: innerHeight,
                available: document.documentElement.scrollHeight > innerHeight + 10
            }
        })"""
    )

    selectors = [
        "[data-review-id]",
        "[data-reviewid]",
        "[data-review]",
        ".review",
        "li.review",
        "article.review",
        "[class*='review']",
        "[id*='review']",
        "[class*='Review']",
        "[id*='Review']",
    ]

    review_candidates = []
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = min(loc.count(), 30)
            if count:
                samples = []
                for i in range(min(count, 5)):
                    el = loc.nth(i)
                    samples.append({
                        "text": _safe_text(el),
                        "id": _safe_attr(el, "id"),
                        "data_review_id": _safe_attr(el, "data-review-id"),
                        "class": _safe_attr(el, "class"),
                        "selector": _selector_for(el),
                    })
                review_candidates.append({
                    "selector": sel,
                    "count": count,
                    "samples": samples,
                })
        except Exception:
            pass

    action_candidates = []
    try:
        buttons = page.locator("button, a, [role='button']")
        for i in range(min(buttons.count(), max_nodes)):
            el = buttons.nth(i)
            text = _safe_text(el, 120)
            aria = _safe_attr(el, "aria-label")
            disabled = _safe_attr(el, "disabled")
            if not (text or aria):
                continue
            blob = f"{text} {aria}".lower()
            if any(k in blob for k in (
                "load more", "more reviews", "next", "previous",
                "show more", "see more", "reviews"
            )):
                action_candidates.append({
                    "text": text,
                    "aria": aria,
                    "disabled": bool(disabled),
                    "selector": _selector_for(el),
                })
    except Exception:
        pass

    scroll_candidates = []
    try:
        scroll_candidates = page.evaluate(
            """() => [...document.querySelectorAll('*')].map((e, i) => {
                const s = getComputedStyle(e);
                return {
                    i,
                    tag: e.tagName.toLowerCase(),
                    id: e.id || '',
                    class: typeof e.className === 'string' ? e.className.slice(0,160) : '',
                    scrollHeight: e.scrollHeight,
                    clientHeight: e.clientHeight,
                    overflowY: s.overflowY
                };
            }).filter(x =>
                x.scrollHeight > x.clientHeight + 20 &&
                ['auto','scroll'].includes(x.overflowY)
            ).slice(0, 15)"""
        )
    except Exception:
        pass

    repeated_groups = []
    try:
        repeated_groups = page.evaluate(
            """() => {
                const map = new Map();
                for (const e of [...document.querySelectorAll('body *')].slice(0, 5000)) {
                    const key = e.tagName.toLowerCase() + '|' +
                      (typeof e.className === 'string' ? e.className.split(/\\s+/).slice(0,3).sort().join('.') : '');
                    if (!map.has(key)) map.set(key, []);
                    const a = map.get(key);
                    if (a.length < 4) a.push({
                        tag: e.tagName.toLowerCase(),
                        id: e.id || '',
                        class: typeof e.className === 'string' ? e.className.slice(0,160) : '',
                        text: (e.innerText || '').replace(/\\s+/g,' ').trim().slice(0,180)
                    });
                }
                return [...map.entries()]
                  .filter(([k,v]) => v.length >= 3)
                  .sort((a,b) => b[1].length-a[1].length)
                  .slice(0,20)
                  .map(([key,samples]) => ({key,samples}));
            }"""
        )
    except Exception:
        pass

    return {
        "page": metrics,
        "review_candidates": review_candidates,
        "action_candidates": action_candidates,
        "scroll_candidates": scroll_candidates,
        "repeated_groups": repeated_groups,
    }


def save_observation(observation: dict[str, Any], path: str = "dom_candidates.json") -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(observation, f, indent=2, ensure_ascii=False)
