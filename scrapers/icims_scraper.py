"""
icims_scraper.py — Scrapes companies using the iCIMS ATS.

KEY FINDINGS (earned through ~2 hours of debugging):
  1. headless=False REQUIRED — iCIMS detects headless Playwright and blocks job rendering
  2. Jobs render in Frame 1 (iframe with in_iframe=1 in URL), NOT the main page
  3. Pagination URL pattern: ?pr=N where N = page_number - 1
     Page 1 = submit search form, Page 2 = ?pr=1, Page 3 = ?pr=2, etc.
  4. Job title links have /jobs/{numeric_id}/ pattern inside the iframe

Companies confirmed using iCIMS:
  - Joby Aviation → careers-jobyaviation.icims.com (241 jobs, 13 pages)

To add a new iCIMS company:
  1. Confirm their careers page uses iCIMS (URL will contain .icims.com)
  2. Find the slug: careers-{slug}.icims.com
  3. Add to COMPANIES dict below
  4. Run and verify page count detection works

Usage:
    pip install playwright && playwright install chromium
    python scrapers/icims_scraper.py --companies jobyaviation --output data/icims_jobs.csv

NOTE: A browser window will open and navigate automatically. Do not interact with it.
"""

import argparse, asyncio, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, infer_remote, save_jobs, sample_check,
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
        "note":       "Domain confirmed 2026-06-11 via web search.",
    },
    "generaldynamics": {
        "name":       "General Dynamics",
        "domain":     "careers-gd-ots.icims.com",
        "search_url": "https://careers-gd-ots.icims.com/jobs/search?ss=1",
        "note":       "GD OTS division confirmed 2026-06-06 via apply URL inspection.",
    },
}


async def scrape_company(slug, config, limiter):  # config passed through for search_url
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

        # Load page 1 and submit search form
        print(f"  Loading page 1...")
        try:
            await page.goto(config["search_url"], wait_until="networkidle", timeout=90000)
        except Exception:
            # Fallback: wait for domcontentloaded instead of networkidle
            await page.goto(config["search_url"], wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)
        await asyncio.sleep(3)
        submit = await page.query_selector("input[type='submit'], button[type='submit']")
        if submit:
            # Use JS click to bypass sticky header interception on some iCIMS instances
            try:
                await submit.click(timeout=5000)
            except Exception:
                try:
                    await page.evaluate("""
                        var el = document.querySelector('#search-submit') ||
                                 document.querySelector('input[type="submit"]') ||
                                 document.querySelector('button[type="submit"]');
                        if (el) el.click();
                    """)
                except Exception:
                    pass
            await asyncio.sleep(2)
            await asyncio.sleep(5)

        # Detect total pages from iframe content
        total_pages = 13  # fallback
        icims_frame = next((f for f in page.frames if "in_iframe=1" in f.url), page.main_frame)
        try:
            txt = await icims_frame.evaluate("document.body.innerText")
            m = re.search(r"Page \d+ of (\d+)", txt)
            if m:
                total_pages = int(m.group(1))
        except Exception:
            pass
        print(f"  Total pages: {total_pages}")

        # Scrape all pages using direct URL navigation (?pr=N)
        for page_num in range(1, total_pages + 1):
            await limiter.wait()

            if page_num > 1:
                # Use the company's search_url as base for pagination
                search_base = config.get("search_url", f"https://{domain}/jobs/search?ss=1")
                # Remove existing params, add pagination
                base_no_params = search_base.split("?")[0]
                pr_url = f"{base_no_params}?ss=1&pr={page_num - 1}&in_iframe=1"
                try:
                    await page.goto(pr_url, wait_until="networkidle", timeout=60000)
                except Exception:
                    await page.goto(pr_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(4000)
                await asyncio.sleep(2)

            # Find the iframe (jobs are in Frame 1, not main page)
            icims_frame = next((f for f in page.frames if "in_iframe=1" in f.url), page.main_frame)

            all_links = await icims_frame.query_selector_all("a[href]")
            page_jobs = 0
            for link in all_links:
                try:
                    href = await link.get_attribute("href") or ""
                    raw_text = (await link.inner_text()).strip()
                    if not re.search(r"/jobs/\d+", href) or len(raw_text) < 4:
                        continue
                    if raw_text.lower() in ("search","home","back","next","previous","welcome page","title"):
                        continue

                    # Strip iCIMS label prefixes that leak into link text:
                    # "Requisition Title\nActual Title" or "External Job Posting Title\nActual Title"
                    text = raw_text
                    for prefix in ["Requisition Title\n", "External Job Posting Title\n", "Job Title\n"]:
                        if text.startswith(prefix):
                            text = text[len(prefix):].strip()
                            break
                    # Fallback: if newline present and first line matches known label patterns, drop it
                    if "\n" in text:
                        lines = text.split("\n")
                        if lines[0].strip().lower() in ("requisition title", "external job posting title", "job title"):
                            text = "\n".join(lines[1:]).strip()

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

        # Enrich with descriptions + location by visiting each job page.
        # Job content is in whichever frame has the most text (confirmed 2026-06-16):
        # the iCIMS detail page has 2 frames with similar URLs — one is footer/nav
        # boilerplate (~400 chars), the other has the real job content (~3000+ chars).
        # NOTE: browser stays open through enrichment — closing it early breaks page.goto()
        print(f"  Enriching {len(jobs)} jobs with descriptions...")
        enriched = 0
        for i, job in enumerate(jobs):
            try:
                try:
                    await page.goto(job.apply_url, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass  # proceed even if goto times out — content may have loaded enough
                await page.wait_for_timeout(2500)

                best_text = ""
                for frame in page.frames:
                    try:
                        text = await frame.inner_text("body")
                        # Reject frame text that's clearly not job content --
                        # ad-tracking JS that happened to render more characters
                        # than the real content frame. Confirmed bug: 83 Peraton
                        # postings got a minified consent-script frame instead of
                        # the real job description, because this loop previously
                        # picked the frame with the most text with no check that
                        # it was actually prose. Disqualifying JS-shaped text lets
                        # the real content frame win by default instead.
                        if re.search(r"function\s*[a-zA-Z0-9_]*\s*\(|googletagmanager|dataLayer\.push", text):
                            continue
                        if len(text) > len(best_text):
                            best_text = text
                    except Exception:
                        continue

                if len(best_text) > 100:
                    raw = re.sub(r"\s{2,}", " ", best_text).strip()
                    job.description_text = raw
                    # Extract location from "Job Locations\nUS-CA-Santa Cruz" pattern
                    loc_m = re.search(r"Job Locations?\s+([A-Z]{2}-[A-Za-z .]+(?:-[A-Za-z .]+)?)", raw)
                    if loc_m:
                        job.location = loc_m.group(1).replace("-", ", ").replace("US, ", "")
                        job.country = "United States of America"
                    enriched += 1
            except Exception:
                pass
            if (i + 1) % 50 == 0:
                print(f"    {i+1}/{len(jobs)} — {enriched} descriptions so far")

        await browser.close()
    print(f"  Enrichment done: {enriched}/{len(jobs)} descriptions")
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
