#!/usr/bin/env python3
"""
Stage 3, part one: official DOC track information and live alerts.

Everything so far has come from OpenStreetMap, which is good but unofficial.
The Department of Conservation publishes its own track data, and where a DOC
track reaches a summit its numbers beat mine:

  * DOC's own walking time, which is measured rather than estimated. For
    Mount Arthur DOC says 3 hr 30 min to 4 hr 30 min one way. Naismith's rule
    put the whole return trip at 4 hr 46 min, which is far too optimistic for
    New Zealand alpine ground.
  * DOC's track grading, in their own words.
  * Live alerts. Closures, slips, washed out bridges. Nothing else in this
    project can tell you a track is shut.
  * A link to the official DOC page for the track.

Needs a free DOC API key in doc-api-key.local.json, which is git-ignored:

    {"key": "your key here"}

Register at api.doc.govt.nz, then subscribe the key to v1-tracks and
v2-alerts. A key that exists but is not subscribed returns a bare "Forbidden".

Run:  python build_doc.py
"""

import json
import io
import os
import math
import time
import urllib.request
import urllib.error

from build_data import SSL_CTX, DATA, HERE, BBOX, haversine_m, log

API = "https://api.doc.govt.nz/"
UA = "nelson-peak-map/0.3 (personal, non-commercial hiking planner)"

# A DOC track passing this close to a summit is treated as the route to it.
MATCH_RADIUS_M = 600
PAUSE = 0.25

CACHE = os.path.join(DATA, "doc_cache.json")


# ------------------------------------------------- New Zealand map grid

def nztm_to_wgs84(easting, northing):
    """
    Convert NZTM2000 (EPSG:2193) to latitude and longitude.

    DOC publishes track geometry on the New Zealand grid, not in degrees.
    This is the standard inverse transverse Mercator on the GRS80 ellipsoid
    with LINZ's published parameters. Checked against a known point: the
    Mount Arthur Summit Route ends 60 m from the recorded summit.
    """
    a = 6378137.0
    f = 1 / 298.257222101
    lat0, lon0 = 0.0, math.radians(173.0)
    k0, FE, FN = 0.9996, 1600000.0, 10000000.0

    b = a * (1 - f)
    esq = (a * a - b * b) / (a * a)
    A0 = 1 - esq / 4 - 3 * esq ** 2 / 64 - 5 * esq ** 3 / 256
    A2 = 3.0 / 8 * (esq + esq ** 2 / 4 + 15 * esq ** 3 / 128)
    A4 = 15.0 / 256 * (esq ** 2 + 3 * esq ** 3 / 4)
    A6 = 35 * esq ** 3 / 3072

    def meridian(lat):
        return a * (A0 * lat - A2 * math.sin(2 * lat)
                    + A4 * math.sin(4 * lat) - A6 * math.sin(6 * lat))

    mp = meridian(lat0) + (northing - FN) / k0
    n = (a - b) / (a + b)
    G = a * (1 - n) * (1 - n * n) * (1 + 9 * n * n / 4 + 225 * n ** 4 / 64) \
        * math.pi / 180
    sigma = mp * math.pi / (180 * G)
    foot = (sigma
            + (3 * n / 2 - 27 * n ** 3 / 32) * math.sin(2 * sigma)
            + (21 * n * n / 16 - 55 * n ** 4 / 32) * math.sin(4 * sigma)
            + (151 * n ** 3 / 96) * math.sin(6 * sigma)
            + (1097 * n ** 4 / 512) * math.sin(8 * sigma))

    rho = a * (1 - esq) / ((1 - esq * math.sin(foot) ** 2) ** 1.5)
    ups = a / math.sqrt(1 - esq * math.sin(foot) ** 2)
    psi = ups / rho
    t = math.tan(foot)
    Ep = easting - FE
    x = Ep / (k0 * ups)

    t1 = t / (k0 * rho) * (Ep * x / 2)
    t2 = t / (k0 * rho) * (Ep * x ** 3 / 24) \
        * (-4 * psi * psi + 9 * psi * (1 - t * t) + 12 * t * t)
    t3 = t / (k0 * rho) * (Ep * x ** 5 / 720) \
        * (8 * psi ** 4 * (11 - 24 * t * t) - 12 * psi ** 3 * (21 - 71 * t * t)
           + 15 * psi * psi * (15 - 98 * t * t + 15 * t ** 4)
           + 180 * psi * (5 * t * t - 3 * t ** 4) + 360 * t ** 4)
    lat = foot - t1 + t2 - t3

    s = 1 / math.cos(foot)
    l1 = x * s
    l2 = x ** 3 * s / 6 * (psi + 2 * t * t)
    l3 = x ** 5 * s / 120 * (-4 * psi ** 3 * (1 - 6 * t * t)
                             + psi * psi * (9 - 68 * t * t)
                             + 72 * psi * t * t + 24 * t ** 4)
    lon = lon0 + l1 - l2 + l3
    return math.degrees(lat), math.degrees(lon)


