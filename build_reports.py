#!/usr/bin/env python3
"""
Trip reports, from the Nelson Tramping Club archive.

The club has published a trip report for nearly every outing since 2003, and
the whole index sits on one page. Each entry carries its title, the date, and
the author. That is a far better source for this region than Peakbagger or
AllTrails, which are thin here or blocked outright.

What this does NOT do, on purpose:

  * It does not copy the reports. Only the title, date, author and a link.
  * It does not turn a report into a fact about the track. A report is one
    person's day in one set of conditions, years ago in most cases. The app
    says "5 reports mention this peak, most recent March 2019" and lets you
    read them yourself.

Matching is by name, and it is deliberately loose: the panel says the reports
"mention" the peak rather than claiming they are reports of that climb. You
read the titles and judge.

Run:  python build_reports.py
"""

import json
import io
import os
import re
import time
import html
import urllib.request

from build_data import SSL_CTX, DATA, log

INDEX = "https://live.nelsontrampingclub.org.nz/trip-reports"
SITE = "https://live.nelsontrampingclub.org.nz"
UA = ("Mozilla/5.0 (compatible; nelson-peak-map/0.5; "
      "personal, non-commercial hiking planner)")
CACHE = os.path.join(DATA, "trip_reports_raw.html")

MONTHS = ("january february march april may june july august september "
          "october november december").split()

# Words too common to identify a peak on their own.
GENERIC = {"mount", "mt", "the", "peak", "hill", "range", "point", "rock",
           "saddle", "spur", "knob", "top", "col", "pass", "creek", "river",
           "valley", "lake", "bay", "hut", "track", "north", "south", "east",
           "west", "big", "little", "old", "new", "upper", "lower"}


def fetch_index(refresh=False):
    if os.path.exists(CACHE) and not refresh and os.path.getsize(CACHE) > 50000:
        log("  using cached index")
        return io.open(CACHE, encoding="utf-8", errors="replace").read()
    log("  downloading the club index")
    req = urllib.request.Request(INDEX, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
        raw = r.read().decode("utf-8", "replace")
    io.open(CACHE, "w", encoding="utf-8").write(raw)
    return raw


def parse_reports(page):
    """Pull out one entry per report: id, title, date, author, link."""
    out = {}
    pattern = re.compile(
        r'<a[^>]+href="(?P<href>[^"]*view=article[^"]*id=(?P<id>\d+):[^"]*)"'
        r'[^>]*>(?P<text>.*?)</a>', re.S)
    for m in pattern.finditer(page):
        text = html.unescape(re.sub(r"<[^>]+>", " ", m.group("text")))
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text.lower().startswith("click to read"):
            continue
        rid = m.group("id")
        if rid in out and len(out[rid]["title"]) >= len(text):
            continue
        href = html.unescape(m.group("href"))
        out[rid] = {
            "id": rid,
            "title": text,
            "url": SITE + href if href.startswith("/") else href,
        }

    for r in out.values():
        r["date"], r["year"] = parse_date(r["title"])
    return sorted(out.values(), key=lambda r: (r["year"] or 0), reverse=True)


def parse_date(title):
    """Club titles end with the date, for example '2nd August 2026'."""
    low = title.lower()
    year = None
    ym = re.findall(r"\b(19|20)(\d\d)\b", low)
    if ym:
        year = int(ym[-1][0] + ym[-1][1])
    month = None
    for i, name in enumerate(MONTHS, 1):
        if name in low:
            month = i
            break
    day = None
    dm = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:%s)" % "|".join(MONTHS), low)
    if dm:
        day = int(dm.group(1))
    if year and month:
        return ("%04d-%02d%s" % (year, month, "-%02d" % day if day else ""),
                year)
    return (None, year)


# Titles read "Destination, Area, Date", but the separator is sometimes a dash
# rather than a comma: "Gordons Knob - Mt Richmond FP, 5th July 2026".
SPLIT = re.compile(r"\s*[,–—]\s*|\s+-\s+")

# "Mt Richmond FP" is a park, not a destination. Without this, every trip in
# that forest park looked like a trip up Mount Richmond.
PARK = re.compile(
    r"\b(fp|np|forest\s+park|national\s+park|conservation\s+(park|area)|"
    r"state\s+forest|reserve)\b")


