"""
debug_l3harris.py — Dump L3Harris results HTML to inspect job card structure.
"""
import json, sys, asyncio

LIST_URL = "https://careers.l3harris.com/en/search-jobs/results"
PARAMS = "CurrentPage=1&ActiveFacetID=0&RecordsPerPage=15&TotalContentResults=&Distance=50&RadiusUnitType=0&Keywords=&Location=&ShowRadius=False&IsPagination=False&CustomFacetName=&FacetTerm=&FacetType=0&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=1&SearchType=5&PostalCode=&ResultsType=0&fc=&fl=&fcf=&afc=&afl=&afcf=&TotalContentPages=NaN"
URL = f"{LIST_URL}?{PARAMS}"

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("playwright not installed")
    sys.exit(1)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        print("Loading search page...")
        await page.goto("https://careers.l3harris.com/en/search-jobs", wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(4000)

        result = await page.evaluate("""
            async (url) => {
                const resp = await fetch(url, {
                    headers: {'Accept': '*/*', 'X-Requested-With': 'XMLHttpRequest'},
                    credentials: 'include'
                });
                return await resp.text();
            }
        """, URL)
        await browser.close()

    data = json.loads(result)
    html = data.get("results", "")
    print(f"results HTML length: {len(html)}")

    # Dump chars 400-2000 to see job card structure (skip section opening tag)
    print("\n--- HTML chars 400-2500 ---")
    print(html[400:2500])

asyncio.run(main())
