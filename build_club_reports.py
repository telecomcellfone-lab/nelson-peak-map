#!/usr/bin/env python3
"""
Sweep New Zealand tramping club trip report archives and match them to peaks.

Only titles, dates and links are collected. The reports themselves are never
copied, and they are never converted into a claim about a track.

WHICH CLUBS, AND WHY NOT THE OTHERS
-----------------------------------
Federated Mountain Clubs lists about 110 clubs, 27 of them in the top of the
South. Most turned out to have nothing to sweep. What the survey found:

  Nelson Tramping Club     688 reports, 2003 onward. Used, see build_reports.py
  Wellington TMC           about 1,120 reports. Used.
  Waimea Tramping Club     490 reports, and the closest club to home, but its
                           robots.txt is "Disallow: /" for every crawler, so it
                           is left alone. Ask the club and it can be switched
                           on here in one line.
  Auckland TC              robots.txt "Disallow: /". Left alone.
  Christchurch TC          route archive is behind a member login.
  tramper.nz               the old community site is gone. The domain now
                           serves generic travel-blog filler.
  OTMC, CMC, PTC           static sites, no report archive published.
  remotehuts.co.nz         hut pages rather than trip reports.
  Marlborough, Motueka,
  Golden Bay clubs         informal, on webs.com, weebly or a community page.

ETIQUETTE
---------
robots.txt is checked before anything is fetched, and the rules for
User-agent: * are obeyed. A club that disallows crawling is skipped, and the
skip is reported rather than hidden. Requests are spaced out. Wellington's
robots.txt names GPTBot and blocks it; this is not GPTBot, it obeys the
rules for "*", it takes only what a search engine would take, and every
report links back to the club.

Run:  python build_club_reports.py
      python build_club_reports.py --refresh
"""

import json
import io
import os
import re
import sys
import time
import html
import urllib.request
import urllib.parse

from build_data import SSL_CTX, DATA, log
from build_reports import (parse_date, key_words, first_chunk,
                           only_a_park_name, names_another_feature,
                           normalise)

UA = ("Mozilla/5.0 (compatible; nelson-peak-map/0.6; "
      "personal, non-commercial hiking planner)")
PAUSE = 1.5
CACHE = os.path.join(DATA, "club_reports.json")

SOURCES = [
    {
        "club": "Wellington Tramping and Mountaineering Club",
        "short": "WTMC",
        "host": "wtmc.org.nz",
        # The site links /trip-report/page/N/ but that 404s. The category
        # route is the one that actually paginates.
        "index": "https://wtmc.org.nz/category/trip-report/page/%d/",
        "first_page": "https://wtmc.org.nz/category/trip-report/",
        "max_pages": 130,
    },
    # Waimea Tramping Club sits here ready to go, but its robots.txt disallows
    # every crawler. Uncomment only if the club says yes.
    # {
    #     "club": "Waimea Tramping Club",
    #     "short": "Waimea",
    #     "host": "www.waimeatrampingclub.org.nz",
    #     "first_page": "https://www.waimeatrampingclub.org.nz/reports/trip-reports-list?limit=0",
    #     "link": re.compile(r'href="([^"]*trip-reports-list/(\d+-[a-z0-9-]+))"'),
    #     "max_pages": 1,
    # },
]


# ------------------------------------------------------------------ robots

def robots_allows(host):
    """Obey the rules published for User-agent: *."""
    try:
        req = urllib.request.Request("https://%s/robots.txt" % host,
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25, context=SSL_CTX) as r:
            if r.status != 200:
                return True, "no robots.txt"
            txt = r.read().decode("utf-8", "replace")
    except Exception:
        return True, "no robots.txt"
    applies, disallow = False, []
    for line in txt.splitlines():
        line = line.split("#")[0].strip()
        if ":" not in line:
            continue
        key, value = [x.strip() for x in line.split(":", 1)]
        key = key.lower()
        if key == "user-agent":
            applies = (value == "*")
        elif applies and key == "disallow" and value:
            disallow.append(value)
    if "/" in disallow:
        return False, "robots.txt disallows all crawlers"
    return True, "%d path rules" % len(disallow)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")