# ------------------------------------------------------------------ api

GENERIC = {"mount", "mt", "the", "peak", "hill", "range", "point", "rock",
           "saddle", "spur", "knob", "top", "col", "pass"}


def name_score(peak_name, track_name):
    """
    How strongly a DOC track name points at this particular summit.

    "Mount Arthur" against "Mount Arthur Summit Route" scores; against
    "Ellis Basin Route" it does not. Generic words are ignored so that
    "Mount" alone never counts as a match.
    """
    if not track_name:
        return 0
    words = {w for w in peak_name.lower().replace("'", "").split()
             if w not in GENERIC and len(w) > 2}
    if not words:
        return 0
    low = track_name.lower()
    return sum(1 for w in words if w in low)


def duration_rank(text):
    """Roughly how long a DOC walk takes, for ordering. Bigger is longer."""
    if not text:
        return 999
    t = text.lower()
    if "day" in t:
        digits = "".join(c if c.isdigit() else " " for c in t).split()
        return 100 * (int(digits[0]) if digits else 2)
    hours = 0
    parts = "".join(c if (c.isdigit() or c == " ") else " " for c in t).split()
    if "hr" in t and parts:
        hours = int(parts[0])
    elif "min" in t and parts:
        hours = int(parts[0]) / 60
    return hours or 50


def load_key():
    path = os.path.join(HERE, "doc-api-key.local.json")
    if not os.path.exists(path):
        raise SystemExit(
            "No doc-api-key.local.json. Register free at api.doc.govt.nz, "
            "subscribe the key to v1-tracks and v2-alerts, then put it in "
            'that file as {"key": "..."}')
    return json.load(io.open(path, encoding="utf-8"))["key"]


