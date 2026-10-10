# Review Scraper — 3 LLM architecture

## Architecture

1. **Observer** — deterministic Playwright evidence collection.
2. **LLM #1 / Discovery** — high-recall hypotheses.
3. **LLM #2 / Adversarial Investigator** — attacks those hypotheses.
4. **Playwright probes** — objectively tests selectors/navigation/screenshots.
5. **LLM #3 / Final Architect** — synthesizes evidence into a frozen plan.
6. **Executor** — deterministic screenshot extraction.

The LLMs reason. Playwright proves.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

Set:

```bash
export GROQ_API_KEY="..."
```

## Dry run

```bash
.venv/bin/python dom_snap.py "https://www.amazon.in/portal/customer-reviews/B0CHJ3JPJW/ref=cm_cr_dp_d_show_all_top?_encoding=UTF8&ie=UTF8&reviewerType=all_reviews"
```

This writes:

- `dom_candidates.json`
- `proposed_plan.json`
- `validation.json`
- `behavioral_probe.json`
- `final_plan.json`

## Execute

Only after checking the final plan:

```bash
.venv/bin/python dom_snap.py "URL" --execute
```

Screenshots are written to `screenshots/`.

## Why this fixes the Amazon failure

A selector such as:

```text
#R3EHA4QNQ5WLTK
```

can be discovered by LLM #1 but is rejected by the deterministic cardinality gate if it matches one element.

A repeated selector such as:

```text
li.review
```

can survive if:

- it matches multiple reviews,
- a screenshot succeeds,
- navigation actually increases the review set,
- identity evidence is usable.

The final LLM receives those measurements and cannot reasonably choose the unique element as the collection selector.

## Important

For virtualized sites, `count()` alone is not enough. The next iteration should add stronger recycled-node detection and review-content hashing. This implementation already records identity evidence so that can be added without changing the three-LLM architecture.
