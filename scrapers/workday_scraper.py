"""
workday_scraper.py — Scrapes companies using the Workday ATS.

Architecture:
  1. List phase — CXS API (httpx POST, fast, no browser needed)
     POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{instance}/jobs
  2. Enrichment phase — Playwright (headless=False required, Workday blocks headless)
     Description selector: [data-automation-id='jobPostingDescription'] (confirmed 2026-06-12)
     Apply URL format: https://{tenant}.{wd}.myworkdayjobs.com/en-US/{instance}/job/...

Each company saves to its own file: data/workday_{company_key}.csv
Sample check runs after first 20 jobs — aborts if titles/URLs are broken.

Usage:
    python3 scrapers/workday_scraper.py                        # all companies
    python3 scrapers/workday_scraper.py --companies boeing     # one company
    python3 scrapers/workday_scraper.py --skip-enrichment      # list only, no descriptions
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, FIELDNAMES,
    infer_seniority, infer_remote, save_jobs, sample_check,
    extract_salary, extract_citizenship, extract_clearance,
    extract_relocation, _infer_country,
)

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "boeing": {
        "name":     "The Boeing Company",
        "tenant":   "boeing",
        "wd":       "wd1",
        "instance": "EXTERNAL_CAREERS",
        "note":     "May throttle after many requests. 502s trigger 30s retry.",
    },
    "sncorp": {
        "name":     "Sierra Nevada Corporation",
        "tenant":   "snc",
        "wd":       "wd1",
        "instance": "SNC_External_Career_Site",
    },
    "bah": {
        "name":     "Booz Allen Hamilton",
        "tenant":   "bah",
        "wd":       "wd1",
        "instance": "BAH_Jobs",
    },
    "wisk": {
        "name":     "Wisk Aero LLC",
        "tenant":   "wisk",
        "wd":       "wd108",
        "instance": "Wisk_Careers",
    },
    "airbus": {
        "name":     "Airbus",
        "tenant":   "ag",
        "wd":       "wd3",
        "instance": "Airbus",
    },
    "cae": {
        "name":     "CAE",
        "tenant":   "cae",
        "wd":       "wd3",
        "instance": "career",
    },
    "woodward": {
        "name":     "Woodward",
        "tenant":   "woodward",
        "wd":       "wd5",
        "instance": "woodward",
    },
    "crane": {
        "name":     "Crane",
        "tenant":   "cranecompany",
        "wd":       "wd5",
        "instance": "Careers",
    },
    "curtisswright": {
        "name":     "Curtiss-Wright",
        "tenant":   "curtisswright",
        "wd":       "wd1",
        "instance": "CW_External_Career_Site",
    },
    "moog": {
        "name":     "Moog Inc",
        "tenant":   "moog",
        "wd":       "wd5",
        "instance": "MOOG_External_Career_Site",
    },
    "blueorigin": {
        "name":     "Blue Origin",
        "tenant":   "blueorigin",
        "wd":       "wd5",
        "instance": "BlueOrigin",
    },
    "delta": {
        "name":     "Delta TechOps",
        "tenant":   "delta",
        "wd":       "wd5",
        "instance": "DeltaJobSearch",
        "note":     "Full Delta catalog — filter TechOps roles in analysis.",
    },
    "leidos": {
        "name":     "Leidos",
        "tenant":   "leidos",
        "wd":       "wd5",
        "instance": "External",
    },
    "vantor": {
        "name":     "Vantor",
        "tenant":   "maxar",
        "wd":       "wd1",
        "instance": "Vantor",
        "note":     "Formerly Maxar Intelligence. Rebranded 2025.",
    },
}

CXS_URL = "https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{instance}/jobs"

HEADERS = {
    "Accept":       "application/json",
    "Content-Type": "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

PAYLOAD = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(raw: dict) -> str:
    for key in ["postedOn", "startDate", "closingDate"]:
        val = raw.get(key, "")
        if val:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", str(val))
            if m:
                return m.group(1)
    return "N/A"


def _extract_job_id(raw: dict, tenant: str) -> str:
    bullet = raw.get("bulletFields", [])
    if bullet and bullet[0]:
        return str(bullet[0])
    ext_path = raw.get("externalPath", "")
    if ext_path:
        parts = ext_path.rstrip("/").split("/")
        if parts:
            return parts[-1]
    return Job.stable_id(ext_path or tenant)


def _build_apply_url(raw: dict, tenant: str, wd: str, instance: str) -> str:
    ext_path = raw.get("externalPath", "")
    if not ext_path:
        return ""
    if ext_path.startswith("/job/"):
        return f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{instance}{ext_path}"
    return f"https://{tenant}.{wd}.myworkdayjobs.com{ext_path}"


# ---------------------------------------------------------------------------
# List phase — CXS API
# ---------------------------------------------------------------------------

async def scrape_via_api(config: dict, limiter: RateLimiter) -> list[dict]:
    try:
        import httpx
    except ImportError:
        print("  httpx not installed: pip install httpx")
        return []

    tenant   = config["tenant"]
    instance = config["instance"]
    wd       = config.get("wd", "wd5")
    company  = config["name"]
    url      = CXS_URL.format(tenant=tenant, wd=wd, instance=instance)
    raw_jobs = []
    offset   = 0
    total    = None

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        while True:
            await limiter.wait()
            payload = {**PAYLOAD, "offset": offset}
            try:
                resp = await client.post(url, json=payload, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                print(f"  [{tenant}] API error at offset {offset}: {e}")
                if "502" in str(e) or "503" in str(e):
                    print(f"  [{tenant}] Waiting 30s and retrying...")
                    await asyncio.sleep(30)
                    try:
                        resp = await client.post(url, json=payload, timeout=30)
                        resp.raise_for_status()
                    except Exception as e2:
                        print(f"  [{tenant}] Retry failed: {e2} — stopping")
                        break
                else:
                    break

            data      = resp.json()
            total     = total if total is not None else data.get("total", 0)
            page_jobs = data.get("jobPostings", [])

            if not page_jobs:
                break

            raw_jobs.extend(page_jobs)
            offset += len(page_jobs)
            print(f"  [{tenant}] {offset}/{total} jobs listed...")

            if offset >= total:
                break

    return raw_jobs


# ---------------------------------------------------------------------------
# Build jobs without descriptions (skip-enrichment mode)
# ---------------------------------------------------------------------------

def _build_jobs_no_desc(raw_jobs: list[dict], config: dict) -> list[Job]:
    tenant   = config["tenant"]
    instance = config.get("instance", "External")
    wd       = config.get("wd", "wd5")
    company  = config["name"]
    jobs     = []

    for raw in raw_jobs:
        job_id    = _extract_job_id(raw, tenant)
        title     = raw.get("title", "").strip()
        location  = raw.get("locationsText", "")
        apply_url = _build_apply_url(raw, tenant, wd, instance)

        jobs.append(Job(
            company                = company,
            title                  = title,
            job_id                 = job_id,
            location               = location,
            country                = _infer_country(location),
            salary                 = "",
            remote                 = infer_remote(location),
            seniority              = infer_seniority(title),
            us_citizenship_required= "unknown",
            security_clearance     = "unknown",
            relocation_assistance  = "unknown",
            source_platform        = "workday",
            date_posted            = _parse_date(raw),
            apply_url              = apply_url,
            description_text       = "",
        ))

    return jobs


# ---------------------------------------------------------------------------
# Enrichment phase — Playwright (headless=False required)
# ---------------------------------------------------------------------------

async def enrich_with_descriptions(
    raw_jobs: list[dict],
    config: dict,
    limiter: RateLimiter,
) -> list[Job]:
    """
    Visit each job's detail page to extract description.
    headless=False required — Workday blocks headless browsers.
    Confirmed selector: [data-automation-id='jobPostingDescription'] (2026-06-12)
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not available — building jobs without descriptions")
        return _build_jobs_no_desc(raw_jobs, config)

    tenant   = config["tenant"]
    instance = config.get("instance", "External")
    wd       = config.get("wd", "wd5")
    company  = config["name"]
    jobs     = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        for i, raw in enumerate(raw_jobs):
            job_id    = _extract_job_id(raw, tenant)
            title     = raw.get("title", "").strip()
            location  = raw.get("locationsText", "")
            apply_url = _build_apply_url(raw, tenant, wd, instance)
            date_posted = _parse_date(raw)
            desc = ""

            if apply_url:
                await limiter.wait()
                try:
                    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)
                    desc_el = await page.query_selector("[data-automation-id='jobPostingDescription']")
                    if desc_el:
                        raw_desc = await desc_el.inner_text()
                        desc = re.sub(r"\s{2,}", " ", raw_desc).strip()
                except Exception:
                    pass

            if (i + 1) % 20 == 0:
                pct = int(100 * (i + 1) / len(raw_jobs))
                has_desc = sum(1 for j in jobs if j.description_text)
                print(f"  [{tenant}] {i+1}/{len(raw_jobs)} ({pct}%) — {has_desc} descriptions so far")

            jobs.append(Job(
                company                = company,
                title                  = title,
                job_id                 = job_id,
                location               = location,
                country                = _infer_country(location),
                salary                 = extract_salary(desc),
                remote                 = infer_remote(location, desc),
                seniority              = infer_seniority(title),
                us_citizenship_required= extract_citizenship(desc),
                security_clearance     = extract_clearance(desc),
                relocation_assistance  = extract_relocation(desc),
                source_platform        = "workday",
                date_posted            = date_posted,
                apply_url              = apply_url,
                description_text       = desc,
            ))

        await browser.close()

    return jobs


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def scrape_company(
    key: str,
    config: dict,
    output_dir: Path,
    skip_enrichment: bool,
    list_limiter: RateLimiter,
    detail_limiter: RateLimiter,
) -> int:
    company = config["name"]
    wd      = config.get("wd", "wd5")
    print(f"\nScraping {company} ({config['tenant']}.{wd}/{config['instance']})...")
    if config.get("note"):
        print(f"  Note: {config['note']}")

    raw_jobs = await scrape_via_api(config, list_limiter)
    print(f"  Listed: {len(raw_jobs)} jobs")

    if not raw_jobs:
        print(f"  No jobs found — skipping")
        return 0

    if skip_enrichment:
        jobs = _build_jobs_no_desc(raw_jobs, config)
    else:
        print(f"  Enriching with descriptions (browser will open)...")
        jobs = await enrich_with_descriptions(raw_jobs, config, detail_limiter)

    # Sample check after building jobs
    if not sample_check(jobs[:20], company, "workday"):
        print(f"  Skipping save due to sample check failure.")
        return 0

    output_path = output_dir / f"workday_{key}.csv"
    save_jobs(jobs, output_path)
    print(f"  Done: {len(jobs)} jobs → {output_path}")
    return len(jobs)


async def main(company_keys: list[str], output_dir: Path, skip_enrichment: bool) -> None:
    list_limiter   = RateLimiter(calls_per_minute=25)
    detail_limiter = RateLimiter(calls_per_minute=15)
    total = 0

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown company: {key}. Available: {', '.join(COMPANIES)}")
            continue
        count = await scrape_company(key, config, output_dir, skip_enrichment, list_limiter, detail_limiter)
        total += count

    print(f"\nTotal: {total} jobs across {len(company_keys)} companies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Workday ATS job boards")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()),
                        help=f"Companies to scrape. Options: {', '.join(COMPANIES.keys())}")
    parser.add_argument("--output-dir",      type=Path, default=Path("data"),
                        help="Directory for output files (one CSV per company)")
    parser.add_argument("--skip-enrichment", action="store_true",
                        help="Skip description fetches — fast list-only mode")
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output_dir, args.skip_enrichment))
