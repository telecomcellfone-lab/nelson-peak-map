#!/usr/bin/env python3
"""
Build the peak dataset for the Nelson region peak map.

Stage 1: peaks, the track that reaches them, the trailhead you drive to,
the drive time from home, and whether the summit sits on public land.

Data sources. Every one is recorded in the output so nothing in the app is
an unattributed claim.

  peaks, tracks, roads : OpenStreetMap via the Overpass API      (ODbL)
  public access        : Herenga a Nuku Aotearoa, Public Access
                         Areas map service                       (CC BY 3.0 NZ)
  drive time           : OSRM public routing over OSM roads      (ODbL)

Rules this script follows:
  * It never invents a geographic fact. If a value cannot be established it
    is written as null and the app says so rather than guessing.
  * Elevation gain is deliberately NOT calculated here. Summit height minus
    trailhead height is not the climb, because real routes rise and fall.
    That belongs in stage 2, sampled along the actual route.
  * Home coordinates live in home.local.json, which is never written into
    the shared data file.

Run:  python build_data.py            (uses cached downloads if present)
      python build_data.py --refresh  (re-downloads everything)
"""

import json
import io
import os
import ssl
import sys
import math
import time
import heapq
import urllib.parse
import urllib.request

# Windows ships an old root certificate list, which makes Python reject some
# perfectly good servers with "certificate has expired". certifi carries an
# up to date list. We use it when it is available rather than turning
# certificate checking off.
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# Region covered: Nelson, Tasman, Golden Bay, Nelson Lakes, Mt Richmond, and
# far enough south east to pick up the Kaikoura ranges.
BBOX = (-42.35, 171.85, -40.40, 174.35)   # south, west, north, east

# A track passing within this distance of a summit counts as reaching it.
SUMMIT_RADIUS_M = 1000
# A track node this close to a drivable road counts as a road access point.
ROAD_SNAP_MAX_M = 250

OVERPASS = "https://overpass-api.de/api/interpreter"
OSRM = "https://routing.openstreetmap.de/routed-car"
ACCESS_SERVICE = ("https://maps.herengaanuku.govt.nz/maps/rest/services"
                  "/Public_Access_Areas/MapServer/identify")

UA = "nelson-peak-map/0.1 (personal, non-commercial hiking planner)"

# Stand-in used for the published copy and whenever home.local.json is
# missing. Waimea College is a public landmark in Richmond, so it gives away
# nothing, and it is close enough to real Richmond addresses that the drive
# times stay accurate to a minute or so.
DEFAULT_HOME = {"name": "Waimea College, Richmond",
                "lat": -41.338473, "lon": 173.197641}


# ---------------------------------------------------------------- helpers

def log(*a):
    print(*a, flush=True)


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def overpass(query, cache_name, refresh=False):
    path = os.path.join(DATA, cache_name)
    if os.path.exists(path) and not refresh and os.path.getsize(path) > 10000:
        log("  using cached", cache_name)
        return json.load(io.open(path, encoding="utf-8"))["elements"]
    log("  downloading", cache_name, "...")
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(OVERPASS, data=body,
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300, context=SSL_CTX) as r:
                raw = r.read().decode("utf-8")
            if not raw.lstrip().startswith("{"):
                raise ValueError("Overpass returned an error page, it is busy")
            io.open(path, "w", encoding="utf-8").write(raw)
            return json.loads(raw)["elements"]
        except Exception as e:
            log("    attempt", attempt + 1, "failed:", e)
            time.sleep(20)
    raise SystemExit("Overpass failed three times. Try again later.")


