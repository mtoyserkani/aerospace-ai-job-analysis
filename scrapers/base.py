"""
base.py — Shared schema, rate limiting, and normalization for all scrapers.

All scrapers produce the same normalized output. The 16-field schema is locked.
Field order: company → title → job_id → location → country → salary → remote →
             seniority → us_citizenship_required → security_clearance →
             relocation_assistance → source_platform → date_posted → scraped_at →
             apply_url → description_text
"""

import asyncio
import csv
import hashlib
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class Job:
    # Field order matches master schema (locked 2026-06-06)
    company: str
    title: str
    job_id: str
    location: str
    country: str
    salary: str                        # Regex-extracted from description_text
    remote: str                        # "remote" | "hybrid" | "onsite" | "unknown"
    seniority: str                     # "Senior" | "Mid" | "Junior" | "Lead" | "Principal" | "Manager" | "Director" | ""
    us_citizenship_required: str       # "yes" | "us_person" | "no" | "unknown"
    security_clearance: str            # "none" | "Secret" | "Top Secret" | "TS/SCI" | "TS/SCI + Poly" | "unknown"
    relocation_assistance: str         # "yes" | "no" | "unknown"
    source_platform: str               # "greenhouse" | "lever" | "workday" | "icims" | "talentbrew" | "brassring" | "taleo" | "successfactors"
    date_posted: str                   # ISO "YYYY-MM-DD" or "N/A"
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    apply_url: str = ""
    description_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def stable_id(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]


FIELDNAMES = [
    "company", "title", "job_id", "location", "country", "salary",
    "remote", "seniority", "us_citizenship_required", "security_clearance",
    "relocation_assistance", "source_platform", "date_posted", "scraped_at",
    "apply_url", "description_text",
]


# ---------------------------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------------------------

def extract_salary(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r'\$[\d,]+(?:\.\d+)?\s*[-\u2013\u2014]\s*\$[\d,]+(?:\.\d+)?(?:\s*/\s*(?:year|yr|hour|hr))?',
        r'\$[\d,]+(?:\.\d+)?\s*/\s*(?:hour|hr|year|yr)',
    ]
    matches = []
    seen = set()
    for pat in patterns:
        for m in re.findall(pat, text, re.IGNORECASE):
            clean = re.sub(r'\s+', ' ', m.strip())
            if clean not in seen:
                seen.add(clean)
                matches.append(clean)
    return "; ".join(matches)


def extract_citizenship(text: str) -> str:
    if not text:
        return "unknown"
    t = text.lower()
    if "u.s. government requires u.s. citizenship" in t or "must be a u.s. citizen" in t:
        return "yes"
    if "u.s. person as defined by 22 c.f.r" in t or "itar" in t:
        return "us_person"
    if "does not require u.s. citizenship" in t:
        return "no"
    if "u.s. citizen" in t or "united states citizen" in t:
        return "yes"
    return "unknown"


def extract_clearance(text: str) -> str:
    if not text:
        return "unknown"
    t = text.lower()
    if "does not require a security clearance" in t:
        return "none"
    if "ts/sci" in t and "poly" in t:
        return "TS/SCI + Poly"
    if "ts/sci" in t:
        return "TS/SCI"
    if "top secret" in t:
        return "Top Secret"
    if "secret clearance" in t or "secret-level" in t or " secret " in t:
        return "Secret"
    return "unknown"


def extract_relocation(text: str) -> str:
    if not text:
        return "unknown"
    t = text.lower()
    if "relocation will be provided" in t or "offers relocation" in t or "relocation assistance is available" in t:
        return "yes"
    if "not a negotiable benefit" in t or "must live in the immediate area" in t or "relocation is not" in t:
        return "no"
    if "relocation" in t:
        return "yes"
    return "unknown"


def infer_seniority(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ["director", "vp ", "vice president", "head of", "chief"]):
        return "Director"
    if any(k in t for k in ["principal", "staff ", "architect"]):
        return "Principal"
    if any(k in t for k in ["lead ", "lead,", "team lead"]):
        return "Lead"
    if any(k in t for k in ["manager", "mgr"]):
        return "Manager"
    if any(k in t for k in ["senior", "sr.", "sr "]):
        return "Senior"
    if any(k in t for k in ["junior", "jr.", "jr ", "entry level", "associate", "intern"]):
        return "Junior"
    return "Mid"


