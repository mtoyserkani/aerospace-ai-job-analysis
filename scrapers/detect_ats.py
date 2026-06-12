"""
detect_ats.py — Batch ATS detection for all aerospace companies.

For each company, this script:
  1. Finds the careers page URL (tries common patterns)
  2. Runs dom_inspector-style detection to identify the ATS platform
  3. Extracts the job board slug where applicable
  4. Outputs a CSV: company, careers_url, ats_platform, slug, status

Platforms detected:
  Workday    — myworkdayjobs.com in links or source
  Greenhouse — boards.greenhouse.io or greenhouse.io in API calls
  Lever      — jobs.lever.co in links
  iCIMS      — icims.com in links or source
  Phenom     — phenom.com or phenompeople.com in source
  TalentBrew — talentbrew.com or kenexa in source
  SmartRecruiters — smartrecruiters.com
  SAP SuccessFactors — successfactors.com
  Oracle Taleo — taleo.net
  ADP        — adp.com
  BrassRing  — brassring.com
  Generic    — careers page found but ATS unidentified
  Blocked    — careers page returned 403/blocked
  NotFound   — no careers page found

Usage:
    python3 scrapers/detect_ats.py --output data/ats_map.csv
    python3 scrapers/detect_ats.py --company "Lockheed Martin" --output data/ats_map.csv
"""

import argparse
import asyncio
import csv
import re
import sys
from pathlib import Path
from datetime import datetime

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("playwright not installed: python3 -m playwright install chromium")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Company registry — all 70 companies from research list
# ---------------------------------------------------------------------------

