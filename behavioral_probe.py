from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Page


SHOW_MORE_TEXTS = (
    "show more reviews",
    "show more review",
    "load more reviews",
    "load more review",
    "load more",
    "show more",
)


def _count(page: Page, selector: str | None) -> int:
    if not selector:
        return 0
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def _identity_sample(page: Page, selector: str, limit: int = 12) -> dict[str, Any]:
    loc = page.locator(selector)
    n = min(loc.count(), limit)
    ids = []
    texts = []
    for i in range(n):
        el = loc.nth(i)
        try:
            ids.append(
                el.get_attribute("data-review-id")
                or el.get_attribute("id")
                or el.get_attribute("data-id")
                or ""
            )
            texts.append(" ".join((el.inner_text() or "").split())[:180])
        except Exception:
            ids.append("")
            texts.append("")
    return {
        "sample_count": n,
        "ids": ids,
        "unique_ids": len(set(ids)) if ids else 0,
        "stable_id_candidates": bool(ids) and all(ids) and len(set(ids)) == len(ids),
        "text_samples": texts,
    }


def _test_screenshot(page: Page, selector: str) -> dict[str, Any]:
    try:
        el = page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=5000)
        box = el.bounding_box()
        path = "probe_review.png"
        el.screenshot(path=path, timeout=10000)
        return {
            "success": True,
            "method": "element_screenshot",
            "path": path,
            "width": round(box["width"], 1) if box else 0,
            "height": round(box["height"], 1) if box else 0,
        }
    except Exception as exc:
        return {
            "success": False,
            "method": "element_screenshot",
            "error": str(exc)[:240],
        }


def _normalize_text(value: str) -> str:
    return " ".join((value or "").split()).strip().lower()


def _is_show_more_reviews_text(value: str) -> bool:
    text = _normalize_text(value)

    # Amazon commonly renders controls such as:
    #   "Show 10 more reviews"
    #   "Show 10 more review"
    # The numeric count must therefore be allowed between "show" and "more".
    patterns = (
        r"^show\s+\d+\s+more\s+reviews?$",
        r"^load\s+\d+\s+more\s+reviews?$",
        r"^show\s+more\s+reviews?$",
        r"^load\s+more\s+reviews?$",
        r"^show\s+more$",
        r"^load\s+more$",
    )

    return any(re.match(pattern, text) for pattern in patterns)


def _visible_load_more_candidates(page: Page) -> list[dict[str, Any]]:
    """
    Discover visible review-expansion controls deterministically.

    Amazon can expose hidden Rufus/load-history controls in the DOM while the
    actual review pagination control is a visible "Show more reviews" button.
    Do not trust an LLM selector alone here; inspect visible buttons/links and
    test the candidates by their rendered text.
    """
    candidates: list[dict[str, Any]] = []

    try:
        loc = page.locator("button, a, input[type='button'], input[type='submit']")
        total = min(loc.count(), 300)

        for i in range(total):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue

                text = _normalize_text(
                    el.inner_text() or el.get_attribute("value") or el.get_attribute("aria-label") or ""
                )
                aria = _normalize_text(el.get_attribute("aria-label") or "")
                title = _normalize_text(el.get_attribute("title") or "")
                combined = " ".join(x for x in (text, aria, title) if x)

                if not _is_show_more_reviews_text(combined):
                    continue

                if not el.is_enabled():
                    continue

                # Prefer a stable id when one exists. Otherwise use a Playwright
                # text locator scoped to the discovered element's tag/text.
                element_id = el.get_attribute("id")
                if element_id:
                    selector = f"#{element_id}"
                else:
                    tag = el.evaluate("e => e.tagName.toLowerCase()")
                    exact_text = text or aria or title
                    if exact_text:
                        escaped_text = exact_text.replace('"', '\\"')
                        selector = f'{tag}:has-text("{escaped_text}")'
                    else:
                        selector = None

                candidates.append(
                    {
                        "selector": selector,
                        "text": text,
                        "aria_label": aria,
                        "title": title,
                        "index": i,
                    }
                )
            except Exception:
                continue
    except Exception:
        return []

    return candidates