def infer_remote(location: str, description: str = "") -> str:
    text = (location + " " + description).lower()
    if any(k in text for k in ["remote", "work from home", "wfh"]):
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    return "onsite"


def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", " bc", " ab", "ontario", "toronto", "montreal", "quebec"]):
        return "Canada"
    if any(k in loc for k in ["uk", "london", "england", "ireland", "dublin"]):
        return "United Kingdom"
    if any(k in loc for k in ["germany", "münchen", "berlin", "france", "toulouse"]):
        return "Europe"
    if any(k in loc for k in ["india", "bangalore", "chennai"]):
        return "India"
    if any(k in loc for k in ["australia", "sydney", "melbourne"]):
        return "Australia"
    return "United States of America"


# ---------------------------------------------------------------------------
# Sample check — runs after first 20 jobs, before full scrape continues
# ---------------------------------------------------------------------------

def sample_check(jobs: list, company: str, platform: str) -> bool:
    """
    Checks first 20 jobs for data quality issues.
    Returns True if safe to continue, False if scrape should abort.
    Prints a clear report so the user knows what's happening.
    """
    if not jobs:
        print(f"\n  ⚠️  SAMPLE CHECK FAILED [{company}]")
        print(f"  No jobs collected at all. Check the scraper config.")
        return False

    sample = jobs[:20]
    n = len(sample)

    has_title     = sum(1 for j in sample if j.title and len(j.title) > 2)
    has_desc      = sum(1 for j in sample if j.description_text and len(j.description_text) > 50)
    has_url       = sum(1 for j in sample if j.apply_url and j.apply_url.startswith("http"))
    unique_ids    = len(set(j.job_id for j in sample))

    title_pct = int(100 * has_title / n)
    desc_pct  = int(100 * has_desc / n)
    url_pct   = int(100 * has_url / n)

    print(f"\n  {'='*50}")
    print(f"  SAMPLE CHECK — {company} ({platform}) — first {n} jobs")
    print(f"  {'='*50}")
    print(f"  Titles:       {has_title}/{n} ({title_pct}%)  {'✅' if title_pct == 100 else '❌ PROBLEM'}")
    print(f"  Descriptions: {has_desc}/{n} ({desc_pct}%)  {'✅' if desc_pct > 50 else '⚠️  low — keyword analysis will miss these jobs' if desc_pct > 0 else '❌ empty — enrichment not working'}")
    print(f"  Apply URLs:   {has_url}/{n} ({url_pct}%)  {'✅' if url_pct > 90 else '❌ PROBLEM — bad URL format'}")
    print(f"  Unique IDs:   {unique_ids}/{n}  {'✅' if unique_ids == n else '❌ DUPLICATES — pagination may be broken'}")

    # Hard failures — abort
    if title_pct < 50:
        print(f"\n  ❌ ABORTING — fewer than half the jobs have titles.")
        print(f"  The job card parser is not matching this company's HTML structure.")
        print(f"  Run: python3 scrapers/dom_inspector.py --url <careers_url> --intercept-api")
        return False

    if url_pct < 50:
        print(f"\n  ❌ ABORTING — fewer than half the jobs have valid apply URLs.")
        print(f"  The URL builder is producing bad links. Check the tenant/instance config.")
        return False

    if unique_ids < n * 0.8:
        print(f"\n  ❌ ABORTING — too many duplicate job IDs. Pagination is broken.")
        return False

    # Warnings — continue but inform
    if desc_pct == 0:
        print(f"\n  ⚠️  No descriptions found. This is OK if enrichment runs separately.")
        print(f"  Keyword analysis will be limited to job titles only for this company.")

    print(f"  {'='*50}")
    print(f"  ✅ Sample looks good — continuing full scrape...\n")
    return True


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, calls_per_minute: int = 30):
        self.min_interval = 60.0 / calls_per_minute
        self._last_call = 0.0

    async def wait(self):
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_jobs(jobs: list, output_path: Path) -> None:
    """Write jobs to CSV. Overwrites if exists."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            writer.writerow(job.to_dict())
    print(f"  Saved {len(jobs)} jobs → {output_path}")
