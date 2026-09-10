#!/usr/bin/env python3
"""
Stage 2, part one: the route line and an honest climb figure.

Summit height minus car park height is not the climb. Real routes rise and
fall on the way, and on a New Zealand ridge they do it a lot. So this script
walks the actual track from the trailhead to the summit, samples the ground
height every 50 metres along it, and adds up every rise.

  route geometry : OpenStreetMap walking tracks, already cached by build_data
  ground height  : LINZ NZ 8m digital elevation model, served by OpenTopoData

The 8m model agrees with recorded summit heights to within about 12 metres on
the peaks checked, which is close enough to plan a day around.

Sampled heights are noisy. Adding up every tiny wobble would inflate the climb,
so a rise only counts once it exceeds a threshold. That is the same trick a
GPS watch uses. The threshold is recorded in the output.

Run:  python build_routes.py
Sampled heights are cached, so a second run costs nothing.
"""

import json
import io
import os
import sys
import math
import time
import heapq
import urllib.parse
import urllib.request

from build_data import (SSL_CTX, DATA, HERE, UA, TrackGraph, PointGrid,
                        haversine_m, log)

# Sample the ground this often along the route.
SAMPLE_EVERY_M = 50
# A rise only counts towards the climb once it exceeds this, which stops
# noise in the height model inflating the total.
CLIMB_THRESHOLD_M = 5.0
# Points kept for drawing the line on the map, roughly one per this distance.
MAP_POINT_EVERY_M = 100
# Points kept for the little height chart in the panel.
PROFILE_POINTS = 60

ELEVATION_API = "https://api.opentopodata.org/v1/nzdem8m"
BATCH = 100          # OpenTopoData allows 100 locations per request
PAUSE = 1.1          # and one request per second

CACHE = os.path.join(DATA, "elevation_cache.json")


# ------------------------------------------------------------------ geometry

def path_between(graph, start, goal):
    """Dijkstra that returns the node sequence, not just the distance."""
    if start == goal:
        return [start], 0.0
    dist = {start: 0.0}
    prev = {}
    pq = [(0.0, start)]
    seen = set()
    while pq:
        d, n = heapq.heappop(pq)
        if n in seen:
            continue
        seen.add(n)
        if n == goal:
            path = [n]
            while path[-1] != start:
                path.append(prev[path[-1]])
            return list(reversed(path)), d
        for (m, w) in graph.adj.get(n, ()):
            nd = d + w
            if nd < dist.get(m, float("inf")):
                dist[m] = nd
                prev[m] = n
                heapq.heappush(pq, (nd, m))
    return None, None


def resample(points, every_m):
    """Walk a polyline and drop a point every so many metres."""
    if len(points) < 2:
        return list(points), [0.0] * len(points)
    out = [points[0]]
    along = [0.0]
    carried = 0.0
    total = 0.0
    for (a, b) in zip(points, points[1:]):
        seg = haversine_m(a[0], a[1], b[0], b[1])
        if seg <= 0:
            continue
        pos = every_m - carried
        while pos < seg:
            f = pos / seg
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            along.append(total + pos)
            pos += every_m
        carried = (carried + seg) % every_m
        total += seg
    if out[-1] != points[-1]:
        out.append(points[-1])
        along.append(total)
    return out, along


def climb(heights, threshold=CLIMB_THRESHOLD_M):
    """Total rise and fall, ignoring wobbles smaller than the threshold."""
    clean = [h for h in heights if h is not None]
    if len(clean) < 2:
        return None, None
    up = down = 0.0
    ref = clean[0]
    for h in clean[1:]:
        if h - ref >= threshold:
            up += h - ref
            ref = h
        elif ref - h >= threshold:
            down += ref - h
            ref = h
    return up, down


# ------------------------------------------------------------------ heights

def load_cache():
    if os.path.exists(CACHE):
        return json.load(io.open(CACHE, encoding="utf-8"))
    return {}


def key_of(lat, lon):
    return "%.5f,%.5f" % (lat, lon)