def _test_load_more(page: Page, nav: dict[str, Any], review_selector: str) -> dict[str, Any]:
    before = _count(page, review_selector)
    requested_selector = nav.get("selector")

    # First try the LLM/observer candidate, but only if it is actually visible.
    candidates: list[tuple[str | None, str]] = []
    if requested_selector:
        candidates.append((requested_selector, "llm_candidate"))

    # Then deterministically discover visible "Show more reviews" / "Load more"
    # controls. This is essential for Amazon, where hidden Rufus controls can
    # coexist with the real visible review pagination button.
    for candidate in _visible_load_more_candidates(page):
        selector = candidate.get("selector")
        if selector and all(selector != existing[0] for existing in candidates):
            candidates.append((selector, "visible_text_candidate"))

    if not candidates:
        return {
            "type": "load_more",
            "selector": requested_selector,
            "confirmed": False,
            "before": before,
            "reason": "no visible show-more/load-more review control found",
        }

    attempts = []

    for selector, source in candidates:
        if not selector:
            continue

        try:
            button = page.locator(selector).first

            if not button.is_visible():
                attempts.append({
                    "selector": selector,
                    "source": source,
                    "result": "not visible",
                })
                continue

            if not button.is_enabled():
                attempts.append({
                    "selector": selector,
                    "source": source,
                    "result": "disabled",
                })
                continue

            button.scroll_into_view_if_needed(timeout=5000)
            button.click(timeout=8000)
            page.wait_for_timeout(1400)

            after = _count(page, review_selector)

            # Some sites update asynchronously.
            if after <= before:
                page.wait_for_timeout(1800)
                after = _count(page, review_selector)

            attempts.append({
                "selector": selector,
                "source": source,
                "result": "review count increased" if after > before else "review count did not increase",
                "before": before,
                "after": after,
            })

            if after > before:
                return {
                    "type": "load_more",
                    "selector": selector,
                    "confirmed": True,
                    "before": before,
                    "after": after,
                    "new_elements": after - before,
                    "reason": "visible show-more/load-more control increased review count",
                    "source": source,
                    "attempts": attempts,
                }

        except Exception as exc:
            attempts.append({
                "selector": selector,
                "source": source,
                "result": str(exc)[:240],
            })

    return {
        "type": "load_more",
        "selector": requested_selector,
        "confirmed": False,
        "before": before,
        "reason": "load-more candidates did not increase review count",
        "attempts": attempts,
    }


def _test_internal_scroll(page: Page, nav: dict[str, Any], review_selector: str) -> dict[str, Any]:
    before = _count(page, review_selector)
    sel = nav.get("selector")

    if not sel:
        return {
            "type": "internal_scroll",
            "selector": None,
            "confirmed": False,
            "before": before,
            "reason": "missing container",
        }

    try:
        container = page.locator(sel).first
        state_before = container.evaluate(
            "(e) => ({top:e.scrollTop,height:e.scrollHeight,client:e.clientHeight})"
        )

        container.evaluate(
            "(e) => e.scrollTop = Math.min(e.scrollTop + Math.max(800, e.clientHeight * 0.9), e.scrollHeight)"
        )
        page.wait_for_timeout(1200)

        after = _count(page, review_selector)
        state_after = container.evaluate(
            "(e) => ({top:e.scrollTop,height:e.scrollHeight,client:e.clientHeight})"
        )

        return {
            "type": "internal_scroll",
            "selector": sel,
            "confirmed": after > before,
            "before": before,
            "after": after,
            "scroll_before": state_before,
            "scroll_after": state_after,
            "reason": "review count increased" if after > before else "review count did not increase",
        }

    except Exception as exc:
        return {
            "type": "internal_scroll",
            "selector": sel,
            "confirmed": False,
            "before": before,
            "reason": str(exc)[:240],
        }


