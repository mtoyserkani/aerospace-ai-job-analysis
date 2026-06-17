"""
icims_scraper.py — Scrapes companies using the iCIMS ATS.

KEY FINDINGS (earned through ~2 hours of debugging):
  1. headless=False REQUIRED — iCIMS detects headless Playwright and blocks job rendering
  2. Jobs render in Frame 1 (iframe with in_iframe=1 in URL), NOT the main page
  3. Pagination URL pattern: ?pr=N where N = page_number - 1
  4. Description selector: div.iCIMS_JobContent in Frame 1
  5. Title prefix bug: iCIMS injects "Title\n" before link text — must be stripped

Companies confirmed using iCIMS:
  - Joby Aviation → careers-jobyaviation.icims.com
  - General Dynamics → careers-gd-ots.icims.com
  - Peraton → careers-peraton.icims.com
  - Spirit AeroSystems → careers-spiritaero.icims.com
  - Ducommun → careers-ducommun.icims.com
  - Astronics → careers-astronics.icims.com
  - Elbit Systems → careers-elbitsystemsofamerica.icims.com

Usage:
    python scrapers/icims_scraper.py --companies jobyaviation --output-dir data
"""

import argparse, asyncio, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, save_jobs, sample_check,
    extract_salary, extract_citizenship, extract_clearance, extract_relocation,
    _infer_country,
)

COMPANIES = {
    "jobyaviation": {
        "name":       "Joby Aviation",
        "domain":     "careers-jobyaviation.icims.com",
        "search_url": "https://careers-jobyaviation.icims.com/jobs/search?ss=1",
    },
    "spiritaero": {
        "name":       "Spirit AeroSystems",
        "domain":     "careers-spiritaero.icims.com",
        "search_url": "https://careers-spiritaero.icims.com/jobs/search?ss=1",
    },
    "ducommun": {
        "name":       "Ducommun",
        "domain":     "careers-ducommun.icims.com",
        "search_url": "https://careers-ducommun.icims.com/jobs/search?ss=1",
    },
    "astronics": {
        "name":       "Astronics Corporation",
        "domain":     "careers-astronics.icims.com",
        "search_url": "https://careers-astronics.icims.com/jobs/search?ss=1",
    },
    "elbitsystems": {
        "name":       "Elbit Systems of America",
        "domain":     "careers-elbitsystemsofamerica.icims.com",
        "search_url": "https://careers-elbitsystemsofamerica.icims.com/jobs/search?ss=1",
    },
    "peraton": {
        "name":       "Peraton",
        "domain":     "careers-peraton.icims.com",
        "search_url": "https://careers-peraton.icims.com/jobs/search?ss=1",
    },
    "generaldynamics": {
        "name":       "General Dynamics",
        "domain":     "careers-gd-ots.icims.com",
        "search_url": "https://careers-gd-ots.icims.com/jobs/search?ss=1",
    },
}


def clean_title(text):
    """Strip iCIMS 'Title\n' label prefix injected before link text."""
    text = text.strip()
    if text.lower().startswith("title\n"):
        text = text[6:].strip()
    return text


