"""
greenhouse_scraper.py — Scrapes companies using the Greenhouse ATS.

Greenhouse exposes a public jobs board API. No authentication required.
This is the cleanest scraper in the set — no Playwright needed.

Companies in this dataset using Greenhouse:
  - Relativity Space   → boards.greenhouse.io/relativityspace
  - Rocket Lab         → boards.greenhouse.io/rocketlab
  - Planet Labs        → boards.greenhouse.io/planetlabs
  - SpaceX             → boards.greenhouse.io/spacex
  - Archer Aviation    → boards.greenhouse.io/archer56  ✅ confirmed 2026-06-06 via dom_inspector

API reference: https://developers.greenhouse.io/job-board.html
Rate limit: ~60 req/min before throttling. We use 30 to be safe.

Usage:
    python3 greenhouse_scraper.py --output data/greenhouse_jobs.csv
    python3 greenhouse_scraper.py --companies archer56 --output data/archer_jobs.csv
"""

import argparse
import asyncio
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, infer_remote, save_jobs, sample_check,
    extract_salary, extract_citizenship, extract_clearance, extract_relocation,
    _infer_country,
)

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "relativityspace": "Relativity Space",
    "rocketlab":       "Rocket Lab",
    "planetlabs":      "Planet Labs",
    "spacex":          "SpaceX",
    "archer56":        "Archer Aviation",   # confirmed 2026-06-06 via dom_inspector
    "flyzipline":      "Zipline",           # confirmed 2026-06-06
    "mercury":         "Mercury Systems",   # confirmed 2026-06-06
    "heartaerospace":  "Heart Aerospace",   # confirmed 2026-06-06
    "wing":            "Wing Aviation",     # confirmed 2026-06-12; 25 jobs
}

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

# ---------------------------------------------------------------------------
# HTML → plain text
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return " ".join(self._parts).strip()


def strip_html(html: str) -> str:
    if not html:
        return ""
    s = _HTMLStripper()
    s.feed(html)
    text = s.get_text()
    return re.sub(r"\s{2,}", " ", text)


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

async def scrape_company(
    slug: str,
    company_name: str,
    client: httpx.AsyncClient,
    limiter: RateLimiter,
) -> list[Job]:
    """Fetch all jobs for one Greenhouse company slug."""
    await limiter.wait()
    try:
        resp = await client.get(
            BASE_URL.format(slug=slug),
            params={"content": "true"},
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"  [{slug}] HTTP {e.response.status_code} — skipping")
        return []
    except httpx.RequestError as e:
        print(f"  [{slug}] Request error: {e} — skipping")
        return []

    data     = resp.json()
    raw_jobs = data.get("jobs", [])
    print(f"  [{slug}] Found {len(raw_jobs)} jobs")

    jobs = []
    for raw in raw_jobs:
        job_id    = str(raw.get("id", ""))
        title     = raw.get("title", "").strip()
        apply_url = raw.get("absolute_url", "")

        # Location
        loc_data  = raw.get("location", {})
        location  = loc_data.get("name", "") if isinstance(loc_data, dict) else str(loc_data)
        country   = _infer_country(location)

        # Description — Greenhouse returns HTML in content.body
        content   = raw.get("content", "") or ""
        desc      = strip_html(content)

        # Date posted — Greenhouse returns updated_at ISO string
        date_posted = _parse_date(raw.get("updated_at", ""))

        # Extracted fields
        salary     = extract_salary(desc)
        citizenship = extract_citizenship(desc)
        clearance  = extract_clearance(desc)
        relocation = extract_relocation(desc)

        jobs.append(Job(
            company                = company_name,
            title                  = title,
            job_id                 = job_id,
            location               = location,
            country                = country,
            salary                 = salary,
            remote                 = infer_remote(location, desc),
            seniority              = infer_seniority(title),
            us_citizenship_required= citizenship,
            security_clearance     = clearance,
            relocation_assistance  = relocation,
            source_platform        = "greenhouse",
            date_posted            = date_posted,
            apply_url              = apply_url,
            description_text       = desc,
        ))

    return jobs


def _parse_date(value: str) -> str:
    """Extract YYYY-MM-DD from Greenhouse ISO timestamp. Returns 'N/A' if missing."""
    if not value:
        return "N/A"
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else "N/A"


def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["uk", "united kingdom", "london", "england"]):
        return "United Kingdom"
    if any(k in loc for k in ["canada", "ontario", "toronto", "vancouver", " bc", " ab"]):
        return "Canada"
    if any(k in loc for k in ["germany", "münchen", "berlin", "hamburg"]):
        return "Germany"
    return "United States of America"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(slugs: list[str], output_dir: Path) -> None:
    limiter  = RateLimiter(calls_per_minute=30)
    total    = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": "aerospace-job-research/1.0 (github.com/mtoyserkani/aerospace-ai-job-analysis)"},
        follow_redirects=True,
    ) as client:
        for slug in slugs:
            company_name = COMPANIES.get(slug, slug.title())
            print(f"\nScraping {company_name} ({slug})...")
            jobs = await scrape_company(slug, company_name, client, limiter)
            if not jobs:
                continue
            if not sample_check(jobs[:20], company_name, "greenhouse"):
                continue
            output_path = output_dir / f"greenhouse_{slug}.csv"
            save_jobs(jobs, output_path)
            total += len(jobs)

    print(f"\nTotal: {total} jobs from {len(slugs)} companies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Greenhouse ATS job boards")
    parser.add_argument(
        "--companies", nargs="*",
        default=list(COMPANIES.keys()),
        help=f"Company slugs to scrape. Available: {', '.join(COMPANIES.keys())}",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data"),
        help="Directory for output files (one CSV per company)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output_dir))
