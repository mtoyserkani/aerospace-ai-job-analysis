"""
dom_inspector.py — Diagnostic tool for debugging ATS scraper failures.

Run this against any careers page to inspect the DOM structure before
writing or debugging a scraper. Identifies job card selectors, pagination
patterns, and API calls made during page load.

This was written to diagnose the GE Aerospace Phenom pagination issue
where the scraper was undercounting job results.

Usage:
    python dom_inspector.py --url https://careers.geaerospace.com
    python dom_inspector.py --url https://ngccareers.northropgrumman.com --save-html
    python dom_inspector.py --url https://boeing.wd1.myworkdayjobs.com/EXTERNAL_CAREERS/jobs --intercept-api
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


async def inspect(url: str, save_html: bool, intercept_api: bool) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed: pip install playwright && playwright install chromium")
        sys.exit(1)

    print(f"\nInspecting: {url}")
    print("=" * 60)

    api_calls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = await context.new_page()

        # Intercept API calls
        if intercept_api:
            async def on_request(req):
                if any(kw in req.url for kw in ["api", "jobs", "postings", "graphql", "v1", "v2"]):
                    api_calls.append({
                        "method":  req.method,
                        "url":     req.url,
                        "headers": dict(req.headers),
                    })
            page.on("request", on_request)

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"Page load error: {e}")
            await browser.close()
            return

        # ----------------------------------------------------------------
        # 1. Job card candidates
        # ----------------------------------------------------------------
        print("\n[ JOB CARD CANDIDATES ]")
        card_selectors = [
            "[class*='job-card']",
            "[class*='JobCard']",
            "[class*='job_card']",
            "[data-automation-id*='job']",
            "[data-ph-at-id*='job']",
            "article[class*='job']",
            "li[class*='job']",
            "[role='listitem']",
        ]
        for sel in card_selectors:
            try:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"  ✓ '{sel}': {len(els)} elements")
                    # Sample first element's text
                    text = (await els[0].inner_text())[:100].replace("\n", " ")
                    print(f"    Sample: {text}...")
            except Exception:
                pass

        # ----------------------------------------------------------------
        # 2. Title elements within cards
        # ----------------------------------------------------------------
        print("\n[ JOB TITLE SELECTORS ]")
        title_selectors = ["h2", "h3", "[class*='title']", "[class*='Title']", "a[href*='job']"]
        for sel in title_selectors:
            try:
                els = await page.query_selector_all(sel)
                if els:
                    texts = []
                    for el in els[:3]:
                        t = (await el.inner_text()).strip()
                        if t and len(t) < 100:
                            texts.append(t)
                    if texts:
                        print(f"  '{sel}': {len(els)} elements → {texts[:2]}")
            except Exception:
                pass

        # ----------------------------------------------------------------
        # 3. Pagination
        # ----------------------------------------------------------------
        print("\n[ PAGINATION SELECTORS ]")
        pag_selectors = [
            "button[aria-label*='Next']",
            "button[aria-label*='next']",
            "[class*='pagination']",
            "[class*='Pagination']",
            "[data-ph-at-id*='pagination']",
            "button:has-text('Next')",
            "[aria-label*='next page']",
        ]
        for sel in pag_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text    = (await el.inner_text()).strip()
                    disabled = await el.get_attribute("disabled")
                    print(f"  ✓ '{sel}': text='{text}' disabled={disabled is not None}")
            except Exception:
                pass

        # ----------------------------------------------------------------
        # 4. Total job count
        # ----------------------------------------------------------------
        print("\n[ JOB COUNT INDICATORS ]")
        count_selectors = [
            "[class*='count']", "[class*='Count']", "[class*='total']",
            "[class*='results']", "[aria-label*='result']",
        ]
        for sel in count_selectors:
            try:
                els = await page.query_selector_all(sel)
                for el in els[:3]:
                    text = (await el.inner_text()).strip()
                    if re.search(r"\d", text) and len(text) < 60:
                        print(f"  '{sel}': '{text}'")
            except Exception:
                pass

        # ----------------------------------------------------------------
        # 5. Page source snippet
        # ----------------------------------------------------------------
        if save_html:
            domain = urlparse(url).netloc.replace(".", "_")
            out    = Path(f"dom_inspection_{domain}.html")
            html   = await page.content()
            out.write_text(html, encoding="utf-8")
            print(f"\n[ HTML SAVED → {out} ]")

        # ----------------------------------------------------------------
        # 6. API calls intercepted
        # ----------------------------------------------------------------
        if intercept_api and api_calls:
            print(f"\n[ API CALLS INTERCEPTED: {len(api_calls)} ]")
            for call in api_calls[:10]:
                print(f"  {call['method']} {call['url'][:100]}")

        await browser.close()

    # ----------------------------------------------------------------
    # 7. Summary and recommendations
    # ----------------------------------------------------------------
    print("\n[ RECOMMENDATIONS ]")
    print("  1. Use the ✓ selectors above in your scraper")
    print("  2. If no job cards found: the page may require scroll or interaction first")
    print("  3. If pagination is missing: check if all jobs load on one page (infinite scroll?)")
    print("  4. If job count shows X but you scraped fewer: check pagination logic")
    if intercept_api:
        api_urls = [c["url"] for c in api_calls]
        job_apis = [u for u in api_urls if any(k in u for k in ["jobs", "postings", "apply/v2"])]
        if job_apis:
            print(f"\n  API endpoints found (use these instead of Playwright):")
            for u in job_apis[:5]:
                print(f"    {u}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect DOM structure of any ATS careers page")
    parser.add_argument("--url", required=True, help="Careers page URL to inspect")
    parser.add_argument("--save-html", action="store_true", help="Save full page HTML for manual review")
    parser.add_argument("--intercept-api", action="store_true", help="Log API calls made during page load")
    args = parser.parse_args()
    asyncio.run(inspect(args.url, args.save_html, args.intercept_api))
