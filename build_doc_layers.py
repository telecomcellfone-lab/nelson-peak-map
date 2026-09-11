#!/usr/bin/env python3
"""
Build the DOC huts and tracks map layers.

build_doc.py attaches DOC information to individual summits. This script is
different: it pulls every DOC hut and every DOC track in the region and writes
them as map layers, so the map can show the whole official network rather than
only the bits that touch a peak.

Output is data/doc_layers.json, two GeoJSON collections:

  huts    186 or so, with bunks, category, facilities, booking and open or
          closed status
  tracks  the DOC track network, with their walking time, distance and grading

Needs the same DOC key as build_doc.py.

Run:  python build_doc_layers.py
Detail lookups are cached, so a second run is quick.
"""

import json
import io
import os
import time

from build_data import DATA, BBOX, log
from build_doc import nztm_to_wgs84, load_key, call, PAUSE

HUT_CACHE = os.path.join(DATA, "doc_hut_cache.json")
TRACK_CACHE = os.path.join(DATA, "doc_cache.json")
OUT = os.path.join(DATA, "doc_layers.json")


def in_region(lat, lon):
    south, west, north, east = BBOX
    return south <= lat <= north and west <= lon <= east


def load(path):
    return json.load(io.open(path, encoding="utf-8")) \
        if os.path.exists(path) else {}


def save(path, obj):
    io.open(path, "w", encoding="utf-8").write(json.dumps(obj))


def fetch_details(key, assets, cache, path, kind, fields):
    todo = [a for a in assets if a not in cache]
    for i, asset in enumerate(todo, 1):
        d = call(key, "%s/%s/detail" % (kind, asset))
        if d:
            cache[asset] = {f: d.get(f) for f in fields}
        time.sleep(PAUSE)
        if i % 25 == 0 or i == len(todo):
            log("   %d / %d" % (i, len(todo)))
            save(path, cache)
    save(path, cache)
    return cache


def main():
    key = load_key()

    # ------------------------------------------------------------- huts
    log("1. Huts")
    huts = call(key, "v2/huts") or []
    local = []
    for h in huts:
        try:
            lat, lon = nztm_to_wgs84(h["x"], h["y"])
        except Exception:
            continue
        if in_region(lat, lon):
            h["_lat"], h["_lon"] = lat, lon
            local.append(h)
    log("   %d nationally, %d in the region" % (len(huts), len(local)))

    log("   fetching hut detail")
    hut_cache = fetch_details(
        key, [h["assetId"] for h in local], load(HUT_CACHE), HUT_CACHE,
        "v2/huts",
        ("name", "status", "numberOfBunks", "hutCategory", "bookable",
         "facilities", "introduction", "staticLink", "locationString",
         "proximityToRoadEnd", "place"))

    hut_features = []
    for h in local:
        d = hut_cache.get(h["assetId"], {})
        hut_features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(h["_lon"], 6),
                                         round(h["_lat"], 6)]},
            "properties": {
                "name": d.get("name") or h.get("name"),
                "status": d.get("status") or h.get("status"),
                "bunks": d.get("numberOfBunks"),
                "category": d.get("hutCategory"),
                "bookable": d.get("bookable"),
                "facilities": d.get("facilities") or [],
                "place": d.get("place") or d.get("locationString"),
                "road_end": d.get("proximityToRoadEnd"),
                "introduction": d.get("introduction"),
                "link": d.get("staticLink"),
            },
        })

    # ----------------------------------------------------------- tracks
    log("")
    log("2. Tracks")
    tracks = call(key, "v1/tracks") or []
    local_tracks = []
    for t in tracks:
        lines = []
        for seg in (t.get("line") or []):
            pts = []
            for (e, n) in seg:
                try:
                    lat, lon = nztm_to_wgs84(e, n)
                except Exception:
                    continue
                pts.append([round(lon, 6), round(lat, 6)])
            if len(pts) > 1:
                lines.append(pts)
        if not lines:
            continue
        if not any(in_region(p[1], p[0]) for seg in lines for p in seg):
            continue
        t["_lines"] = lines
        local_tracks.append(t)
    log("   %d nationally, %d in the region"
        % (len(tracks), len(local_tracks)))

    log("   fetching track detail")
    track_cache = fetch_details(
        key, [t["assetId"] for t in local_tracks], load(TRACK_CACHE),
        TRACK_CACHE, "v1/tracks",
        ("name", "walkDuration", "walkDurationCategory", "walkTrackCategory",
         "distance", "introduction", "staticLink", "locationString",
         "dogsAllowed", "permittedActivities"))

    log("   fetching alerts")
    alerts = call(key, "v1/tracks/alerts") or []
    by_asset = {a["assetId"]: a.get("alerts", []) for a in alerts
                if a.get("alerts")}

    track_features = []
    for t in local_tracks:
        d = track_cache.get(t["assetId"], {})
        live = by_asset.get(t["assetId"], [])
        track_features.append({
            "type": "Feature",
            "geometry": {"type": "MultiLineString", "coordinates": t["_lines"]},
            "properties": {
                "name": d.get("name") or t.get("name"),
                "duration": d.get("walkDuration"),
                "grade": (d.get("walkTrackCategory") or [None])[0],
                "distance": d.get("distance"),
                "place": d.get("locationString"),
                "introduction": d.get("introduction"),
                "link": d.get("staticLink"),
                "alert": live[0].get("heading") if live else None,
                "alert_count": len(live),
            },
        })

    out = {
        "built": time.strftime("%Y-%m-%d %H:%M"),
        "source": ("Department of Conservation, api.doc.govt.nz, CC BY 4.0. "
                   "Geometry converted from the New Zealand map grid."),
        "huts": {"type": "FeatureCollection", "features": hut_features},
        "tracks": {"type": "FeatureCollection", "features": track_features},
    }
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False))
    log("")
    log("Wrote %s  (%d huts, %d tracks, %.2f MB)"
        % (OUT, len(hut_features), len(track_features),
           os.path.getsize(OUT) / 1e6))


if __name__ == "__main__":
    main()
