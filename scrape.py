#!/usr/bin/env python3
"""
Nebraska Supreme Court Oral Argument Podcast Feed Generator

Reads pre-downloaded HTML files from the pages/ directory (fetched by curl
in the GitHub Actions workflow) and produces a podcast-compatible RSS XML file.
"""

import re
import glob
import os
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urljoin
from html import escape

from bs4 import BeautifulSoup

# ── Configuration ────────────────────────────────────────────────────────────
BASE_URL = "https://nebraskajudicial.gov"
ARCHIVE_URL = f"{BASE_URL}/courts/supreme-court/supreme-court-oral-argument-archive"
PAGES_DIR = "pages"
OUTPUT_FILE = "nebraska-sc-oral-arguments.xml"

FEED_TITLE = "Nebraska Supreme Court Oral Arguments"
FEED_DESCRIPTION = "Audio recordings of oral arguments before the Nebraska Supreme Court."
FEED_AUTHOR = "Nebraska Judicial Branch"
FEED_LANGUAGE = "en-us"
FEED_LINK = ARCHIVE_URL
FEED_SELF_URL = os.environ.get(
    "FEED_SELF_URL",
    "https://arspader27-byte.github.io/nebraskasupremecourtoralarguments/nebraska-sc-oral-arguments.xml",
)

# Titles that look like date strings rather than case names — skip these
DATE_TITLE_RE = re.compile(
    r"^Arguments\s+on\s+\d|^\d{1,2}/\d{1,2}/\d{4}|^Oral\s+Arguments?\s+[-–]\s+\d",
    re.IGNORECASE,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_date(date_str: str) -> datetime | None:
    """Parse dates like 'Thursday, April 2, 2026'."""
    clean = re.sub(r"^[A-Za-z]+,\s*", "", date_str.strip())
    for fmt in ("%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def rfc2822(dt: datetime) -> str:
    return formatdate(dt.timestamp(), usegmt=True)


# ── Scraper ──────────────────────────────────────────────────────────────────

def scrape_page(html: str) -> list[dict]:
    """
    Parse one archive page using the Drupal Views DOM structure:
      Dates:       div.views-grouping-header > time.datetime
      Cases:       div.views-row-inner
        Title:       .views-field-title a
        Case number: .views-field-field-case-numbers .field-content
        County:      .views-field-field-court-number .field-content
        Audio:       source[src*="/sc/audio/"]
    """
    soup = BeautifulSoup(html, "html.parser")
    cases = []
    current_date = None

    view_content = (
        soup.find("div", class_="view-content")
        or soup.find("main")
        or soup
    )

    for element in view_content.descendants:
        if not hasattr(element, "name") or not element.name:
            continue

        # Date group headers
        if element.name == "div" and "views-grouping-header" in element.get("class", []):
            time_el = element.find("time", class_="datetime")
            if time_el:
                current_date = parse_date(time_el.get_text(strip=True))
            continue

        # Individual case rows
        if element.name == "div" and "views-row-inner" in element.get("class", []):
            title_el    = element.select_one(".views-field-title a")
            case_num_el = element.select_one(".views-field-field-case-numbers .field-content")
            county_el   = element.select_one(".views-field-field-court-number .field-content")
            audio_el    = element.select_one('source[src*="/sc/audio/"]')

            if not title_el:
                continue

            title = title_el.get_text(strip=True)

            # Skip video-only entries
            if "video" in title.lower():
                continue

            # Skip entries whose title is actually a date/index page rather than a case name
            if DATE_TITLE_RE.match(title):
                continue

            href = title_el.get("href", "")
            case_url = urljoin(BASE_URL, href) if href else ""
            case_number = (case_num_el.get_text(strip=True) if case_num_el else "").rstrip(")")
            county = county_el.get_text(strip=True) if county_el else ""

            audio_src = ""
            if audio_el:
                src = audio_el.get("src", "")
                audio_src = (BASE_URL + src) if src.startswith("/") else src

            cases.append({
                "title":       title,
                "date":        current_date,
                "case_number": case_number.strip(),
                "county":      county,
                "case_url":    case_url,
                "audio_url":   audio_src,   # may be empty — handled in RSS builder
                "description": "",
            })

    return cases


def load_all_cases() -> list[dict]:
    """Read every downloaded HTML file from pages/ in order."""
    html_files = sorted(
        glob.glob(f"{PAGES_DIR}/page_*.html"),
        key=lambda p: int(re.search(r"page_(\d+)", p).group(1)),
    )

    if not html_files:
        print(f"ERROR: No HTML files found in {PAGES_DIR}/")
        return []

    all_cases = []
    for path in html_files:
        with open(path, encoding="utf-8", errors="replace") as f:
            html = f.read()
        page_cases = scrape_page(html)
        print(f"  {path}: {len(page_cases)} cases")
        all_cases.extend(page_cases)

    with_audio    = sum(1 for c in all_cases if c["audio_url"])
    without_audio = sum(1 for c in all_cases if not c["audio_url"])
    print(f"\nTotal: {len(all_cases)} cases ({with_audio} with audio, {without_audio} awaiting audio)")
    return all_cases


def build_descriptions(cases: list[dict]) -> list[dict]:
    """Build show-notes text from structured data."""
    for case in cases:
        date_str = case["date"].strftime("%B %-d, %Y") if case["date"] else "Unknown date"
        audio_note = "" if case["audio_url"] else "\n\nNote: Audio not yet available for this argument."
        case["description"] = (
            f"Nebraska Supreme Court Oral Argument\n\n"
            f"Case: {case['title']}\n"
            f"Case Number: {case['case_number']}\n"
            f"County: {case['county']} County\n"
            f"Argument Date: {date_str}\n\n"
            f"Case details: {case['case_url']}"
            f"{audio_note}"
        )
    return cases


# ── RSS Builder ──────────────────────────────────────────────────────────────

def build_rss(cases: list[dict]) -> str:
    now_rfc = rfc2822(datetime.now(timezone.utc))

    items = []
    for case in cases:
        pub_date = rfc2822(case["date"]) if case["date"] else now_rfc
        guid     = case["audio_url"] or case["case_url"]
        date_str = case["date"].strftime("%B %-d, %Y") if case["date"] else ""
        subtitle = f"Case {case['case_number']} · {case['county']} County · {date_str}".strip(" ·")

        # Only include <enclosure> when we actually have an audio URL.
        # Omitting it entirely prevents podcast apps from showing a "no media" error.
        enclosure_tag = (
            f'\n      <enclosure url="{escape(case["audio_url"])}" type="audio/mpeg" length="0" />'
            f'\n      <itunes:duration>0</itunes:duration>'
            if case["audio_url"] else ""
        )

        items.append(f"""    <item>
      <title>{escape(case['title'])}</title>
      <link>{escape(case['case_url'])}</link>
      <guid isPermaLink="false">{escape(guid)}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{escape(case['description'])}</description>
      <itunes:title>{escape(case['title'])}</itunes:title>
      <itunes:subtitle>{escape(subtitle)}</itunes:subtitle>
      <itunes:summary>{escape(case['description'])}</itunes:summary>{enclosure_tag}
    </item>""")

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
{"".join(items)}
  </channel>
</rss>"""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"Reading HTML files from {PAGES_DIR}/...")
    cases = load_all_cases()
    if not cases:
        print("No cases found — aborting.")
        return

    cases = build_descriptions(cases)
    xml   = build_rss(cases)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"Wrote {len(cases)} episodes to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
