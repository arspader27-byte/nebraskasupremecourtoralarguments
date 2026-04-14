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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
REQUEST_DELAY = 1.5
MAX_PAGES = 100


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


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


def fetch_case_description(case_url: str) -> str:
    """Fetch case detail page and return plain-text show notes."""
    try:
        time.sleep(REQUEST_DELAY)
        soup = get_soup(case_url)
        main = soup.find("main") or soup.find("div", class_=re.compile(r"content|main|body", re.I))
        if not main:
            return ""
        paragraphs = []
        for tag in main.find_all(["p", "li"]):
            text = tag.get_text(separator=" ", strip=True)
            if text and len(text) > 20:
                paragraphs.append(text)
        return "\n\n".join(paragraphs[:8]).strip()
    except Exception as exc:
        print(f"    Warning: could not fetch case detail ({exc})")
        return ""


# ── Scraper ──────────────────────────────────────────────────────────────────

def scrape_page(soup: BeautifulSoup) -> list[dict]:
    """
    Parse one archive page using correct DOM structure:
      Dates:  div.views-grouping-header > time.datetime
      Cases:  div.views-row-inner
        Title:       .views-field-title a
        Case number: .views-field-field-case-numbers .field-content
        County:      .views-field-field-court-number .field-content
        Audio:       source[src*="/sc/audio/"]
    """
    cases = []
    current_date = None

    view_content = soup.find("div", class_="view-content") or soup.find("main") or soup

    for element in view_content.descendants:
        if not hasattr(element, "name") or not element.name:
            continue

        # Date headings
        if element.name == "div" and "views-grouping-header" in element.get("class", []):
            time_el = element.find("time", class_="datetime")
            if time_el:
                current_date = parse_date(time_el.get_text(strip=True))
            continue

        # Case rows
        if element.name == "div" and "views-row-inner" in element.get("class", []):
            title_el = element.select_one(".views-field-title a")
            case_num_el = element.select_one(".views-field-field-case-numbers .field-content")
            county_el = element.select_one(".views-field-field-court-number .field-content")
            audio_el = element.select_one('source[src*="/sc/audio/"]')

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if "video" in title.lower():
                continue

            href = title_el.get("href", "")
            case_url = urljoin(BASE_URL, href) if href else ""
            case_number = case_num_el.get_text(strip=True) if case_num_el else ""
            county = county_el.get_text(strip=True) if county_el else ""
            case_number = re.sub(r"\)$", "", case_number).strip()

            audio_src = ""
            if audio_el:
                audio_src = audio_el.get("src", "")
                if audio_src.startswith("/"):
                    audio_src = BASE_URL + audio_src

            cases.append({
                "title": title,
                "date": current_date,
                "case_number": case_number,
                "county": county,
                "case_url": case_url,
                "audio_url": audio_src,
                "description": "",
            })

    return cases


def scrape_all_cases() -> list[dict]:
    """Iterate through all paginated archive pages."""
    all_cases = []
    page = 0

    while page < MAX_PAGES:
        url = ARCHIVE_URL if page == 0 else f"{ARCHIVE_URL}?page={page}"
        print(f"Scraping page {page + 1}: {url}")

        try:
            soup = get_soup(url)
        except Exception as exc:
            print(f"  Error fetching page: {exc}")
            break

        page_cases = scrape_page(soup)
        print(f"  Found {len(page_cases)} cases on this page")
        all_cases.extend(page_cases)

        next_link = soup.select_one("li.pager__item--next a, a[rel='next']")
        if not next_link:
            print("  No next page — done.")
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"\nTotal: {len(all_cases)} cases across {page + 1} page(s).")
    return all_cases


def enrich_with_descriptions(cases: list[dict]) -> list[dict]:
    """Visit each case detail page to populate show notes."""
    print("\nFetching case detail pages for show notes...")
    for i, case in enumerate(cases):
        print(f"  [{i+1}/{len(cases)}] {case['title']}")
        detail = fetch_case_description(case["case_url"])
        date_str = case["date"].strftime("%B %-d, %Y") if case["date"] else "Unknown date"
        base = (
            f"Nebraska Supreme Court Oral Argument\n\n"
            f"Case: {case['title']}\n"
            f"Case Number: {case['case_number']}\n"
            f"County: {case['county']} County\n"
            f"Argument Date: {date_str}\n\n"
            f"Case details: {case['case_url']}"
        )
        case["description"] = (base + "\n\n" + detail).strip() if detail else base
    return cases


# ── RSS Builder ──────────────────────────────────────────────────────────────

def build_rss(cases: list[dict]) -> str:
    now_rfc = rfc2822(datetime.now(timezone.utc))

    items = []
    for case in cases:
        pub_date = rfc2822(case["date"]) if case["date"] else now_rfc
        guid = case["audio_url"] or case["case_url"]
        date_str = case["date"].strftime("%B %-d, %Y") if case["date"] else ""
        subtitle = f"Case {case['case_number']} · {case['county']} County · {date_str}".strip(" ·")

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
