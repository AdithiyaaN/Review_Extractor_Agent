from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from observer import observe, save_observation
from planner_llm import discover
from critic_llm import criticize
from behavioral_probe import run_probes
from final_planner_llm import finalize
from executor import execute

PROFILE = "./browser_profile"


def wait_for_login(page):
    if "/ap/signin" not in page.url:
        return
    print("\nAmazon/session login detected.")
    print("Sign in manually in the visible browser.")
    input("Press ENTER after login... ")
    if "/ap/signin" in page.url:
        raise RuntimeError("Still on sign-in page. Login was not completed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("Set GROQ_API_KEY first.")

    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    print("Review Scraper — 3 LLM architecture")
    print(f"URL: {args.url}")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1440, "height": 900},
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        wait_for_login(page)

        print("\n[Observer] collecting deterministic evidence...")
        observation = observe(page)
        save_observation(observation)
        print(f"  review candidate groups: {len(observation['review_candidates'])}")
        print(f"  actions: {len(observation['action_candidates'])}")
        print(f"  scroll containers: {len(observation['scroll_candidates'])}")
        print(f"  page scroll: {observation['page']['scroll']['height']} / {observation['page']['scroll']['viewportHeight']}")

        print("\n[LLM #1] discovery...")
        discovery = discover(client, observation)
        Path("proposed_plan.json").write_text(json.dumps(discovery, indent=2), encoding="utf-8")

        print("\n[LLM #2] adversarial investigation...")
        critique = criticize(client, observation, discovery)
        Path("validation.json").write_text(json.dumps(critique, indent=2), encoding="utf-8")

        print("\n[Playwright] behavioral probes...")
        probes = run_probes(page, discovery, critique)
        Path("behavioral_probe.json").write_text(json.dumps(probes, indent=2), encoding="utf-8")
        print(json.dumps(probes, indent=2))

        print("\n[LLM #3] final architecture...")
        final_plan = finalize(client, discovery, critique, probes)
        Path("final_plan.json").write_text(json.dumps(final_plan, indent=2), encoding="utf-8")

        print("\nFINAL PLAN")
        print(json.dumps(final_plan, indent=2))

        if args.execute:
            print("\n[Executor] starting screenshot extraction...")
            execute(page, final_plan)
        else:
            print("\nDry run only. Use --execute after reviewing final_plan.json.")

        browser.close()


if __name__ == "__main__":
    main()