COMPANIES = [
    # Prime
    {"name": "Boeing",                      "website": "boeing.com",                    "tier": "Prime"},
    {"name": "Lockheed Martin",             "website": "lockheedmartin.com",            "tier": "Prime"},
    {"name": "Northrop Grumman",            "website": "northropgrumman.com",           "tier": "Prime"},
    {"name": "RTX",                         "website": "rtx.com",                       "tier": "Prime"},
    {"name": "General Dynamics",            "website": "gd.com",                        "tier": "Prime"},
    {"name": "L3Harris",                    "website": "l3harris.com",                  "tier": "Prime"},
    {"name": "Textron",                     "website": "textron.com",                   "tier": "Prime"},
    {"name": "Airbus (North America)",      "website": "airbus.com",                    "tier": "Prime"},
    {"name": "Bombardier",                  "website": "bombardier.com",                "tier": "Prime"},
    {"name": "CAE",                         "website": "cae.com",                       "tier": "Prime"},
    {"name": "Bell (Textron)",              "website": "bellflight.com",                "tier": "Prime"},
    # Tier 1
    {"name": "Sikorsky (Lockheed)",         "website": "sikorsky.com",                  "tier": "Tier 1"},
    {"name": "Collins Aerospace (RTX)",     "website": "collinsaerospace.com",          "tier": "Tier 1"},
    {"name": "Pratt & Whitney (RTX)",       "website": "prattwhitney.com",              "tier": "Tier 1"},
    {"name": "GE Aerospace",                "website": "geaerospace.com",               "tier": "Tier 1"},
    {"name": "Honeywell Aerospace",         "website": "honeywell.com",                 "tier": "Tier 1"},
    {"name": "Parker Hannifin",             "website": "parker.com",                    "tier": "Tier 1"},
    {"name": "TransDigm Group",             "website": "transdigm.com",                 "tier": "Tier 1"},
    {"name": "Moog Inc",                    "website": "moog.com",                      "tier": "Tier 1"},
    {"name": "Spirit AeroSystems",          "website": "spiritaero.com",                "tier": "Tier 1"},
    {"name": "Ducommun",                    "website": "ducommun.com",                  "tier": "Tier 1"},
    {"name": "Triumph Group",               "website": "triumphgroup.com",              "tier": "Tier 1"},
    {"name": "Kaman Aerospace",             "website": "kaman.com",                     "tier": "Tier 1"},
    {"name": "Curtiss-Wright",              "website": "curtisswright.com",             "tier": "Tier 1"},
    {"name": "Woodward",                    "website": "woodward.com",                  "tier": "Tier 1"},
    {"name": "Mercury Systems",             "website": "mercury.com",                   "tier": "Tier 1"},
    {"name": "Astronics Corporation",       "website": "astronics.com",                 "tier": "Tier 1"},
    {"name": "Crane Aerospace",             "website": "craneae.com",                   "tier": "Tier 1"},
    {"name": "HEICO Corporation",           "website": "heico.com",                     "tier": "Tier 1"},
    {"name": "Safran (North America)",      "website": "safran-group.com",              "tier": "Tier 1"},
    {"name": "Leonardo DRS",                "website": "leonardodrs.com",               "tier": "Tier 1"},
    {"name": "Elbit Systems of America",    "website": "elbitsystems-us.com",           "tier": "Tier 1"},
    # MRO
    {"name": "AAR Corp",                    "website": "aarcorp.com",                   "tier": "MRO"},
    {"name": "StandardAero",                "website": "standardaero.com",              "tier": "MRO"},
    {"name": "Duncan Aviation",             "website": "duncanaviation.aero",           "tier": "MRO"},
    {"name": "West Star Aviation",          "website": "weststaraviation.com",          "tier": "MRO"},
    {"name": "HAECO Americas",              "website": "haeco.com",                     "tier": "MRO"},
    {"name": "Pemco Aviation Group",        "website": "pemcoair.com",                  "tier": "MRO"},
    {"name": "Nordam Group",                "website": "nordam.com",                    "tier": "MRO"},
    {"name": "Lufthansa Technik Americas",  "website": "lufthansa-technik.com",         "tier": "MRO"},
    {"name": "Delta TechOps",               "website": "delta.com",                     "tier": "MRO"},
    {"name": "American Airlines Tech Ops",  "website": "aa.com",                        "tier": "MRO"},
    {"name": "Air Canada Technical Services","website": "aircanada.com",                "tier": "MRO"},
    {"name": "Chromalloy",                  "website": "chromalloy.com",                "tier": "MRO"},
    # Defense
    {"name": "Leidos",                      "website": "leidos.com",                    "tier": "Defense"},
    {"name": "Booz Allen Hamilton",         "website": "boozallen.com",                 "tier": "Defense"},
    {"name": "SAIC",                        "website": "saic.com",                      "tier": "Defense"},
    {"name": "Peraton",                     "website": "peraton.com",                   "tier": "Defense"},
    {"name": "Sierra Nevada Corporation",   "website": "sncorp.com",                    "tier": "Defense"},
    {"name": "Kratos Defense",              "website": "kratosdefense.com",             "tier": "Defense"},
    {"name": "Vectrus Technologies",        "website": "vectrus.com",                   "tier": "Defense"},
    {"name": "Jacobs Engineering",          "website": "jacobs.com",                    "tier": "Defense"},
    # Space
    {"name": "SpaceX",                      "website": "spacex.com",                    "tier": "Space"},
    {"name": "Blue Origin",                 "website": "blueorigin.com",                "tier": "Space"},
    {"name": "Rocket Lab",                  "website": "rocketlabusa.com",              "tier": "Space"},
    {"name": "Vantor (Maxar)",              "website": "vantor.com",                    "tier": "Space"},
    {"name": "Aerojet Rocketdyne (L3Harris)","website": "aerojetrocketdyne.com",        "tier": "Space"},
    {"name": "Planet Labs",                 "website": "planet.com",                    "tier": "Space"},
    # New Entrant
    {"name": "Joby Aviation",               "website": "jobyaviation.com",              "tier": "New Entrant"},
    {"name": "Archer Aviation",             "website": "archeraircraft.com",            "tier": "New Entrant"},
    {"name": "Wisk Aero",                   "website": "wisk.aero",                     "tier": "New Entrant"},
    {"name": "Beta Technologies",           "website": "beta.team",                     "tier": "New Entrant"},
    {"name": "Boom Supersonic",             "website": "boomsupersonic.com",            "tier": "New Entrant"},
    {"name": "Reliable Robotics",           "website": "reliable.ai",                   "tier": "New Entrant"},
    {"name": "Elroy Air",                   "website": "elroyair.com",                  "tier": "New Entrant"},
    {"name": "Zipline",                     "website": "flyzipline.com",                "tier": "New Entrant"},
    {"name": "Electra.aero",                "website": "electra.aero",                  "tier": "New Entrant"},
    {"name": "Heart Aerospace",             "website": "heartaerospace.com",            "tier": "New Entrant"},
    {"name": "Relativity Space",            "website": "relativityspace.com",           "tier": "New Entrant"},
    {"name": "Hermeus",                     "website": "hermeus.com",                   "tier": "New Entrant"},
    {"name": "Overair",                     "website": "overair.com",                   "tier": "New Entrant"},
]