# Each listing entry is a WordPress <article> carrying the real title, the
# date, and a set of place tags. The tags are the useful part: a report on the
# Broken Axe Pinnacles is tagged with every hut, spur and range it touched, so
# matching against them finds trips that never name the peak in the title.
ARTICLE = re.compile("<article", re.I)
CLASSES = re.compile(r'class="([^"]*)"')
TITLE = re.compile(
    r'<h2[^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S)
TIME = re.compile(r'<time[^>]+datetime="(\d{4})-(\d{2})-(\d{2})', re.I)


def parse_articles(page):
    """Yield one record per listing entry: url, title, date, tags."""
    starts = [m.start() for m in ARTICLE.finditer(page)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(page)
        block = page[start:end]
        t = TITLE.search(block)
        if not t:
            continue
        url = html.unescape(t.group(1))
        if "/trip-report/" not in url:
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", t.group(2)))
        title = re.sub(r"\s+", " ", title).strip()
        cls = CLASSES.search(block)
        tags = []
        if cls:
            tags = [c[4:].replace("-", " ") for c in cls.group(1).split()
                    if c.startswith("tag-")]
        d = TIME.search(block)
        date = "%s-%s-%s" % d.groups() if d else None
        year = int(d.group(1)) if d else None
        yield {"url": url, "title": title, "tags": tags,
               "date": date, "year": year}


# -------------------------------------------------------------------- sweep

def sweep(source, refresh):
    allowed, note = robots_allows(source["host"])
    if not allowed:
        log("  %-12s SKIPPED, %s" % (source["short"], note))
        return []
    log("  %-12s allowed (%s)" % (source["short"], note))

    seen, out = set(), []
    pages = source.get("max_pages", 1)
    for n in range(1, pages + 1):
        url = source["first_page"] if n == 1 else source["index"] % n
        try:
            page = get(url)
        except Exception as e:
            log("    page %d stopped the sweep (%s)" % (n, type(e).__name__))
            break
        found = 0
        for rec in parse_articles(page):
            if rec["url"] in seen:
                continue
            seen.add(rec["url"])
            rec.update(club=source["club"], short=source["short"])
            out.append(rec)
            found += 1
        if found == 0 and n > 1:
            log("    page %d had nothing new, stopping" % n)
            break
        if n % 20 == 0:
            log("    page %d, %d reports so far" % (n, len(out)))
        time.sleep(PAUSE)
    log("  %-12s %d reports" % (source["short"], len(out)))
    return out


def main():
    refresh = "--refresh" in sys.argv
    if os.path.exists(CACHE) and not refresh:
        reports = json.load(io.open(CACHE, encoding="utf-8"))
        log("Using %d cached club reports. Use --refresh to sweep again."
            % len(reports))
    else:
        log("Sweeping club archives")
        reports = []
        for source in SOURCES:
            reports += sweep(source, refresh)
        io.open(CACHE, "w", encoding="utf-8").write(
            json.dumps(reports, ensure_ascii=False))

    peaks_file = os.path.join(DATA, "peaks.json")
    if not os.path.exists(peaks_file):
        peaks_file = os.path.join(DATA, "peaks.public.json")

    log("")
    log("Matching to summits")
    matches = {}
    peaks = json.load(io.open(peaks_file, encoding="utf-8"))["peaks"]
    for p in peaks:
        words = key_words(p["name"])
        if not words:
            continue
        hits = []
        for r in reports:
            low = normalise(r["title"].lower())
            tags = normalise(" | ".join(r.get("tags") or []).lower())
            in_title = all(w in low for w in words)
            in_tags = all(w in tags for w in words)
            if not (in_title or in_tags):
                continue
            # A title match means the trip was named for this place. A tag
            # match means the trip passed through it, which is still worth
            # reading but is not a report of that climb.
            about = in_title and all(w in first_chunk(low) for w in words)                 and not only_a_park_name(low, words)                 and not names_another_feature(low, words, p["name"])
            hits.append(dict(r, about=about))
        if hits:
            hits.sort(key=lambda r: (r["about"], r["year"] or 0), reverse=True)
            matches[p["id"]] = hits[:6]
    log("   %d peaks picked up a club report" % len(matches))

    log("")
    for name in ("peaks.json", "peaks.public.json"):
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            continue
        d = json.load(io.open(path, encoding="utf-8"))
        added = 0
        for p in d["peaks"]:
            extra = matches.get(p["id"])
            if not extra:
                continue
            existing = {r["url"] for r in (p.get("reports") or [])}
            merged = list(p.get("reports") or [])
            for r in extra:
                if r["url"] in existing:
                    continue
                merged.append({"title": r["title"], "date": r["date"],
                               "year": r["year"], "url": r["url"],
                               "about": r["about"], "club": r["short"]})
                added += 1
            merged.sort(key=lambda r: (r.get("about", True), r.get("year") or 0),
                        reverse=True)
            p["reports"] = merged[:12]
        d["sources"]["club_reports"] = (
            "Trip report titles and links from " +
            ", ".join(s["club"] for s in SOURCES) +
            ", plus the Nelson Tramping Club. Titles and links only.")
        io.open(path, "w", encoding="utf-8").write(
            json.dumps(d, indent=1, ensure_ascii=False))
        log("Wrote %s, %d club reports added" % (name, added))


if __name__ == "__main__":
    main()
