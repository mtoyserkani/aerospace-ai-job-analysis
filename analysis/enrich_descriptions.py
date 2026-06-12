"""
enrich_descriptions.py — Fetch descriptions for jobs missing them.

Takes any CSV with apply_url column, visits each job page via Playwright,
extracts description text, and saves an enriched CSV.

Works for: TalentBrew (L3Harris, Boeing), Brassring (Lockheed Martin)

Usage:
    python3 analysis/enrich_descriptions.py --input data/l3harris_jobs.csv --output data/l3harris_enriched.csv
    python3 analysis/enrich_descriptions.py --input data/brassring_jobs.csv --output data/brassring_enriched.csv --batch-size 500
    python3 analysis/enrich_descriptions.py --input data/boeing_jobs.csv --output data/boeing_enriched.csv

Options:
    --batch-size N    Process N jobs then save checkpoint (default: 200)
    --skip-existing   Skip jobs that already have descriptions (default: True)
    --start-at N      Resume from row N (for crash recovery)
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scrapers"))
from base import RateLimiter

# Description selectors by platform (detected from apply_url domain)
SELECTORS = {
    "l3harris":    [
        "[class*='job-description']", "[class*='jobDescription']",
        "#job-description", ".description", "section[class*='description']",
        "[class*='ats-description']",
    ],
    "boeing":      [
        "[class*='job-description']", ".position-description",
        "#job-description", "[class*='description']",
    ],
    "lockheed":    [
        "#job-description", "[class*='job-description']",
        ".job-details", "[class*='description']",
    ],
    "default":     [
        "[class*='description']", "[class*='job-desc']",
        "#job-description", "section[class*='desc']",
        "div[class*='detail']", ".content",
    ],
}


def _detect_platform(url: str) -> str:
    url = url.lower()
    if "l3harris" in url:    return "l3harris"
    if "boeing" in url:      return "boeing"
    if "lockheed" in url:    return "lockheed"
    return "default"


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


async def fetch_description(page, url: str, platform: str) -> str:
    """Visit a job page and extract description text."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
    except Exception:
        return ""

    selectors = SELECTORS.get(platform, SELECTORS["default"])
    for selector in selectors:
        try:
            el = await page.query_selector(selector)
            if el:
                text = await el.inner_text()
                text = _clean(text)
                if len(text) > 100:
                    return text
        except Exception:
            continue
    return ""


async def enrich(
    input_path: Path,
    output_path: Path,
    batch_size: int,
    skip_existing: bool,
    start_at: int,
) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed: python3 -m playwright install chromium")
        return

    df = pd.read_csv(input_path, dtype=str, low_memory=False)
    print(f"Loaded {len(df):,} jobs from {input_path}")

    if "description_text" not in df.columns:
        df["description_text"] = ""

    df["description_text"] = df["description_text"].fillna("")

    # Identify jobs needing enrichment
    if skip_existing:
        needs_desc = df["description_text"].str.len() < 50
    else:
        needs_desc = pd.Series([True] * len(df))

    needs_desc = needs_desc & (df.index >= start_at)
    todo = df[needs_desc].index.tolist()
    print(f"Jobs needing description: {len(todo):,}")

    if not todo:
        print("All jobs already have descriptions.")
        df.to_csv(output_path, index=False)
        return

    # Estimate time
    est_minutes = len(todo) * 3 // 60
    print(f"Estimated time: ~{est_minutes} minutes at ~3s/job\n")

    limiter = RateLimiter(calls_per_minute=20)
    enriched = 0
    failed = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        for i, idx in enumerate(todo):
            url = str(df.at[idx, "apply_url"] or "")
            if not url or not url.startswith("http"):
                failed += 1
                continue

            platform = _detect_platform(url)
            await limiter.wait()

            desc = await fetch_description(page, url, platform)
            if desc:
                df.at[idx, "description_text"] = desc
                enriched += 1
            else:
                failed += 1

            if (i + 1) % 50 == 0:
                pct = 100 * (i + 1) // len(todo)
                print(f"  [{pct}%] {i+1}/{len(todo)} processed — {enriched} enriched, {failed} failed")

            # Checkpoint save every batch_size jobs
            if (i + 1) % batch_size == 0:
                df.to_csv(output_path, index=False)
                print(f"  Checkpoint saved → {output_path}")

        await browser.close()

    df.to_csv(output_path, index=False)
    print(f"\nDone: {enriched:,} enriched, {failed:,} failed")
    print(f"Saved {len(df):,} jobs → {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich job descriptions via Playwright")
    parser.add_argument("--input",         type=Path, required=True)
    parser.add_argument("--output",        type=Path, required=True)
    parser.add_argument("--batch-size",    type=int, default=200)
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--start-at",      type=int, default=0)
    args = parser.parse_args()
    asyncio.run(enrich(args.input, args.output, args.batch_size, args.skip_existing, args.start_at))