# ---------------------------------------------------------------------------
# ATS detection signatures
# ---------------------------------------------------------------------------

ATS_SIGNATURES = [
    # Platform,        URL pattern,                      Source pattern
    ("workday",        r"myworkdayjobs\.com",            r"myworkdayjobs\.com|workday\.com"),
    ("greenhouse",     r"boards\.greenhouse\.io|boards-api\.greenhouse\.io", r"greenhouse\.io"),
    ("lever",          r"jobs\.lever\.co",               r"lever\.co"),
    ("icims",          r"\.icims\.com",                  r"icims\.com"),
    ("phenom",         r"phenompeople\.com|\.phenom\.",  r"phenompeople\.com|phenom\.com"),
    ("talentbrew",     r"talentbrew\.com",               r"talentbrew\.com|kenexa\.com"),
    ("smartrecruiters",r"smartrecruiters\.com",          r"smartrecruiters\.com"),
    ("successfactors", r"successfactors\.com|sap\.com",  r"successfactors\.com"),
    ("taleo",          r"taleo\.net",                    r"taleo\.net"),
    ("brassring",      r"brassring\.com",                r"brassring\.com"),
    ("adp",            r"adp\.com",                      r"adp\.com"),
    ("jobvite",        r"jobvite\.com",                  r"jobvite\.com"),
    ("jazz",           r"app\.jazz\.co",                 r"jazz\.co"),
    ("ashby",          r"jobs\.ashbyhq\.com",            r"ashbyhq\.com"),
]

CAREERS_URL_PATTERNS = [
    "https://www.{domain}/careers",
    "https://careers.{domain}",
    "https://jobs.{domain}",
    "https://www.{domain}/en/careers",
    "https://www.{domain}/about/careers",
    "https://www.{domain}/company/careers",
    "https://{domain}/careers",
]

# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def detect_ats_from_content(url: str, html: str, api_urls: list[str]) -> tuple[str, str]:
    """
    Returns (platform, slug) from page content and intercepted API calls.
    """
    all_text = html + " ".join(api_urls)

    for platform, url_pattern, src_pattern in ATS_SIGNATURES:
        # Check intercepted API URLs first (most reliable)
        for api_url in api_urls:
            if re.search(url_pattern, api_url, re.IGNORECASE):
                slug = _extract_slug(platform, api_url)
                return platform, slug
        # Check page source
        if re.search(src_pattern, all_text, re.IGNORECASE):
            # Try to extract slug from page source
            slug = _extract_slug_from_source(platform, html)
            return platform, slug

    return "unknown", ""


