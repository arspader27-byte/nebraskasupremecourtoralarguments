#!/usr/bin/env python3
"""
Nebraska Supreme Court Oral Argument Podcast Feed Generator
Scrapes the Nebraska Judicial Branch archive and produces a podcast-compatible RSS XML file.
"""

import re
import time
import os
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urljoin
from html import escape

import requests
from bs4 import BeautifulSoup

# ── Configuration ────────────────────────────────────────────────────────────
ARCHIVE_URL = "https://nebraskajudicial.gov/courts/supreme-court/supreme-court-oral-argument-archive"
BASE_URL = "https://nebraskajudicial.gov"
AUDIO_BASE = "https://nebraskajudicial.gov/sites/default/files/audio/"
OUTPUT_FILE = "nebraska-sc-oral-arguments.xml"

# Customise these to match your feed settings
FEED_TITLE = "Nebraska Supreme Court Oral Arguments"
FEED_DESCRIPTION = "Audio recordings of oral arguments before the Nebraska Supreme Court."
FEED_AUTHOR = "Nebraska Judicial Branch"
FEED_LANGUAGE = "en-us"
FEED_LINK = ARCHIVE_URL
# Set this to your actual GitHub Pages URL once deployed:
FEED_SELF_URL = os.environ.get(
    "FEED_SELF_URL",
    "https://arspader27-byte.github.io/nebraska-sc-podcast/nebraska-sc-oral-arguments.xml",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PodcastFeedBot/1.0; "
        "+https://github.com/yourusername/nebraska-sc-podcast)"
    )
}
REQUEST_DELAY = 1.0   # seconds between requests — be polite
MAX_PAGES = 100       # safety cap on pagination


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_date(date_str: str) -> datetime | None:
    """Parse dates like 'Wednesday, February 4, 2026' → datetime."""
    clean = re.sub(r"^[A-Za-z]+,\s*", "", date_str.strip())
    for fmt in ("%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def rfc2822(dt: datetime) -> str:
    return formatdate(dt.timestamp(), usegmt=True)


def infer_audio_url(date: datetime, case_number: str) -> str:
    """
    Build the audio URL from the filename pattern observed on the site:
    SC_YYYYMMDD_CaseNumber.mp3
    Multiple-case entries use a '+' suffix (e.g. SC_20260203_25-0170+.mp3).
    """
    date_part = date.strftime("%Y%m%d")
    # Strip the leading 'S-' from case numbers; use first number for multi-case entries
    first_number = case_number.split(",")[0].strip().lstrip("S-")
    filename = f"SC_{date_part}_{first_number}.mp3"
    return AUDIO_BASE + filename


def fetch_case_description(case_url: str) -> str:
    """Fetch the case detail page and return a plain-text description for show notes."""
    try:
        time.sleep(REQUEST_DELAY)
        soup = get_soup(case_url)

        # The case detail pages have a main content area; grab all paragraph text
        main = (
            soup.find("main")
            or soup.find("div", class_=re.compile(r"content|main|body", re.I))
            or soup.find("article")
        )
        if not main:
            return ""

        paragraphs = []
        for tag in main.find_all(["p", "li"]):
            text = tag.get_text(separator=" ", strip=True)
            if text and len(text) > 20:
                paragraphs.append(text)

        return "\n\n".join(paragraphs[:10])  # cap at first 10 paragraphs
    except Exception as exc:
        print(f"  Warning: could not fetch case detail page ({exc})")
        return ""


# ── Scraper ──────────────────────────────────────────────────────────────────

def scrape_all_cases() -> list[dict]:
    """Iterate through all paginated archive pages and collect every case."""
    cases = []
    page = 0

    while page < MAX_PAGES:
        url = ARCHIVE_URL if page == 0 else f"{ARCHIVE_URL}?page={page}"
        print(f"Scraping archive page {page + 1}: {url}")
        try:
            soup = get_soup(url)
        except Exception as exc:
            print(f"  Error fetching page {page}: {exc}")
            break

        # Each call-date block: a heading followed by case rows
        current_date = None
        content = soup.find("main") or soup.find("div", id=re.compile(r"main|content", re.I)) or soup

        for element in content.find_all(True):
            tag = element.name

            # Date headings appear as plain text nodes or <h2>/<h3>/<strong>
            if tag in ("h2", "h3", "h4", "strong", "p"):
                text = element.get_text(strip=True)
                parsed = parse_date(text)
                if parsed:
                    current_date = parsed
                    continue

            # Case links
            if tag == "a" and current_date:
                href = element.get("href", "")
                title = element.get_text(strip=True)
                if not title or not href:
                    continue
                if "/supreme-court-call/" not in href:
                    continue
                # Skip video links
                if "video" in title.lower() or "video" in href.lower():
                    continue

                case_url = urljoin(BASE_URL, href)

                # Case number and county sit near the link — walk siblings
                case_number = ""
                county = ""
                parent = element.find_parent()
                if parent:
                    full_text = parent.get_text(separator="|", strip=True)
                    num_match = re.search(r"S-\d{2}-\d{4}[^|]*", full_text)
                    if num_match:
                        case_number = num_match.group(0).strip().rstrip(")")
                    county_match = re.search(r"County:\s*([^|]+)", full_text)
                    if county_match:
                        county = county_match.group(1).strip()

                # Also check grandparent / sibling divs
                if not case_number or not county:
                    container = element.find_parent("div") or element.find_parent("li")
                    if container:
                        ct = container.get_text(separator="|")
                        if not case_number:
                            nm = re.search(r"S-\d{2}-\d{4}[^|]*", ct)
                            if nm:
                                case_number = nm.group(0).strip().rstrip(")")
                        if not county:
                            cm = re.search(r"County:\s*([^|\n]+)", ct)
                            if cm:
                                county = cm.group(1).strip()

                audio_url = infer_audio_url(current_date, case_number) if case_number else ""

                cases.append({
                    "title": title,
                    "date": current_date,
                    "case_number": case_number,
                    "county": county,
                    "case_url": case_url,
                    "audio_url": audio_url,
                    "description": "",   # filled in below
                })

        # Check for a "next page" link
        next_link = soup.find("a", string=re.compile(r"next|›|»", re.I))
        if not next_link:
            # Also check aria-label
            next_link = soup.find("a", attrs={"aria-label": re.compile(r"next", re.I)})
        if not next_link:
            print("  No next page found — done paginating.")
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"\nFound {len(cases)} cases across {page + 1} archive page(s).")
    return cases