def overpass_tiled(query_template, cache_name, refresh=False, rows=4, cols=4):
    """
    Fetch a whole-region query in tiles and join the results.

    Overpass times out on a single region-wide request for the track network,
    which is why an earlier version only pulled tracks within 4 km of each
    peak. That quietly truncated long approach tracks: Mount Fishtail's network
    stopped dead 4.2 km from the summit, exactly at the edge of the pull, so no
    road connection could ever be found. Tiling gets the whole network.
    """
    path = os.path.join(DATA, cache_name)
    if os.path.exists(path) and not refresh and os.path.getsize(path) > 10000:
        log("  using cached", cache_name)
        return json.load(io.open(path, encoding="utf-8"))["elements"]

    south, west, north, east = BBOX
    dy = (north - south) / rows
    dx = (east - west) / cols
    seen, elements = set(), []
    for r in range(rows):
        for c in range(cols):
            box = "%f,%f,%f,%f" % (south + r * dy, west + c * dx,
                                   south + (r + 1) * dy, west + (c + 1) * dx)
            log("  tile %d/%d" % (r * cols + c + 1, rows * cols))
            part = overpass(query_template % box,
                            "%s.tile%02d.json" % (cache_name, r * cols + c),
                            refresh)
            for e in part:
                if e["id"] not in seen:
                    seen.add(e["id"])
                    elements.append(e)

    io.open(path, "w", encoding="utf-8").write(
        json.dumps({"elements": elements}, ensure_ascii=False))
    log("  joined %d unique ways into %s" % (len(elements), cache_name))
    return elements


def parse_ele(tags):
    """OSM elevation tags are free text. Return metres, or None."""
    v = tags.get("ele")
    if v is None:
        return None
    try:
        return float(str(v).split()[0].replace(",", ""))
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- home

def load_home():
    path = os.path.join(HERE, "home.local.json")
    if "--public" in sys.argv:
        log("Home: --public, so home.local.json is ignored on purpose and")
        log("      %s is used instead. Nothing about" % DEFAULT_HOME["name"])
        log("      your house goes into the file this run produces.")
        return dict(DEFAULT_HOME), False
    if os.path.exists(path):
        h = json.load(io.open(path, encoding="utf-8"))
        log("Home: using your home.local.json")
        return h, True
    log("Home: home.local.json not found, using %s" % DEFAULT_HOME["name"])
    log("      as a placeholder.")
    return dict(DEFAULT_HOME), False


# ---------------------------------------------------------------- spatial

class PointGrid:
    """Coarse lat/lon bucket index for nearest point lookups."""

    CELL = 0.01   # about 1.1 km north to south, 0.84 km east to west here

    def __init__(self):
        self.cells = {}

    def add(self, lat, lon, payload):
        k = (int(lat / self.CELL), int(lon / self.CELL))
        self.cells.setdefault(k, []).append((lat, lon, payload))

    def nearest(self, lat, lon, max_m, rings=2):
        """Return (payload, metres). payload is None if nothing is in range."""
        k = (int(lat / self.CELL), int(lon / self.CELL))
        # Fast reject. Most alpine track nodes have no road anywhere near, so
        # checking for an occupied cell first saves an enormous amount of work.
        buckets = [self.cells.get((k[0] + dy, k[1] + dx))
                   for dy in range(-rings, rings + 1)
                   for dx in range(-rings, rings + 1)]
        buckets = [b for b in buckets if b]
        if not buckets:
            return None, float("inf")
        best_payload, best_d = None, float("inf")
        for b in buckets:
            for (la, lo, payload) in b:
                d = haversine_m(lat, lon, la, lo)
                if d < best_d:
                    best_payload, best_d = payload, d
        if best_d > max_m:
            return None, best_d
        return best_payload, best_d


# ---------------------------------------------------------------- graph

