"""
lever_scraper.py — Scrapes companies using the Lever ATS.

Lever exposes a public postings API. No authentication required.
Clean JSON response, fast, no JavaScript rendering needed.

Companies in this dataset using Lever:
  - Hermeus  → jobs.lever.co/hermeus

API: https://api.lever.co/v0/postings/{company}?mode=json
Rate limit: permissive. We use 30 req/min anyway.

Usage:
    python lever_scraper.py --output data/lever_jobs.csv
    python lever_scraper.py --companies hermeus
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from base import Job, RateLimiter, infer_seniority, infer_remote, save_jobs

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "hermeus": "Hermeus",
}

API_URL = "https://api.lever.co/v0/postings/{slug}?mode=json&limit=250"

# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

async def scrape_company(
    slug: str,
    company_name: str,
    client: httpx.AsyncClient,
    limiter: RateLimiter,
) -> list[Job]:
    await limiter.wait()
    try:
        resp = await client.get(API_URL.format(slug=slug), timeout=30)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"  [{slug}] HTTP {e.response.status_code} — skipping")
        return []
    except httpx.RequestError as e:
        print(f"  [{slug}] Request error: {e} — skipping")
        return []

    raw_jobs = resp.json()
    print(f"  [{slug}] Found {len(raw_jobs)} jobs")

    jobs = []
    for raw in raw_jobs:
        job_id    = raw.get("id", "")
        title     = raw.get("text", "").strip()
        apply_url = raw.get("hostedUrl", raw.get("applyUrl", ""))

        # Location
        categories = raw.get("categories", {})
        location   = categories.get("location", "") or raw.get("workplaceType", "")
        commitment = categories.get("commitment", "")  # Full-time / Part-time
        country    = "US"  # Lever companies here are US-based

        # Description — Lever returns lists of text+content blocks
        desc = _extract_description(raw.get("descriptionBody", raw.get("description", "")))

        # Salary — rarely exposed in Lever public API
        salary = _extract_salary(raw.get("text", ""), desc)

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
            source_platform = "lever",
        ))

    return jobs


def _extract_description(raw: str | dict | list) -> str:
    """Lever description can be HTML string, dict, or list of blocks."""
    if isinstance(raw, str):
        return _strip_html(raw)
    if isinstance(raw, dict):
        return _strip_html(raw.get("html", raw.get("text", "")))
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                parts.append(_strip_html(block.get("content", block.get("text", ""))))
            elif isinstance(block, str):
                parts.append(_strip_html(block))
        return " ".join(parts)
    return ""


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s{2,}", " ", text).strip()


def _extract_salary(title: str, description: str) -> str:
    # Look for salary range patterns like $120,000 - $160,000 or $120K-$160K
    pattern = r"\$[\d,]+(?:K|k)?\s*(?:–|-|to)\s*\$[\d,]+(?:K|k)?"
    match = re.search(pattern, description)
    return match.group(0) if match else ""


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
    parser = argparse.ArgumentParser(description="Scrape Lever ATS job boards")
    parser.add_argument(
        "--companies", nargs="*",
        default=list(COMPANIES.keys()),
        help=f"Company slugs. Available: {', '.join(COMPANIES.keys())}",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/lever_jobs.csv"),
    )
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output))
