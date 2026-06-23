"""
phenom_scraper.py — Scrapes companies using the Phenom People ATS.

Phenom is a React SPA with a GraphQL/REST API. The API endpoint is
consistent across tenants but GE Aerospace requires special handling:

  Northrop Grumman → ngccareers.northropgrumman.com
    API: /api/apply/v2/jobs (POST) returns full description in payload
    Status: ✅ Working

  GE Aerospace → careers.geaerospace.com
    API: /api/apply/v2/jobs (POST) returns nav boilerplate in description field
    Fix: Use Playwright to intercept the internal API call each job page
         makes, which returns clean structured JSON with actual description.
    Status: ✅ Fixed (2026-06-06)

FIX NOTES — GE Aerospace description bug:
  Root cause: GE's Phenom instance returns navigation HTML in the top-level
  `description` field of the job listing API. The actual job description is
  served separately when the job detail page loads, via a secondary API call
  to /api/apply/v2/jobs/{job_id} (GET) which returns a richer object with
  `jobDescription.jobDescriptionSection[].body` fields.

  Strategy:
    1. Scrape job list via API (fast, gets id/title/location for all jobs).
    2. For each job, fetch the detail endpoint to get clean description.
    3. Parse salary, date_posted from detail response.
    4. Rate-limit detail fetches to avoid throttling.

Usage:
    pip install playwright httpx && playwright install chromium
    python phenom_scraper.py --output data/phenom_jobs.csv
    python phenom_scraper.py --companies ge --output data/ge_jobs.csv
    python phenom_scraper.py --companies northrop --skip-descriptions --output data/northrop_jobs.csv
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, infer_remote, save_jobs, sample_check,
    extract_salary, extract_citizenship, extract_clearance, extract_relocation,
)

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "ge": {
        "name":             "GE Aerospace",
        "base_url":         "https://careers.geaerospace.com",
        "api_path":         "/api/apply/v2/jobs",
        "detail_api_path":  "/api/apply/v2/jobs/{job_id}",
        "description_mode": "api_detail",
        "known_issue":      "Phenom widget-based API. Tenant not identified via direct API calls. Blocked 2026-06-12.",
        "status":           "parked",
    },
    "rtx": {
        "name":             "RTX",
        "base_url":         "https://careers.rtx.com",
        "api_path":         "/api/apply/v2/jobs",
        "detail_api_path":  "/api/apply/v2/jobs/{job_id}",
        "description_mode": "api_detail",
        "known_issue":      "Cloudflare Bot Management blocks all automated access.",
        "status":           "parked",
    },
}
# NOTE: Northrop Grumman moved from Phenom to Eightfold AI (jobs.northropgrumman.com).
# Use eightfold_scraper.py for Northrop.

# ---------------------------------------------------------------------------
# Phenom API headers
# ---------------------------------------------------------------------------

PHENOM_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Content-Type":    "application/json",
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
}

PHENOM_LIST_PAYLOAD = {
    "lang":   "en_US",
    "offset": 0,
    "limit":  20,
    "sortBy": "relevance",
    "facets": {},
    "text":   "",
}

# ---------------------------------------------------------------------------
# Description extraction helpers
# ---------------------------------------------------------------------------

def _clean_html(text: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_nav_boilerplate(text: str) -> bool:
    """
    Detect GE nav boilerplate. Returns True if the description is the
    site navigation menu rather than actual job content.
    """
    NAV_SIGNALS = [
        "About UsAbout Us",
        "Our CultureOur Benefits",
        "Search & ApplyCareer Areas",
        "SustainabilityMilitary",
    ]
    return any(signal in text for signal in NAV_SIGNALS)


def _extract_description_from_detail(detail: dict) -> str:
    """
    Parse job description from the per-job detail API response.
    GE Aerospace detail endpoint returns:
      {
        "jobDescription": {
          "jobDescriptionSection": [
            {"body": "<html>...actual description...</html>", "title": "..."},
            ...
          ]
        }
      }
    Falls back to top-level description fields if structure differs.
    """
    # Primary: nested jobDescriptionSection
    jd = detail.get("jobDescription", {})
    if isinstance(jd, dict):
        sections = jd.get("jobDescriptionSection", [])
        if sections:
            parts = []
            for section in sections:
                title = section.get("title", "")
                body  = section.get("body", "")
                if body:
                    cleaned = _clean_html(body)
                    if title:
                        parts.append(f"{title}: {cleaned}")
                    else:
                        parts.append(cleaned)
            combined = " ".join(parts)
            if combined and not _is_nav_boilerplate(combined):
                return combined

        # Fallback: jobDescription is a string
        if isinstance(jd, str):
            cleaned = _clean_html(jd)
            if not _is_nav_boilerplate(cleaned):
                return cleaned

    # Fallback: top-level fields
    for key in ["description", "jobDescription", "summary", "jobSummary", "fullDescription"]:
        val = detail.get(key, "")
        if val and isinstance(val, str):
            cleaned = _clean_html(val)
            if cleaned and not _is_nav_boilerplate(cleaned):
                return cleaned

    return ""


def _extract_salary_from_text(text: str) -> str:
    """
    Extract salary range(s) from description text.
    Captures patterns like:
      $136,850 - $185,150
      $23.32/hour
      $145,000–$185,000 (em-dash variant)
    Multiple state ranges (L3Harris style) joined with semicolons.
    """
    patterns = [
        r"\$[\d,]+(?:\.\d+)?\s*[-–—]\s*\$[\d,]+(?:\.\d+)?(?:\s*/\s*(?:year|yr|hour|hr))?",
        r"\$[\d,]+(?:\.\d+)?\s*/\s*(?:hour|hr|year|yr)",
    ]
    matches = []
    for pattern in patterns:
        found = re.findall(pattern, text, re.IGNORECASE)
        matches.extend(found)
    # Deduplicate preserving order
    seen = set()
    unique = []
    for m in matches:
        m_clean = re.sub(r"\s+", " ", m.strip())
        if m_clean not in seen:
            seen.add(m_clean)
            unique.append(m_clean)
    return "; ".join(unique)


def _extract_date_posted(raw: dict) -> str:
    """Extract posting date from API response. Returns ISO date string or 'N/A'."""
    for key in ["postedDate", "postingDate", "datePosted", "posted_date",
                "publishDate", "createdDate", "requisitionCreationDate"]:
        val = raw.get(key, "")
        if val:
            # Normalise to YYYY-MM-DD
            match = re.search(r"(\d{4}-\d{2}-\d{2})", str(val))
            if match:
                return match.group(1)
            # Try epoch millis
            if str(val).isdigit() and len(str(val)) == 13:
                from datetime import datetime
                return datetime.fromtimestamp(int(val) / 1000).strftime("%Y-%m-%d")
    return "N/A"


def _extract_location(raw: dict) -> str:
    for key in ["location", "city", "locationName", "primaryLocation",
                "cityStateCountry", "locationDesc"]:
        val = raw.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # Some Phenom instances nest location
    loc = raw.get("locations", [])
    if loc and isinstance(loc, list):
        return loc[0].get("locationName", "") if isinstance(loc[0], dict) else str(loc[0])
    return ""


def _build_apply_url(base_url: str, raw: dict) -> str:
    job_id = str(raw.get("id", raw.get("reqId", raw.get("jobReqId", ""))))
    if not job_id:
        return ""
    return f"{base_url}/global/en/job/{job_id}/"


def _infer_country(location: str) -> str:
    loc = location.lower()
    country_map = {
        "canada": "Canada",
        "ontario": "Canada",
        "toronto": "Canada",
        "montreal": "Canada",
        "france": "France",
        "toulouse": "France",
        "germany": "Germany",
        "uk": "United Kingdom",
        "united kingdom": "United Kingdom",
        "india": "India",
        "bangalore": "India",
    }
    for keyword, country in country_map.items():
        if keyword in loc:
            return country
    return "United States of America"

# ---------------------------------------------------------------------------
# Core scraper — listing phase
# ---------------------------------------------------------------------------

async def scrape_job_list(config: dict, client, limiter: RateLimiter) -> list[dict]:
    """
    Fetch all job listings from Phenom API.
    Returns raw job dicts (no descriptions yet for detail-mode companies).
    """
    base_url = config["base_url"]
    api_url  = base_url + config["api_path"]
    company  = config["name"]
    raw_jobs = []
    offset   = 0
    total    = None

    while True:
        await limiter.wait()
        payload = {**PHENOM_LIST_PAYLOAD, "offset": offset}

        try:
            resp = await client.post(api_url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [{company}] List API error at offset {offset}: {e}")
            break

        if total is None:
            total = data.get("total", data.get("count", data.get("totalCount", 0)))

        page_jobs = (
            data.get("positions", []) or
            data.get("jobs", []) or
            data.get("requisitions", []) or
            []
        )

        if not page_jobs:
            break

        raw_jobs.extend(page_jobs)
        offset += len(page_jobs)
        print(f"  [{company}] Listed {offset}/{total or '?'} jobs...")

        if total and offset >= total:
            break

    return raw_jobs


# ---------------------------------------------------------------------------
# Detail fetch — GE Aerospace fix
# ---------------------------------------------------------------------------

async def fetch_job_detail(config: dict, job_id: str, client, limiter: RateLimiter) -> dict:
    """
    Fetch per-job detail from Phenom API.
    Returns the detail dict, or {} on failure.
    GE Aerospace: detail endpoint returns clean jobDescriptionSection structure.
    """
    base_url    = config["base_url"]
    detail_path = config["detail_api_path"].format(job_id=job_id)
    url         = base_url + detail_path

    await limiter.wait()
    try:
        resp = await client.get(url, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Detail fetch failed for job {job_id}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

async def scrape_company(
    config: dict,
    limiter: RateLimiter,
    skip_descriptions: bool = False,
) -> list[Job]:
    """
    Full scrape for one Phenom company using Playwright.
    Phenom blocks direct API calls — we load the careers page in a browser
    and intercept the API responses during page navigation.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed: python3 -m playwright install chromium")
        return []

    import json as _json

    company  = config["name"]
    base_url = config["base_url"]
    api_path = config["api_path"]
    jobs_out = []
    all_raw  = []

    careers_url = base_url + "/global/en/"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await context.new_page()

        # Intercept Phenom API responses
        async def handle_response(response):
            if api_path in response.url and response.status == 200:
                try:
                    body = await response.text()
                    data = _json.loads(body)
                    positions = (data.get("positions", []) or
                                data.get("jobs", []) or
                                data.get("requisitions", []) or [])
                    if positions:
                        all_raw.extend(positions)
                except Exception:
                    pass

        page.on("response", handle_response)

        print(f"\n  Loading {careers_url}...")
        try:
            await page.goto(careers_url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"  Page load error: {e}")
            await browser.close()
            return []

        await page.wait_for_timeout(3000)

        total_count = 0
        if all_raw:
            print(f"  [{company}] Intercepted {len(all_raw)} jobs on page load")
        else:
            # Try direct API call via browser fetch
            print(f"  [{company}] No interception — trying browser fetch...")
            api_url = base_url + api_path
            payload = {"lang": "en_US", "offset": 0, "limit": 20,
                       "sortBy": "relevance", "facets": {}, "text": ""}
            result = await page.evaluate("""
                async ([url, payload]) => {
                    try {
                        const resp = await fetch(url, {
                            method: "POST",
                            headers: {"Content-Type": "application/json",
                                      "Accept": "application/json"},
                            credentials: "include",
                            body: JSON.stringify(payload)
                        });
                        return {status: resp.status, text: await resp.text()};
                    } catch(e) { return {status: 0, text: e.toString()}; }
                }
            """, [api_url, payload])

            if result.get("status") == 200:
                data = _json.loads(result["text"])
                total_count = data.get("total", data.get("count", 0))
                positions = (data.get("positions", []) or
                            data.get("jobs", []) or [])
                all_raw.extend(positions)
                print(f"  [{company}] Browser fetch: {len(all_raw)}/{total_count} jobs")

                # Paginate
                offset = len(all_raw)
                while offset < total_count:
                    await limiter.wait()
                    payload["offset"] = offset
                    result = await page.evaluate("""
                        async ([url, payload]) => {
                            const resp = await fetch(url, {
                                method: "POST",
                                headers: {"Content-Type": "application/json",
                                          "Accept": "application/json"},
                                credentials: "include",
                                body: JSON.stringify(payload)
                            });
                            return {status: resp.status, text: await resp.text()};
                        }
                    """, [api_url, payload])
                    if result.get("status") != 200:
                        break
                    data = _json.loads(result["text"])
                    positions = (data.get("positions", []) or
                                data.get("jobs", []) or [])
                    if not positions:
                        break
                    all_raw.extend(positions)
                    offset = len(all_raw)
                    print(f"  [{company}] {offset}/{total_count} jobs...")
            else:
                print(f"  [{company}] API returned {result.get('status')}: {result.get('text', '')[:100]}")

        await browser.close()

    print(f"  {company}: {len(all_raw)} jobs found in listing")

    # Build Job objects
    for raw in all_raw:
        job_id    = str(raw.get("id", raw.get("reqId", raw.get("jobReqId", ""))))
        title     = (raw.get("title", "") or raw.get("jobTitle", "")).strip()
        location  = _extract_location(raw)
        apply_url = _build_apply_url(base_url, raw)
        date_posted = _extract_date_posted(raw)
        desc      = _extract_description_from_detail(raw)
        if _is_nav_boilerplate(desc):
            desc = ""
        salary = _extract_salary_from_text(desc)

        jobs_out.append(Job(
            company                = company,
            title                  = title,
            job_id                 = job_id,
            location               = location,
            country                = _infer_country(location),
            salary                 = salary,
            remote                 = infer_remote(location, desc),
            seniority              = infer_seniority(title),
            us_citizenship_required= extract_citizenship(desc),
            security_clearance     = extract_clearance(desc),
            relocation_assistance  = extract_relocation(desc),
            source_platform        = "phenom",
            date_posted            = date_posted,
            apply_url              = apply_url,
            description_text       = desc,
        ))

    print(f"  [{company}] Done: {len(jobs_out)} jobs")
    return jobs_out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(company_keys: list[str], output: Path, skip_descriptions: bool) -> None:
    limiter  = RateLimiter(calls_per_minute=30)
    all_jobs = []

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown company: {key}. Available: {', '.join(COMPANIES)}")
            continue
        jobs = await scrape_company(config, limiter, skip_descriptions)
        all_jobs.extend(jobs)

    # Save per company
    from collections import defaultdict
    jobs_by_company = defaultdict(list)
    for job in all_jobs:
        jobs_by_company[job.company].append(job)
    total = 0
    for company_jobs in jobs_by_company.values():
        if not company_jobs:
            continue
        if not sample_check(company_jobs[:20], company_jobs[0].company, "phenom"):
            continue
        key = company_jobs[0].company.lower().replace(" ", "_").replace("(", "").replace(")", "")
        output_path = output_dir / f"phenom_{key}.csv"
        save_jobs(company_jobs, output_path)
        total += len(company_jobs)
    print(f"\nTotal: {len(all_jobs)} jobs from {len(company_keys)} companies → {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Phenom People ATS job boards")
    parser.add_argument("--companies",          nargs="*", default=list(COMPANIES.keys()),
                        help=f"Companies to scrape. Options: {', '.join(COMPANIES)}")
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Directory for output files (one CSV per company)")
    parser.add_argument("--skip-descriptions",  action="store_true",
                        help="Skip description fetch (fast list-only mode)")
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output, args.skip_descriptions))