class TrackGraph:
    """Undirected graph of the walking track network, keyed by OSM node id."""

    def __init__(self, ways):
        self.pos = {}          # node id -> (lat, lon)
        self.adj = {}          # node id -> list of (neighbour id, metres)
        self.way_of = {}       # node id -> a way id that contains it
        self.ways = {w["id"]: w for w in ways}

        for w in ways:
            nodes = w.get("nodes") or []
            geom = w.get("geometry") or []
            if len(nodes) != len(geom):
                continue
            for nid, g in zip(nodes, geom):
                self.pos[nid] = (g["lat"], g["lon"])
                self.way_of.setdefault(nid, w["id"])
            for a, b in zip(nodes, nodes[1:]):
                if a not in self.pos or b not in self.pos:
                    continue
                d = haversine_m(self.pos[a][0], self.pos[a][1],
                                self.pos[b][0], self.pos[b][1])
                self.adj.setdefault(a, []).append((b, d))
                self.adj.setdefault(b, []).append((a, d))

        self.component = self._components()
        self.by_component = {}
        for n, c in self.component.items():
            self.by_component.setdefault(c, []).append(n)

    def _components(self):
        comp = {}
        cid = 0
        for start in self.pos:
            if start in comp:
                continue
            cid += 1
            stack = [start]
            comp[start] = cid
            while stack:
                n = stack.pop()
                for (m, _) in self.adj.get(n, ()):
                    if m not in comp:
                        comp[m] = cid
                        stack.append(m)
        return comp

    def shortest_path_m(self, start, goal):
        """Dijkstra along the track network. Returns metres, or None."""
        if start == goal:
            return 0.0
        dist = {start: 0.0}
        pq = [(0.0, start)]
        while pq:
            d, n = heapq.heappop(pq)
            if n == goal:
                return d
            if d > dist.get(n, float("inf")):
                continue
            for (m, w) in self.adj.get(n, ()):
                nd = d + w
                if nd < dist.get(m, float("inf")):
                    dist[m] = nd
                    heapq.heappush(pq, (nd, m))
        return None


# ---------------------------------------------------------------- access

STRONG_ACCESS_LAYERS = ("Public Access Conservation Land", "Reserve Land",
                        "Other Parks & Reserves", "Other Public Access Areas")


def public_access(lat, lon):
    """Ask Herenga a Nuku what the summit point sits on."""
    d = 0.0005
    params = {
        "geometry": "%f,%f" % (lon, lat),
        "geometryType": "esriGeometryPoint",
        "sr": "4326",
        "layers": "all:1,2,3,4,5,6",
        "tolerance": "1",
        "mapExtent": "%f,%f,%f,%f" % (lon - d, lat - d, lon + d, lat + d),
        "imageDisplay": "400,400,96",
        "returnGeometry": "false",
        "f": "json",
    }
    url = ACCESS_SERVICE + "?" + urllib.parse.urlencode(params)
    try:
        res = get(url, timeout=45).get("results", [])
    except Exception:
        return None      # null means we could not check, not "no access"

    layers, detail = [], None
    for r in res:
        name = r.get("layerName")
        if name and name not in layers:
            layers.append(name)
        attrs = r.get("attributes") or {}
        if detail is None:
            for k in ("statutory_actions", "common_name", "paa_class",
                      "managed_by"):
                v = attrs.get(k)
                if v and str(v).strip() not in ("", "Null", "null"):
                    detail = str(v)[:200]
                    break

    if any(l in STRONG_ACCESS_LAYERS for l in layers):
        status = "public"
    elif "Road Parcels" in layers or "Easements" in layers:
        status = "road_or_easement"
    else:
        status = "not_shown"
    return {"status": status, "layers": layers, "detail": detail}


# ---------------------------------------------------------------- driving

def drive(home, lat, lon):
    """Drive time and distance from home to a point, snapped to the roads."""
    url = ("%s/route/v1/driving/%f,%f;%f,%f?overview=false"
           % (OSRM, home["lon"], home["lat"], lon, lat))
    try:
        d = get(url, timeout=45)
    except Exception:
        return None
    if d.get("code") != "Ok" or not d.get("routes"):
        return None
    r = d["routes"][0]
    wp = (d.get("waypoints") or [{}, {}])[-1]
    return {
        "seconds": round(r["duration"]),
        "metres": round(r["distance"]),
        "snap_m": round(wp.get("distance", 0)),
        "snapped_to": wp.get("name") or None,
    }


