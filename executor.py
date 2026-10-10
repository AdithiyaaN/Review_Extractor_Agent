from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


def _identity(el, strategy: dict) -> str:
    mode = strategy.get("strategy")
    attr = strategy.get("attribute")

    if mode in ("attribute", "id attribute") and attr:
        return el.get_attribute(attr) or ""

    if mode == "text":
        return " ".join((el.inner_text() or "").split())

    return ""


def execute(
    page: Page,
    plan: dict,
    output_dir: str = "screenshots",
    max_rounds: int = 250,
) -> int:
    # Do not execute an unproven plan.
    if plan.get("status") != "verified":
        raise RuntimeError(
            f"Refusing to execute plan with status={plan.get('status')}. "
            "Navigation, selector, identity and screenshot behavior must be verified."
        )

    if plan.get("screenshot", {}).get("method") != "element_screenshot":
        raise RuntimeError("Refusing non-element screenshot plan.")

    if not plan.get("navigation", {}).get("verified"):
        raise RuntimeError("Refusing plan with unverified navigation.")

    selector = plan["review"].get("selector")

    if not selector:
        raise RuntimeError("Final plan has no review selector.")

    nav = plan["navigation"]
    identity_strategy = plan["review"]["identity"]

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    seen = set()
    saved = 0

    def capture_visible_reviews():
        nonlocal saved

        loc = page.locator(selector)
        count = loc.count()

        for i in range(count):
            el = loc.nth(i)

            try:
                key = _identity(el, identity_strategy)

                # A stable identity is required by the verified plan.
                if not key:
                    print(f"  skipping review {i}: no stable identity")
                    continue

                if key in seen:
                    continue

                el.scroll_into_view_if_needed(timeout=5000)

                path = os.path.join(
                    output_dir,
                    f"review_{saved + 1:05d}.png",
                )

                el.screenshot(
                    path=path,
                    timeout=10000,
                )

                seen.add(key)
                saved += 1

                print(f"  screenshot {saved}: {key[:100]}")

            except Exception as exc:
                print(f"  screenshot failed index={i}: {exc}")

    for round_no in range(max_rounds):
        print(f"\n[EXECUTOR] Round {round_no + 1}/{max_rounds}")

        before = page.locator(selector).count()

        capture_visible_reviews()

        if nav["type"] == "load_more":
            try:
                import re

                button = None
                pattern = re.compile(
                    r"^(?:show|load)(?:\\s+\\d+)?\\s+more\\s+reviews?$",
                    re.IGNORECASE,
                )

                # Amazon frequently re-renders this control and changes its
                # generated id. The selector in final_plan.json is therefore
                # only the first attempt.
                button_selector = nav.get("selector")

                if button_selector:
                    try:
                        candidate = page.locator(button_selector).first
                        if candidate.is_visible() and candidate.is_enabled():
                            button = candidate
                    except Exception:
                        pass

                # IMPORTANT: explicitly search for the exact Amazon control
                # shown on the page: "Show 10 more reviews".
                #
                # This is intentionally the FIRST fallback after the frozen
                # selector. We are not relying on an inferred selector/id.
                if button is None:
                    exact = page.get_by_text(
                        "Show 10 more reviews",
                        exact=True,
                    )

                    for i in range(exact.count()):
                        candidate = exact.nth(i)

                        try:
                            if not candidate.is_visible():
                                continue

                            # The text is usually inside a button. Climb to the
                            # nearest button and click that actual control.
                            button_ancestor = candidate.locator(
                                "xpath=ancestor::button[1]"
                            )

                            if button_ancestor.count():
                                parent = button_ancestor.first

                                if parent.is_visible() and parent.is_enabled():
                                    button = parent
                                    print(
                                        '  found exact "Show 10 more reviews" button'
                                    )
                                    break

                            # If the text node itself is the button, use it.
                            if candidate.is_enabled():
                                button = candidate
                                print(
                                    '  found exact "Show 10 more reviews" control'
                                )
                                break

                        except Exception:
                            continue

                # Also support the same control when Amazon changes the number:
                # "Show 20 more reviews", "Show 10 more reviews", etc.
                if button is None:
                    candidates = page.locator("button")

                    for i in range(candidates.count()):
                        candidate = candidates.nth(i)

                        try:
                            if not candidate.is_visible():
                                continue

                            text = " ".join(
                                (candidate.inner_text() or "").split()
                            )

                            if pattern.match(text) and candidate.is_enabled():
                                button = candidate
                                print(
                                    f"  found Load More button by text: {text}"
                                )
                                break

                        except Exception:
                            continue

                # Amazon's visible control can sometimes be represented by a
                # span inside the actual button. Find the text span and climb
                # to its nearest clickable ancestor.
                if button is None:
                    text_nodes = page.locator("span, div, a")

                    for i in range(text_nodes.count()):
                        candidate = text_nodes.nth(i)

                        try:
                            if not candidate.is_visible():
                                continue

                            text = " ".join(
                                (candidate.inner_text() or "").split()
                            )

                            if not pattern.match(text):
                                continue

                            clickable = candidate.locator(
                                "xpath=ancestor-or-self::button[1]"
                            )

                            if clickable.count():
                                parent = clickable.first
                                if parent.is_visible() and parent.is_enabled():
                                    button = parent
                                    print(
                                        f"  found Load More via nested text: {text}"
                                    )
                                    break

                            clickable = candidate.locator(
                                "xpath=ancestor-or-self::*[@role='button'][1]"
                            )

                            if clickable.count():
                                parent = clickable.first
                                if parent.is_visible() and parent.is_enabled():
                                    button = parent
                                    print(
                                        f"  found Load More via role ancestor: {text}"
                                    )
                                    break

                            clickable = candidate.locator(
                                "xpath=ancestor-or-self::a[1]"
                            )

                            if clickable.count():
                                parent = clickable.first
                                if parent.is_visible():
                                    button = parent
                                    print(
                                        f"  found Load More via link ancestor: {text}"
                                    )
                                    break

                        except Exception:
                            continue

                # Last resort: inspect the DOM directly and return the
                # matching clickable element. This handles Amazon markup that
                # does not expose a useful accessible role.
                if button is None:
                    try:
                        handle = page.evaluate(
                            """() => {
                                const norm = s => (s || '')
                                    .replace(/\\s+/g, ' ')
                                    .trim();

                                const re =
                                    /^(show|load)(\\s+\\d+)?\\s+more\\s+reviews?$/i;

                                const els = [
                                    ...document.querySelectorAll(
                                        'button, a, [role="button"]'
                                    )
                                ];

                                for (const el of els) {
                                    const text = norm(
                                        el.innerText ||
                                        el.textContent ||
                                        el.getAttribute('aria-label') ||
                                        el.getAttribute('title') ||
                                        ''
                                    );

                                    if (!re.test(text))
                                        continue;

                                    const r = el.getBoundingClientRect();
                                    const style = getComputedStyle(el);

                                    if (
                                        r.width > 0 &&
                                        r.height > 0 &&
                                        style.display !== 'none' &&
                                        style.visibility !== 'hidden'
                                    ) {
                                        return true;
                                    }
                                }

                                return false;
                            }"""
                        )

                        if handle:
                            candidates = page.locator(
                                'button, a, [role="button"]'
                            )

                            for i in range(candidates.count()):
                                candidate = candidates.nth(i)

                                try:
                                    text = " ".join(
                                        (
                                            candidate.inner_text()
                                            or candidate.get_attribute(
                                                "aria-label"
                                            )
                                            or ""
                                        ).split()
                                    )

                                    if (
                                        pattern.match(text)
                                        and candidate.is_visible()
                                    ):
                                        button = candidate
                                        print(
                                            f"  found Load More via DOM scan: {text}"
                                        )
                                        break

                                except Exception:
                                    continue

                    except Exception:
                        pass

                if button is None:
                    # Diagnostic output for the next run. This tells us exactly
                    # what Amazon exposed as visible clickable controls.
                    print(
                        "No visible Show/Load More Reviews button found."
                    )

                    try:
                        visible_buttons = page.locator(
                            "button, a, [role='button']"
                        )

                        print("  Visible clickable controls:")

                        shown = 0
                        for i in range(visible_buttons.count()):
                            candidate = visible_buttons.nth(i)

                            try:
                                if not candidate.is_visible():
                                    continue

                                text = " ".join(
                                    (
                                        candidate.inner_text()
                                        or candidate.get_attribute("aria-label")
                                        or candidate.get_attribute("title")
                                        or ""
                                    ).split()
                                )

                                if text:
                                    print(f"    - {text[:150]}")
                                    shown += 1

                                if shown >= 30:
                                    break

                            except Exception:
                                continue

                    except Exception as exc:
                        print(f"  diagnostic failed: {exc}")

                    break

                button.scroll_into_view_if_needed(timeout=8000)
                page.wait_for_timeout(500)

                if not button.is_enabled():
                    print("Load More disabled. Finished.")
                    break

                print("  clicking Show/Load More Reviews...")
                button.click(timeout=10000)

                # Wait for Amazon to append the next batch.
                page.wait_for_timeout(1800)

                after = page.locator(selector).count()

                if after <= before:
                    page.wait_for_timeout(3000)
                    after = page.locator(selector).count()

                if after <= before:
                    print(
                        f"Load More click produced no new reviews "
                        f"({before} -> {after}). Finished."
                    )
                    break

                print(f"  navigation: {before} -> {after}")
                continue

            except Exception as exc:
                print(f"Load More stopped: {exc}")
                break

        elif nav["type"] == "internal_scroll":
            container = nav.get("selector")

            if not container:
                raise RuntimeError(
                    "Verified internal_scroll plan has no container."
                )

            state = page.locator(container).evaluate(
                "(e) => ({top:e.scrollTop,height:e.scrollHeight,client:e.clientHeight})"
            )

            if state["top"] + state["client"] >= state["height"] - 5:
                page.wait_for_timeout(800)

                new_state = page.locator(container).evaluate(
                    "(e) => ({top:e.scrollTop,height:e.scrollHeight,client:e.clientHeight})"
                )

                if new_state["top"] + new_state["client"] >= new_state["height"] - 5:
                    print("Internal scroll reached bottom. Finished.")
                    break

            page.locator(container).evaluate(
                """e => e.scrollTop = Math.min(
                    e.scrollTop + Math.max(700, e.clientHeight * 0.8),
                    e.scrollHeight
                )"""
            )

            page.wait_for_timeout(900)

        elif nav["type"] == "page_scroll":
            state = page.evaluate(
                """() => ({
                    y: scrollY,
                    height: document.documentElement.scrollHeight,
                    client: innerHeight
                })"""
            )

            if state["y"] + state["client"] >= state["height"] - 5:
                page.wait_for_timeout(800)
                print("Page reached bottom. Finished.")
                break

            page.evaluate(
                "window.scrollBy(0, Math.max(700, innerHeight * 0.8))"
            )

            page.wait_for_timeout(900)

        elif nav["type"] == "pagination":
            button_selector = nav.get("selector")

            if not button_selector:
                raise RuntimeError(
                    "Verified pagination plan has no selector."
                )

            try:
                page.locator(button_selector).first.click(timeout=8000)
                page.wait_for_timeout(1200)

            except Exception as exc:
                print(f"Pagination stopped: {exc}")
                break

        else:
            raise RuntimeError(
                f"Unsupported verified navigation type: {nav['type']}"
            )

    capture_visible_reviews()

    print(f"\nFinished. Total screenshots: {saved}")

    return saved


