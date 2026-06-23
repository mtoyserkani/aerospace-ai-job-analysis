"""
debug_boeing.py — Show Boeing TalentBrew HTML structure to diagnose job card parsing.
"""
import asyncio, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

SEARCH_URL = "https://jobs.boeing.com/search-jobs"
LIST_PARAMS = (
    "ActiveFacetID=0&RecordsPerPage=15&TotalContentResults=&Distance=50"
    "&RadiusUnitType=0&Keywords=&Location=&ShowRadius=False&IsPagination=False"
    "&CustomFacetName=&FacetTerm=&FacetType=0"
    "&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters"
    "&SortCriteria=0&SortDirection=1&SearchType=5&PostalCode=&ResultsType=0"
    "&fc=&fl=&fcf=&afc=&afl=&afcf=&TotalContentPages=NaN"
)

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
        print("Loading Boeing careers page...")
        await page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        url = f"https://jobs.boeing.com/search-jobs/results?CurrentPage=1&{LIST_PARAMS}"
        result = await page.evaluate("""
            async ([url]) => {
                const resp = await fetch(url, {
                    headers: {'Accept': '*/*', 'X-Requested-With': 'XMLHttpRequest'},
                    credentials: 'include'
                });
                return await resp.text();
            }
        """, [url])
        data = json.loads(result)
        html = data.get("results", "")
        print(f"results HTML length: {len(html)}")
        print("\n--- chars 400-1000 ---")
        print(html[400:1000])
        print("\n--- chars 1000-2000 ---")
        print(html[1000:2000])

        # Count <li> tags with and without class
        bare_li = len(re.findall(r'<li>', html))
        class_li = len(re.findall(r'<li[^>]+>', html))
        print(f"\nBare <li> tags: {bare_li}")
        print(f"<li> with attributes: {class_li}")

        await browser.close()

asyncio.run(main())