def enrich_with_descriptions(cases: list[dict]) -> list[dict]:
    """Fetch each case detail page to get the summary for show notes."""
    print("\nFetching case detail pages for show notes…")
    for i, case in enumerate(cases):
        print(f"  [{i+1}/{len(cases)}] {case['title']}")
        desc = fetch_case_description(case["case_url"])
        base_notes = (
            f"Nebraska Supreme Court Oral Argument\n\n"
            f"Case: {case['title']}\n"
            f"Case Number: {case['case_number']}\n"
            f"County: {case['county']} County\n"
            f"Argument Date: {case['date'].strftime('%B %-d, %Y')}\n\n"
            f"Case details: {case['case_url']}"
        )
        case["description"] = (base_notes + "\n\n" + desc).strip() if desc else base_notes
    return cases


# ── RSS Builder ──────────────────────────────────────────────────────────────

def build_rss(cases: list[dict]) -> str:
    now_rfc = rfc2822(datetime.now(timezone.utc))

    items = []
    for case in cases:
        pub_date = rfc2822(case["date"])
        guid = case["audio_url"] or case["case_url"]
        subtitle = f"Case {case['case_number']} · {case['county']} County · {case['date'].strftime('%B %-d, %Y')}"

        items.append(f"""    <item>
      <title>{escape(case['title'])}</title>
      <link>{escape(case['case_url'])}</link>
      <guid isPermaLink="false">{escape(guid)}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{escape(case['description'])}</description>
      <itunes:title>{escape(case['title'])}</itunes:title>
      <itunes:subtitle>{escape(subtitle)}</itunes:subtitle>
      <itunes:summary>{escape(case['description'])}</itunes:summary>
      <enclosure url="{escape(case['audio_url'])}" type="audio/mpeg" length="0" />
      <itunes:duration>0</itunes:duration>
    </item>""")

    items_xml = "\n".join(items)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(FEED_TITLE)}</title>
    <link>{escape(FEED_LINK)}</link>
    <description>{escape(FEED_DESCRIPTION)}</description>
    <language>{escape(FEED_LANGUAGE)}</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <atom:link href="{escape(FEED_SELF_URL)}" rel="self" type="application/rss+xml" />
    <itunes:author>{escape(FEED_AUTHOR)}</itunes:author>
    <itunes:category text="Government" />
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
{items_xml}
  </channel>
</rss>"""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    cases = scrape_all_cases()
    if not cases:
        print("No cases found — aborting.")
        return

    cases = enrich_with_descriptions(cases)
    xml = build_rss(cases)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"\nWrote {len(cases)} episodes to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