def main():
    parser = argparse.ArgumentParser(
        description="Execute a verified review screenshot plan."
    )

    parser.add_argument(
        "plan",
        help="Path to final_plan.json",
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute the plan.",
    )

    parser.add_argument(
        "--url",
        default=None,
        help="URL to open before executing.",
    )

    parser.add_argument(
        "--profile",
        default="./browser_profile",
        help="Persistent Playwright browser profile.",
    )

    parser.add_argument(
        "--output",
        default="screenshots",
        help="Directory for review screenshots.",
    )

    args = parser.parse_args()

    plan_path = Path(args.plan)

    if not plan_path.exists():
        raise FileNotFoundError(
            f"Plan file not found: {plan_path}"
        )

    plan = json.loads(
        plan_path.read_text(encoding="utf-8")
    )

    print("Review Scraper — Executor")
    print(f"Plan: {plan_path}")
    print(f"Status: {plan.get('status')}")

    if not args.execute:
        print("\nDry run only.")
        print("Use --execute to execute the verified plan.")
        return

    if plan.get("status") != "verified":
        raise RuntimeError(
            f"Refusing to execute plan with status={plan.get('status')}"
        )

    with sync_playwright() as p:
        print("\nLaunching persistent browser...")

        context = p.chromium.launch_persistent_context(
            args.profile,
            headless=False,
            viewport={
                "width": 1440,
                "height": 900,
            },
        )

        try:
            pages = context.pages

            if pages:
                page = pages[0]
            else:
                page = context.new_page()

            if args.url:
                print(f"Opening URL: {args.url}")

                page.goto(
                    args.url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                page.wait_for_timeout(3000)

            print(f"Current URL: {page.url}")
            print("\nExecuting verified plan...\n")

            saved = execute(
                page=page,
                plan=plan,
                output_dir=args.output,
            )

            print(f"\nExecution complete: {saved} screenshots saved.")
            print("Browser is still open.")

            input("Press Enter to close the browser...")

        finally:
            context.close()


if __name__ == "__main__":
    main()