def first_chunk(low):
    return SPLIT.split(low)[0]


def only_a_park_name(low, words):
    """True when every mention of the peak is immediately part of a park name."""
    for w in words:
        for m in re.finditer(re.escape(w), low):
            tail = low[m.end():m.end() + 26]
            if not PARK.match(tail.strip()):
                return False
    return True


# A peak's name can be the first half of a completely different place:
# "Arthur's Pass" is not Mount Arthur, and "Hacket River" is not Hacket Peak.
# If the matched word is always followed by one of these, it is somewhere else.
OTHER_FEATURE = re.compile(
    r"\b(pass|river|stream|creek|valley|bay|sound|road|street|township|"
    r"village|flat|flats|beach|lagoon|gorge|junction|bridge|township)\b")


def names_another_feature(low, words, peak_name):
    """True when every mention of the peak's word belongs to another feature."""
    own = set(re.sub(r"[^a-z ]", " ", peak_name.lower()).split())
    for w in words:
        for m in re.finditer(re.escape(w) + r"(?:'?s)?", low):
            tail = low[m.end():m.end() + 20].strip()
            nxt = OTHER_FEATURE.match(tail)
            # A trailing word that is part of the peak's own name is fine.
            if not nxt or nxt.group(1) in own:
                return False
    return True


def normalise(text):
    """Curly quotes and dashes defeat plain matching, so flatten them."""
    for ch in "’‘“”":
        text = text.replace(ch, "'")
    return text.replace(chr(0x2013), "-").replace(chr(0x2014), "-")


def key_words(name):
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
            if w not in GENERIC and len(w) > 3}


def main():
    page = fetch_index("--refresh" in os.sys.argv)
    reports = parse_reports(page)
    log("   %d reports, %s to %s"
        % (len(reports),
           min(r["year"] for r in reports if r["year"]),
           max(r["year"] for r in reports if r["year"])))

    peaks_file = os.path.join(DATA, "peaks.json")
    if not os.path.exists(peaks_file):
        peaks_file = os.path.join(DATA, "peaks.public.json")
    peaks = json.load(io.open(peaks_file, encoding="utf-8"))["peaks"]

    log("")
    log("Matching reports to summits by name")
    matches = {}
    for p in peaks:
        words = key_words(p["name"])
        if not words:
            continue
        hits = []
        for r in reports:
            low = normalise(r["title"].lower())
            if not all(w in low for w in words):
                continue
            destination = first_chunk(low)
            about = (all(w in destination for w in words)
                     and not only_a_park_name(low, words)
                     and not names_another_feature(low, words, p["name"]))
            hits.append(dict(r, about=about))
        if hits:
            hits.sort(key=lambda r: (r["about"], r["year"] or 0), reverse=True)
            matches[p["id"]] = hits[:8]
    named = sum(1 for rs in matches.values() if any(r["about"] for r in rs))
    log("   %d of %d peaks are mentioned in at least one report"
        % (len(matches), len(peaks)))
    log("   %d of those have a report that is actually about the peak" % named)

    top = sorted(matches.items(), key=lambda kv: -len(kv[1]))[:6]
    by_id = {p["id"]: p for p in peaks}
    for pid, rs in top:
        log("     %-22s %d reports, latest %s"
            % (by_id[pid]["name"][:22], len(rs), rs[0]["date"] or rs[0]["year"]))

    log("")
    for name in ("peaks.json", "peaks.public.json"):
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            continue
        d = json.load(io.open(path, encoding="utf-8"))
        n = 0
        for p in d["peaks"]:
            rs = matches.get(p["id"])
            p["reports"] = [{"title": r["title"], "date": r["date"],
                             "year": r["year"], "url": r["url"],
                             "about": r["about"]}
                            for r in rs] if rs else None
            if rs:
                n += 1
        d["sources"]["trip_reports"] = (
            "Nelson Tramping Club trip report archive, "
            "live.nelsontrampingclub.org.nz. Titles and links only.")
        d["reports_checked"] = time.strftime("%Y-%m-%d %H:%M")
        io.open(path, "w", encoding="utf-8").write(
            json.dumps(d, indent=1, ensure_ascii=False))
        log("Wrote %s, %d peaks with reports" % (name, n))


if __name__ == "__main__":
    main()
