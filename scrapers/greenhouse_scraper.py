"""
greenhouse_scraper.py — Scrapes companies using the Greenhouse ATS.

Greenhouse exposes a public jobs board API. No authentication required.
This is the cleanest scraper in the set — no Playwright needed.

Companies in this dataset using Greenhouse:
  - Relativity Space   → boards.greenhouse.io/relativityspace
  - Rocket Lab         → boards.greenhouse.io/rocketlab
  - Planet Labs        → boards.greenhouse.io/planetlabs
  - SpaceX             → boards.greenhouse.io/spacex

API reference: https://developers.greenhouse.io/job-board.html
Rate limit: ~60 req/min before throttling. We use 30 to be safe.

Usage:
    python greenhouse_scraper.py --output data/greenhouse_jobs.csv
    python greenhouse_scraper.py --companies relativityspace rocketlab
"""

import argparse
import asyncio
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from base import Job, RateLimiter, infer_seniority, infer_remote, save_jobs

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "relativityspace": "Relativity Space",
    "rocketlab":       "Rocket Lab",
    "planetlabs":      "Planet Labs",
    "spacex":          "SpaceX",
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

    data = resp.json()
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
        metadata  = raw.get("metadata", []) or []
        desc      = strip_html(content)

        # Salary from metadata
        salary    = _extract_salary(metadata)

        jobs.append(Job(
            job_id          = job_id,
            title           = title,
            company         = company_name,
            location        = location,
            country         = country,
            remote          = infer_remote(location, desc),
            apply_url       = apply_url,
            description_text= desc,
            seniority       = infer_seniority(title),
            salary          = salary,
            source_platform = "greenhouse",
        ))

    return jobs


def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["uk", "united kingdom", "london", "england"]):
        return "GB"
    if any(k in loc for k in ["canada", "ontario", "toronto", "vancouver", " bc", " ab"]):
        return "CA"
    if any(k in loc for k in ["germany", "münchen", "berlin", "hamburg"]):
        return "DE"
    return "US"  # Greenhouse companies here are US-HQ'd


def _extract_salary(metadata: list) -> str:
    for item in metadata:
        name = (item.get("name") or "").lower()
        if "salary" in name or "compensation" in name or "pay" in name:
            val = item.get("value")
            if val:
                return str(val)
    return ""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(slugs: list[str], output: Path) -> None:
    limiter = RateLimiter(calls_per_minute=30)
    all_jobs: list[Job] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "aerospace-job-research/1.0 (github.com/mtoyserkani/aerospace-ai-job-analysis)"},
        follow_redirects=True,
    ) as client:
        for slug in slugs:
            company_name = COMPANIES.get(slug, slug.title())
            print(f"\nScraping {company_name} ({slug})...")
            jobs = await scrape_company(slug, company_name, client, limiter)
            all_jobs.extend(jobs)

    save_jobs(all_jobs, output)
    print(f"\nTotal: {len(all_jobs)} jobs from {len(slugs)} companies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Greenhouse ATS job boards")
    parser.add_argument(
        "--companies", nargs="*",
        default=list(COMPANIES.keys()),
        help=f"Company slugs to scrape. Available: {', '.join(COMPANIES.keys())}",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/greenhouse_jobs.csv"),
        help="Output CSV path",
    )
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output))