def _test_page_scroll(page: Page, review_selector: str) -> dict[str, Any]:
    before = _count(page, review_selector)

    try:
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(1400)

        after = _count(page, review_selector)

        return {
            "type": "page_scroll",
            "selector": None,
            "confirmed": after > before,
            "before": before,
            "after": after,
            "reason": "review count increased" if after > before else "review count did not increase",
        }
    except Exception as exc:
        return {
            "type": "page_scroll",
            "selector": None,
            "confirmed": False,
            "before": before,
            "reason": str(exc)[:240],
        }


def _candidate_navigation(discovery: dict[str, Any], critique: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    seen = set()

    # Critic recommendations first.
    for n in critique.get("navigation_rankings", []):
        if n.get("decision") in ("accept", "probe"):
            item = {"type": n.get("type"), "selector": n.get("selector")}
            key = (item["type"], item["selector"])
            if key not in seen:
                seen.add(key)
                result.append(item)

    # Discovery candidates next.
    for n in discovery.get("navigation_candidates", []):
        item = {"type": n.get("type"), "selector": n.get("selector")}
        key = (item["type"], item["selector"])
        if key not in seen:
            seen.add(key)
            result.append(item)

    # If no load-more candidate survived the LLM pipeline, still probe for a
    # visible review expansion control. The probe itself is deterministic.
    if not any(n.get("type") == "load_more" for n in result):
        result.append({"type": "load_more", "selector": None})

    return result[:10]


def run_probes(page: Page, discovery: dict[str, Any], critique: dict[str, Any]) -> dict[str, Any]:
    candidates = []

    for item in critique.get("review_rankings", []):
        if item.get("decision") in ("accept", "probe"):
            candidates.append(item.get("selector"))

    for item in discovery.get("review_candidates", []):
        candidates.append(item.get("selector"))

    unique = []
    for sel in candidates:
        if isinstance(sel, str) and sel and sel not in unique:
            unique.append(sel)

    selector_tests = []
    viable = []

    for sel in unique[:8]:
        count = _count(page, sel)
        test = {
            "selector": sel,
            "count": count,
            "repeated": count >= 2,
        }
        selector_tests.append(test)

        # A collection selector needs multiple matches.
        if count >= 2:
            viable.append(sel)

    selected = viable[0] if viable else None

    screenshot_test = (
        _test_screenshot(page, selected)
        if selected
        else {
            "success": False,
            "method": "element_screenshot",
            "error": "no repeated review selector",
        }
    )

    # Identity is measured BEFORE navigation.
    identity = _identity_sample(page, selected) if selected else {}

    # Test EVERY plausible navigation candidate until one actually increases
    # the review set. We do not stop merely because a candidate exists.
    navigation_tests = []
    confirmed_navigation = None

    for nav in _candidate_navigation(discovery, critique):
        if not selected:
            break

        result = None

        if nav["type"] == "load_more":
            result = _test_load_more(page, nav, selected)

        elif nav["type"] == "internal_scroll":
            result = _test_internal_scroll(page, nav, selected)

        elif nav["type"] == "page_scroll":
            result = _test_page_scroll(page, selected)

        elif nav["type"] in ("pagination", "unknown"):
            result = {
                "type": nav["type"],
                "selector": nav.get("selector"),
                "confirmed": False,
                "reason": "not safe to probe automatically",
            }

        if result:
            navigation_tests.append(result)

            if result.get("confirmed"):
                confirmed_navigation = result
                break

    if confirmed_navigation:
        navigation = {
            "confirmed": True,
            "type": confirmed_navigation["type"],
            "selector": confirmed_navigation.get("selector"),
            "before": confirmed_navigation.get("before"),
            "after": confirmed_navigation.get("after"),
        }
    else:
        navigation = {
            "confirmed": False,
            "type": None,
            "selector": None,
        }

    return {
        "selector_tests": selector_tests,
        "selected_probe_selector": selected,
        "screenshot_test": screenshot_test,
        "navigation_tests": navigation_tests,
        "confirmed_navigation": navigation,
        "identity_test": identity,
    }