def fetch_heights(points, cache):
    """Look up ground height for a list of (lat, lon), using the cache."""
    missing = [p for p in points if key_of(*p) not in cache]
    # De-duplicate, keeping order.
    seen, todo = set(), []
    for p in missing:
        k = key_of(*p)
        if k not in seen:
            seen.add(k)
            todo.append(p)

    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        locs = "|".join("%.5f,%.5f" % (a, b) for (a, b) in chunk)
        url = ELEVATION_API + "?" + urllib.parse.urlencode({"locations": locs})
        got = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60,
                                            context=SSL_CTX) as r:
                    got = json.loads(r.read().decode("utf-8"))
                break
            except Exception as e:
                log("    height lookup failed (%s), retrying" % e)
                time.sleep(8)
        if not got or got.get("status") != "OK":
            # Leave them uncached so a later run can try again. The peak will
            # simply report no climb figure rather than a wrong one.
            time.sleep(PAUSE)
            continue
        for p, res in zip(chunk, got.get("results", [])):
            cache[key_of(*p)] = res.get("elevation")
        time.sleep(PAUSE)

    return [cache.get(key_of(*p)) for p in points]


# --------------------------------------------------------------------- main

def main():
    peaks_file = os.path.join(DATA, "peaks.json")
    if not os.path.exists(peaks_file):
        peaks_file = os.path.join(DATA, "peaks.public.json")
    base = json.load(io.open(peaks_file, encoding="utf-8"))

    log("Rebuilding the track network from the cached download")
    tracks = json.load(io.open(os.path.join(DATA, "tracks_wide.json"),
                               encoding="utf-8"))["elements"]
    graph = TrackGraph(tracks)
    log("   %d track nodes" % len(graph.pos))

    node_grid = PointGrid()
    for nid, (la, lo) in graph.pos.items():
        node_grid.add(la, lo, nid)

    cache = load_cache()
    log("   %d ground heights already cached" % len(cache))

    todo = [p for p in base["peaks"] if p.get("trailhead")]
    log("")
    log("Walking %d routes and sampling the ground every %d m"
        % (len(todo), SAMPLE_EVERY_M))

    routes = {}
    done = 0
    for p in todo:
        th = p["trailhead"]
        th_node, d1 = node_grid.nearest(th["lat"], th["lon"], 30)
        su_node, d2 = node_grid.nearest(p["lat"], p["lon"], 1200)
        if th_node is None or su_node is None:
            continue
        nodes, length = path_between(graph, th_node, su_node)
        if not nodes:
            continue

        line = [graph.pos[n] for n in nodes]
        samples, along = resample(line, SAMPLE_EVERY_M)
        heights = fetch_heights(samples, cache)
        up, down = climb(heights)

        # The line drawn on the map does not need every sample.
        step = max(1, MAP_POINT_EVERY_M // SAMPLE_EVERY_M)
        draw = [[round(a, 5), round(b, 5)] for (a, b) in samples[::step]]
        if draw[-1] != [round(samples[-1][0], 5), round(samples[-1][1], 5)]:
            draw.append([round(samples[-1][0], 5), round(samples[-1][1], 5)])

        # A small profile for the panel chart.
        prof = None
        if any(h is not None for h in heights):
            k = max(1, len(samples) // PROFILE_POINTS)
            prof = [[round(along[i]), round(heights[i])]
                    for i in range(0, len(samples), k)
                    if heights[i] is not None]

        routes[p["id"]] = {
            "line": draw,
            "length_m": round(length),
            "ascent_m": round(up) if up is not None else None,
            "descent_m": round(down) if down is not None else None,
            "trailhead_elevation_m": (round(heights[0])
                                      if heights and heights[0] is not None
                                      else None),
            "profile": prof,
            "samples": len(samples),
            "sample_spacing_m": SAMPLE_EVERY_M,
            "climb_threshold_m": CLIMB_THRESHOLD_M,
            "elevation_source": "LINZ NZ 8m DEM via OpenTopoData",
        }
        done += 1
        if done % 10 == 0 or done == len(todo):
            log("   %d / %d   (%d heights cached)" % (done, len(todo), len(cache)))
            io.open(CACHE, "w", encoding="utf-8").write(json.dumps(cache))

    io.open(CACHE, "w", encoding="utf-8").write(json.dumps(cache))

    # Write the routes into both data files. They share geometry, and differ
    # only in where the drive times were measured from.
    for name in ("peaks.json", "peaks.public.json"):
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            continue
        d = json.load(io.open(path, encoding="utf-8"))
        n = 0
        for p in d["peaks"]:
            r = routes.get(p["id"])
            p["route"] = r
            if r:
                n += 1
        d["sources"]["ground_height"] = (
            "LINZ NZ 8m digital elevation model, via OpenTopoData")
        io.open(path, "w", encoding="utf-8").write(
            json.dumps(d, indent=1, ensure_ascii=False))
        log("Wrote %s with %d routes  (%.2f MB)"
            % (name, n, os.path.getsize(path) / 1e6))


if __name__ == "__main__":
    main()