def _extract_slug(platform: str, url: str) -> str:
    """Extract board slug from an ATS API URL."""
    patterns = {
        "greenhouse":  r"boards(?:-api)?\.greenhouse\.io/v\d+/boards/([^/?\s]+)",
        "lever":       r"jobs\.lever\.co/([^/?\s]+)",
        "workday":     r"([^.]+)\.myworkdayjobs\.com",
        "icims":       r"([^.]+)\.icims\.com",
        "phenom":      r"(?:careers\.|jobs\.)([^.]+)\.",
    }
    pattern = patterns.get(platform, "")
    if pattern:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def _extract_slug_from_source(platform: str, html: str) -> str:
    """Extract slug from page HTML source."""
    patterns = {
        "greenhouse": r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([^/&\"'\s]+)",
        "lever":      r"jobs\.lever\.co/([^/&\"'\s]+)",
        "workday":    r"([a-zA-Z0-9-]+)\.myworkdayjobs\.com",
        "phenom":     r"careers\.([^.]+)\.",
    }
    pattern = patterns.get(platform, "")
    if pattern:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# Careers page finder
# ---------------------------------------------------------------------------

async def find_careers_url(page, domain: str) -> tuple[str, str]:
    """
    Try common careers URL patterns. Returns (url, status).
    """
    for pattern in CAREERS_URL_PATTERNS:
        url = pattern.format(domain=domain)
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if resp and resp.status < 400:
                final_url = page.url
                # Make sure we didn't just land on a generic homepage
                if domain.split(".")[0] in final_url or "career" in final_url or "job" in final_url:
                    return final_url, "ok"
        except Exception:
            continue
    return "", "not_found"


# ---------------------------------------------------------------------------
# Main detector
# ---------------------------------------------------------------------------

async def detect_company(company: dict, browser) -> dict:
    """Run ATS detection for one company."""
    name    = company["name"]
    domain  = company["website"]
    tier    = company["tier"]

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    page = await context.new_page()

    intercepted_urls = []

    def handle_request(request):
        url = request.url
        for platform, url_pattern, _ in ATS_SIGNATURES:
            if re.search(url_pattern, url, re.IGNORECASE):
                intercepted_urls.append(url)

    page.on("request", handle_request)

    result = {
        "company":      name,
        "tier":         tier,
        "website":      domain,
        "careers_url":  "",
        "ats_platform": "unknown",
        "slug":         "",
        "status":       "",
        "notes":        "",
        "detected_at":  datetime.now().strftime("%Y-%m-%d"),
    }

    try:
        careers_url, status = await find_careers_url(page, domain)

        if not careers_url:
            result["status"] = "not_found"
            await context.close()
            return result

        result["careers_url"] = careers_url
        # Wait for JS to load and fire ATS scripts
        await page.wait_for_timeout(3000)

        html = await page.content()
        platform, slug = detect_ats_from_content(careers_url, html, intercepted_urls)

        result["ats_platform"] = platform
        result["slug"]         = slug
        result["status"]       = "ok"

        if intercepted_urls:
            result["notes"] = f"API intercepted: {intercepted_urls[0][:80]}"

    except Exception as e:
        result["status"] = f"error"
        result["notes"]  = str(e)[:100]

    await context.close()
    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(companies: list[dict], output: Path) -> None:
    fieldnames = ["company", "tier", "website", "careers_url", "ats_platform",
                  "slug", "status", "notes", "detected_at"]

    # Load existing results to skip already-done companies
    done = set()
    if output.exists():
        with open(output, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "ok":
                    done.add(row["company"])
        print(f"Skipping {len(done)} already-detected companies")

    # Skip companies marked as parked in companies.csv
    parked = set()
    companies_csv = Path("data/companies.csv")
    if companies_csv.exists():
        import csv as _csv2
        with open(companies_csv) as _f:
            for _row in _csv2.DictReader(_f):
                if _row.get("scraper_status", "").strip().lower() == "parked":
                    parked.add(_row.get("company_name", "").strip())
        if parked:
            before = len(companies)
            companies = [c for c in companies if c["name"] not in parked]
            print(f"Skipping {before - len(companies)} parked companies (marked in companies.csv)")

    to_process = [c for c in companies if c["name"] not in done]
    print(f"Detecting ATS for {len(to_process)} companies...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        write_header = not output.exists()
        with open(output, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if write_header:
                writer.writeheader()

            for i, company in enumerate(to_process):
                print(f"  [{i+1}/{len(to_process)}] {company['name']}...", end=" ", flush=True)
                result = await detect_company(company, browser)
                writer.writerow(result)
                f.flush()
                status = result['status']
                print(f"{result['ats_platform']} / {result['slug'] or '-'} [{status}]")
                if status == "not_found":
                    print(f"    ⚠️  Could not find careers page for {company['name']}.")
                    print(f"    URLs tried: www.{company['website']}/careers, careers.{company['website']}, jobs.{company['website']}")
                    print(f"    ACTION: Open one of those URLs in your browser.")
                    print(f"      If careers page exists → add correct URL to data/companies.csv under careers_url")
                    print(f"      If company is not worth scraping → set scraper_status=parked in companies.csv")
                # Brief pause between companies
                await asyncio.sleep(1)

        await browser.close()

    print(f"\nDone. Results saved → {output}")
    # Print summary
    results = []
    with open(output, newline="", encoding="utf-8") as f:
        results = list(csv.DictReader(f))

    from collections import Counter
    platforms = Counter(r["ats_platform"] for r in results)
    print("\nATS platform summary:")
    for platform, count in platforms.most_common():
        print(f"  {platform}: {count} companies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect ATS platform for aerospace companies")
    parser.add_argument("--output",  type=Path, default=Path("data/ats_map.csv"))
    parser.add_argument("--company", type=str,  default=None,
                        help="Run for a single company name only")
    args = parser.parse_args()

    companies = COMPANIES
    if args.company:
        companies = [c for c in COMPANIES if args.company.lower() in c["name"].lower()]
        if not companies:
            print(f"Company not found: {args.company}")
            sys.exit(1)

    asyncio.run(main(companies, args.output))
