"""
base.py — Shared schema, rate limiting, and normalization for all scrapers.

All scrapers produce the same normalized output. Feed them into combine_output()
to get a single CSV matching the schema in data/schema.md.
"""

import asyncio
import csv
import hashlib
import json
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class Job:
    job_id: str                        # Stable identifier (hashed from URL if no native ID)
    title: str
    company: str
    location: str
    country: str
    remote: str                        # "remote" | "hybrid" | "onsite" | "unknown"
    apply_url: str
    description_text: str
    seniority: str                     # "senior" | "mid" | "junior" | "lead" | "director" | "unknown"
    salary: str                        # Raw salary string or empty
    source_platform: str               # "greenhouse" | "lever" | "workday" | "phenom" | etc.
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def stable_id(url: str) -> str:
        """Hash a URL into a short stable ID when no native job ID exists."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]


FIELDNAMES = [
    "job_id", "title", "company", "location", "country", "remote",
    "apply_url", "description_text", "seniority", "salary",
    "source_platform", "scraped_at",
]


# ---------------------------------------------------------------------------
# Seniority inference
# ---------------------------------------------------------------------------

SENIORITY_PATTERNS = {
    "director":  ["director", "vp ", "vice president", "head of", "chief"],
    "lead":      ["lead ", "principal ", "staff ", "architect"],
    "senior":    ["senior", "sr.", "sr "],
    "junior":    ["junior", "jr.", "jr ", "entry level", "associate", "intern"],
    "mid":       [],  # default if none of the above match
}


def infer_seniority(title: str) -> str:
    t = title.lower()
    for level, keywords in SENIORITY_PATTERNS.items():
        if any(k in t for k in keywords):
            return level
    return "mid"


# ---------------------------------------------------------------------------
# Remote inference
# ---------------------------------------------------------------------------

REMOTE_KEYWORDS = ["remote", "work from home", "wfh", "distributed"]
HYBRID_KEYWORDS = ["hybrid"]


def infer_remote(location: str, description: str = "") -> str:
    text = (location + " " + description).lower()
    if any(k in text for k in REMOTE_KEYWORDS):
        return "remote"
    if any(k in text for k in HYBRID_KEYWORDS):
        return "hybrid"
    return "onsite"


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple token-bucket rate limiter. Default: 30 requests/minute."""

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

def save_jobs(jobs: list[Job], output_path: Path) -> None:
    """Append jobs to a CSV. Creates the file with headers if it doesn't exist."""
    output_path = Path(output_path)
    write_header = not output_path.exists()
    with output_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for job in jobs:
            writer.writerow(job.to_dict())
    print(f"  Saved {len(jobs)} jobs → {output_path}")


def combine_outputs(input_paths: list[Path], output_path: Path) -> None:
    """Merge multiple scraper output CSVs into one deduplicated file."""
    seen_ids = set()
    all_jobs = []
    for path in input_paths:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                job_id = row.get("job_id", "")
                if job_id not in seen_ids:
                    seen_ids.add(job_id)
                    all_jobs.append(row)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_jobs)
    print(f"Combined {len(all_jobs)} unique jobs → {output_path}")