async def fetch_description(page, url, limiter):
    """Visit a job detail page and extract description from iCIMS Frame 1."""
    await limiter.wait()
    try:
        await page.goto(url + "?in_iframe=1", wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(2)
        icims_frame = next(
            (f for f in page.frames if "in_iframe=1" in f.url),
            page.main_frame
        )
        el = await icims_frame.query_selector("div.iCIMS_JobContent")
        if el:
            return (await el.inner_text()).strip()
    except Exception as e:
        print(f"    Desc fetch failed: {e}")
    return ""


async def scrape_company(slug, config, limiter):
    from playwright.async_api import async_playwright
    company_name = config["name"]
    domain = config["domain"]
    base_url = f"https://{domain}"
    jobs, seen = [], set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # --- Phase 1: Collect all job titles + URLs ---
        print(f"  Phase 1: collecting job list...")
        try:
            await page.goto(config["search_url"], wait_until="networkidle", timeout=90000)
        except Exception:
            await page.goto(config["search_url"], wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)
        await asyncio.sleep(3)

        submit = await page.query_selector("input[type='submit'], button[type='submit']")
        if submit:
            try:
                await submit.click(timeout=5000)
            except Exception:
                await page.evaluate("""
                    var el = document.querySelector('#search-submit') ||
                             document.querySelector('input[type="submit"]') ||
                             document.querySelector('button[type="submit"]');
                    if (el) el.click();
                """)
            await asyncio.sleep(5)

        # Detect total pages
        total_pages = 13
        icims_frame = next((f for f in page.frames if "in_iframe=1" in f.url), page.main_frame)
        try:
            txt = await icims_frame.evaluate("document.body.innerText")
            m = re.search(r"Page \d+ of (\d+)", txt)
            if m:
                total_pages = int(m.group(1))
        except Exception:
            pass
        print(f"  Total pages: {total_pages}")

        for page_num in range(1, total_pages + 1):
            await limiter.wait()
            if page_num > 1:
                search_base = config.get("search_url", f"https://{domain}/jobs/search?ss=1")
                base_no_params = search_base.split("?")[0]
                pr_url = f"{base_no_params}?ss=1&pr={page_num - 1}&in_iframe=1"
                try:
                    await page.goto(pr_url, wait_until="networkidle", timeout=60000)
                except Exception:
                    await page.goto(pr_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(4000)
                await asyncio.sleep(2)

            icims_frame = next((f for f in page.frames if "in_iframe=1" in f.url), page.main_frame)
            all_links = await icims_frame.query_selector_all("a[href]")
            page_jobs = 0
            for link in all_links:
                try:
                    href = await link.get_attribute("href") or ""
                    text = clean_title((await link.inner_text()).strip())
                    if not re.search(r"/jobs/\d+", href) or len(text) < 4:
                        continue
                    if text.lower() in ("search", "home", "back", "next", "previous", "welcome page", "title"):
                        continue
                    url = href if href.startswith("http") else base_url + href
                    clean_url = re.sub(r'[?&]in_iframe=1', '', url).rstrip('?&')
                    if clean_url in seen:
                        continue
                    seen.add(clean_url)
                    job_id_m = re.search(r"/jobs/(\d+)/", href)
                    jobs.append(Job(
                        company                = company_name,
                        title                  = text,
                        job_id                 = job_id_m.group(1) if job_id_m else Job.stable_id(clean_url),
                        location               = "",
                        country                = "United States of America",
                        salary                 = "",
                        remote                 = "unknown",
                        seniority              = infer_seniority(text),
                        us_citizenship_required= "unknown",
                        security_clearance     = "unknown",
                        relocation_assistance  = "unknown",
                        source_platform        = "icims",
                        date_posted            = "N/A",
                        apply_url              = clean_url,
                        description_text       = "",
                    ))
                    page_jobs += 1
                except Exception:
                    continue
            print(f"  Page {page_num}/{total_pages}: {page_jobs} jobs (total: {len(jobs)})")

        # --- Phase 2: Enrich descriptions ---
        print(f"\n  Phase 2: fetching descriptions for {len(jobs)} jobs...")
        enrich_limiter = RateLimiter(calls_per_minute=20)
        for i, job in enumerate(jobs):
            desc = await fetch_description(page, job.apply_url, enrich_limiter)
            if desc:
                job.description_text       = desc
                job.salary                 = extract_salary(desc)
                job.us_citizenship_required= extract_citizenship(desc)
                job.security_clearance     = extract_clearance(desc)
                job.relocation_assistance  = extract_relocation(desc)
                job.country                = _infer_country(job.location) or job.country
            if (i + 1) % 10 == 0:
                print(f"    Enriched {i+1}/{len(jobs)}")

        await browser.close()
    return jobs


async def main(company_keys, output_dir):
    limiter = RateLimiter(calls_per_minute=10)
    total = 0

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown: {key}. Available: {', '.join(COMPANIES.keys())}")
            continue
        print(f"\nScraping {config['name']}... (browser will open — do not interact)")
        jobs = await scrape_company(key, config, limiter)
        print(f"  Total: {len(jobs)} jobs")

        if not jobs:
            print(f"  No jobs found — skipping")
            continue

        if not sample_check(jobs[:20], config["name"], "icims"):
            print(f"  Skipping save due to sample check failure.")
            continue

        output_path = output_dir / f"icims_{key}.csv"
        save_jobs(jobs, output_path)
        total += len(jobs)

    print(f"\nDone: {total} total jobs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()))
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Directory for output files (one CSV per company)")
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output_dir))