def call(key, path, tries=3):
    req = urllib.request.Request(API + path, headers={
        "x-api-key": key, "User-Agent": UA, "Accept": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=90,
                                        context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise SystemExit(
                    "DOC returned Forbidden. The key exists but is probably "
                    "not subscribed. Open api.doc.govt.nz, go to APIs, and "
                    "click Subscribe on v1-tracks and v2-alerts.")
            if e.code == 429:
                time.sleep(10)
                continue
            if attempt == tries - 1:
                return None
        except Exception:
            if attempt == tries - 1:
                return None
            time.sleep(4)
    return None


# --------------------------------------------------------------------- main

def main():
    key = load_key()
    south, west, north, east = BBOX

    log("1. Every DOC track")
    tracks = call(key, "v1/tracks")
    log("   %d nationally" % len(tracks))

    log("")
    log("2. Converting geometry to degrees and keeping our region")
    local = []
    for t in tracks:
        pts = []
        for seg in (t.get("line") or []):
            for (e, n) in seg:
                try:
                    pts.append(nztm_to_wgs84(e, n))
                except Exception:
                    pass
        pts = [(la, lo) for (la, lo) in pts
               if south <= la <= north and west <= lo <= east]
        if pts:
            t["_pts"] = pts
            local.append(t)
    log("   %d tracks fall inside the region" % len(local))

    log("")
    log("3. Live alerts")
    alerts = call(key, "v1/tracks/alerts") or []
    by_asset = {a["assetId"]: a.get("alerts", []) for a in alerts
                if a.get("alerts")}
    log("   %d tracks nationally have an alert on them" % len(by_asset))

    # Load whichever peaks file exists, to know what we are matching against.
    peaks_file = os.path.join(DATA, "peaks.json")
    if not os.path.exists(peaks_file):
        peaks_file = os.path.join(DATA, "peaks.public.json")
    peaks = json.load(io.open(peaks_file, encoding="utf-8"))["peaks"]

    log("")
    log("4. Matching DOC tracks to summits")
    # Several DOC tracks can pass the same summit. Mount Arthur has both the
    # Summit Route, which is what you walk to climb it, and the two day Ellis
    # Basin Route. Picking whichever line happens to pass closest gets this
    # wrong, so candidates are gathered and then ranked.
    matches = {}
    for p in peaks:
        cands = []
        for t in local:
            d = min(haversine_m(p["lat"], p["lon"], la, lo)
                    for (la, lo) in t["_pts"])
            if d <= MATCH_RADIUS_M:
                cands.append((t, round(d)))
        if cands:
            cands.sort(key=lambda c: (-name_score(p["name"], c[0].get("name")),
                                      c[1]))
            matches[p["id"]] = cands[:3]
    log("   %d of %d peaks have at least one DOC track reaching them"
        % (len(matches), len(peaks)))

    log("")
    log("5. Fetching detail for the matched tracks")
    cache = json.load(io.open(CACHE, encoding="utf-8")) \
        if os.path.exists(CACHE) else {}
    wanted = {t["assetId"] for cands in matches.values() for (t, _) in cands}
    todo = [i for i in wanted if i not in cache]
    for i, asset in enumerate(todo, 1):
        d = call(key, "v1/tracks/%s/detail" % asset)
        if d:
            cache[asset] = {k: d.get(k) for k in
                            ("name", "walkDuration", "walkDurationCategory",
                             "walkTrackCategory", "distance", "introduction",
                             "staticLink", "locationString", "dogsAllowed",
                             "permittedActivities")}
        time.sleep(PAUSE)
        if i % 20 == 0 or i == len(todo):
            log("   %d / %d" % (i, len(todo)))
            io.open(CACHE, "w", encoding="utf-8").write(json.dumps(cache))
    io.open(CACHE, "w", encoding="utf-8").write(json.dumps(cache))

    log("")
    log("6. Writing into the data files")
    for name in ("peaks.json", "peaks.public.json"):
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            continue
        d = json.load(io.open(path, encoding="utf-8"))
        n = alerted = 0
        for p in d["peaks"]:
            cands = matches.get(p["id"])
            if not cands:
                p["doc"] = None
                continue

            def described(entry):
                track, gap = entry
                det = cache.get(track["assetId"], {})
                return {
                    "name": det.get("name") or track.get("name"),
                    "walk_duration": det.get("walkDuration"),
                    "walk_category": (det.get("walkTrackCategory") or [None])[0],
                    "distance": det.get("distance"),
                    "introduction": det.get("introduction"),
                    "location": det.get("locationString"),
                    "link": det.get("staticLink"),
                    "metres_from_summit": gap,
                    "alerts": [{"date": a.get("displayDate"),
                                "heading": a.get("heading"),
                                "detail": (a.get("detail") or "")[:600]}
                               for a in by_asset.get(track["assetId"], [])],
                }

            described_all = [described(c) for c in cands]
            # Now that durations are known, prefer a track that names this
            # summit; failing that, the shortest walk rather than the longest.
            # A multi-day traverse that happens to cross the top is not the
            # route you would walk to climb it.
            best_i = min(range(len(described_all)), key=lambda i: (
                -name_score(p["name"], described_all[i]["name"]),
                duration_rank(described_all[i]["walk_duration"]),
                described_all[i]["metres_from_summit"]))

            chosen = described_all[best_i]
            chosen["others"] = [x["name"] for i, x in enumerate(described_all)
                                if i != best_i]
            p["doc"] = chosen
            n += 1
            if chosen["alerts"]:
                alerted += 1
        d["sources"]["doc"] = ("Department of Conservation Tracks API, "
                               "api.doc.govt.nz, CC BY 4.0")
        d["doc_checked"] = time.strftime("%Y-%m-%d %H:%M")
        io.open(path, "w", encoding="utf-8").write(
            json.dumps(d, indent=1, ensure_ascii=False))
        log("   %s: %d peaks matched, %d carrying a live alert"
            % (name, n, alerted))


if __name__ == "__main__":
    main()