# ---------------------------------------------------------------- main

def main():
    refresh = "--refresh" in sys.argv
    os.makedirs(DATA, exist_ok=True)
    home, home_is_real = load_home()

    south, west, north, east = BBOX
    box = "%f,%f,%f,%f" % (south, west, north, east)

    log("")
    log("1. Peaks")
    peaks_raw = overpass(
        '[out:json][timeout:180];node["natural"="peak"]["name"]["ele"](%s);'
        'out body;' % box, "peaks_raw.json", refresh)
    log("   %d named peaks with a recorded height" % len(peaks_raw))

    log("")
    log("2. Every walking track in the region")
    tracks_raw = overpass_tiled(
        '[out:json][timeout:280];'
        'way["highway"~"^(path|footway|track|steps|bridleway)$"](%s);'
        'out geom;', "tracks_all.json", refresh)
    log("   %d track ways" % len(tracks_raw))

    log("")
    log("3. Drivable roads")
    roads_raw = overpass(
        '[out:json][timeout:280];'
        'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|'
        'unclassified|residential|service|motorway_link|trunk_link|'
        'primary_link|secondary_link)$"](%s);out geom;' % box,
        "roads_raw.json", refresh)
    log("   %d road ways" % len(roads_raw))

    log("")
    log("4. Building the track network")
    graph = TrackGraph(tracks_raw)
    log("   %d track nodes in %d separate networks"
        % (len(graph.pos), len(graph.by_component)))

    log("   indexing roads")
    road_grid = PointGrid()
    for w in roads_raw:
        nm = w.get("tags", {}).get("name")
        for g in w.get("geometry", ()):
            road_grid.add(g["lat"], g["lon"], nm or "unnamed road")

    log("   indexing tracks")
    track_grid = PointGrid()
    for w in tracks_raw:
        nodes = w.get("nodes") or []
        geom = w.get("geometry") or []
        if len(nodes) != len(geom):
            continue
        for nid, g in zip(nodes, geom):
            track_grid.add(g["lat"], g["lon"], nid)

    log("")
    log("5. Matching peaks to tracks")
    candidates = []
    for p in peaks_raw:
        ele = parse_ele(p["tags"])
        if ele is None:
            continue
        nid, gap = track_grid.nearest(p["lat"], p["lon"], SUMMIT_RADIUS_M)
        if nid is None:
            continue
        candidates.append((p, ele, nid, gap))
    log("   %d peaks have a walking track within %d m of the summit"
        % (len(candidates), SUMMIT_RADIUS_M))

    log("")
    log("6. Finding road access points for each track network")
    comp_access = {}
    for (p, ele, nid, gap) in candidates:
        cid = graph.component.get(nid)
        if cid is None or cid in comp_access:
            continue
        found = []
        for n in graph.by_component.get(cid, ()):
            la, lo = graph.pos[n]
            # rings=1 spans about 840 m east to west, well beyond the 250 m
            # we care about, so one ring is enough and much faster.
            road_name, road_d = road_grid.nearest(la, lo, ROAD_SNAP_MAX_M,
                                                  rings=1)
            if road_name is not None:
                found.append((n, road_d, road_name))
        comp_access[cid] = found
    reachable = sum(1 for v in comp_access.values() if v)
    log("   %d networks examined, %d of them touch a road"
        % (len(comp_access), reachable))

    log("")
    log("7. Drive times and public access. This is the slow part.")
    out = []
    ordered = sorted(candidates, key=lambda c: -c[1])
    for i, (p, ele, nid, summit_gap) in enumerate(ordered, 1):
        cid = graph.component.get(nid)
        access_nodes = comp_access.get(cid, [])

        trailhead, track_m = None, None
        if access_nodes:
            # The road access point nearest the mountain is the trailhead.
            th_node, road_d, road_name = min(
                access_nodes,
                key=lambda a: haversine_m(p["lat"], p["lon"],
                                          graph.pos[a[0]][0], graph.pos[a[0]][1]))
            tla, tlo = graph.pos[th_node]
            trailhead = {
                "lat": round(tla, 6),
                "lon": round(tlo, 6),
                "road": road_name,
                "metres_from_road": round(road_d),
            }
            track_m = graph.shortest_path_m(th_node, nid)

        way = graph.ways.get(graph.way_of.get(nid))
        wt = (way or {}).get("tags", {})

        rec = {
            "id": p["id"],
            "name": p["tags"]["name"],
            "lat": round(p["lat"], 6),
            "lon": round(p["lon"], 6),
            "elevation_m": round(ele),
            "track": {
                "name": wt.get("name"),
                "grade": wt.get("sac_scale"),
                "metres_from_summit": round(summit_gap),
                "distance_from_trailhead_m": (round(track_m)
                                              if track_m is not None else None),
            },
            "trailhead": trailhead,
            "drive": drive(home, trailhead["lat"], trailhead["lon"]) if trailhead else None,
            "access": None,
        }
        if trailhead:
            time.sleep(0.3)

        rec["access"] = public_access(p["lat"], p["lon"])
        time.sleep(0.2)

        out.append(rec)
        if i % 10 == 0 or i == len(ordered):
            log("   %d / %d" % (i, len(ordered)))

    payload = {
        "built": time.strftime("%Y-%m-%d %H:%M"),
        "home_is_placeholder": not home_is_real,
        "home_label": None if home_is_real else DEFAULT_HOME["name"],
        "region": {"south": south, "west": west, "north": north, "east": east},
        "counts": {
            "named_peaks_in_region": len(peaks_raw),
            "peaks_with_a_track": len(out),
        },
        "sources": {
            "peaks_tracks_roads": "OpenStreetMap contributors, ODbL, via Overpass",
            "public_access": "Herenga a Nuku Aotearoa, Public Access Areas, CC BY 3.0 NZ",
            "drive_time": "OSRM public routing over OpenStreetMap roads",
            "basemap": "LINZ Topo50 raster basemap, CC BY 4.0",
        },
        "peaks": out,
    }
    # Two data files, on purpose.
    #   peaks.json         drive times from your own front door. Stays on this
    #                      machine, and is git-ignored.
    #   peaks.public.json  drive times from Nelson city centre. Safe to publish,
    #                      because nothing in it points at your house.
    # The page loads the private one when it is there and falls back to the
    # public one, so the same index.html works locally and on the web.
    name = "peaks.public.json" if "--public" in sys.argv else "peaks.json"
    path = os.path.join(DATA, name)
    io.open(path, "w", encoding="utf-8").write(
        json.dumps(payload, indent=1, ensure_ascii=False))
    log("")
    log("Wrote %s  (%.2f MB)" % (path, os.path.getsize(path) / 1e6))

    # Hand the home point to the page in its own file, so the page can show
    # distance and bearing from home without the coordinates ever being
    # written into peaks.json. Both this file and home.local.json are ignored
    # by git, so peaks.json stays safe to share.
    home_out = os.path.join(DATA, "home.local.json")
    if home_is_real:
        io.open(home_out, "w", encoding="utf-8").write(json.dumps(
            {"lat": home["lat"], "lon": home["lon"],
             "name": home.get("name", "home")}, indent=1))
        log("Wrote %s  (private, git-ignored)" % home_out)
    elif "--public" in sys.argv:
        # A public build uses a stand-in on purpose. It must not disturb the
        # private home file, which belongs to the local copy of the map.
        pass
    elif os.path.exists(home_out):
        # No home.local.json any more, so clear the stale pointer.
        os.remove(home_out)


if __name__ == "__main__":
    main()
