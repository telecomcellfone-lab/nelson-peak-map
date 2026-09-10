# Nelson Peak Map

An interactive LINZ topo map of the peaks you can reach from home, with the
drive, the track in, and whether the summit is on public land.

## How to open it

From this folder:

```
python -m http.server 8000
```

Then go to <http://127.0.0.1:8000> in your browser.

Opening `index.html` by double-clicking will not work. Browsers block a local
page from reading a local data file. The little server above gets around that.

## What stage 1 does

* LINZ Topo50 raster basemap, with an aerial photo toggle.
* 209 peaks that have a walking track within 1 km of the summit, out of 683
  named peaks in the region.
* Drive time and distance to the road end for each one.
* Track distance from the road end to the summit, measured along the actual
  track, not as the crow flies.
* Public access status from Herenga a Nuku, the official access map.
* A slider to hide anything further than a given drive.

## What stage 2 adds

* **The route drawn on the map**, following the actual mapped track from the
  road end to the summit.
* **A real climb figure.** The build script walks that route, reads the ground
  height every 50 m from the LINZ 8 m height model, and adds up every rise.
  Mount Arthur comes out at 892 m of climb from a road end at 958 m, which
  matches the guidebooks. Summit height minus car park height would have said
  822 m and missed the dips.
* **A height profile** for each route, drawn in the panel.
* **Summit weather**, forecast for the summit's own height rather than the
  nearest town. Temperature, rain and wind come from ECMWF IFS. Freezing level
  comes from Open-Meteo's multi-model blend, because ECMWF does not publish it
  through this service. Both are labelled.
* **Daylight**, with sunrise and sunset for that exact summit, and the latest
  start that still gets you back to the car before dark.
* A link straight to the yr.no page for the summit coordinates.

The climb threshold matters. Height models are noisy, and adding up every tiny
wobble inflates the total badly. A rise only counts once it exceeds 5 m, which
is the same trick a GPS watch uses. The threshold is recorded in the data.

The walking time is **Naismith's rule**, not a measurement: an hour per 5 km
plus an hour per 600 m of climb, for the round trip. It assumes a fit walker
and no stops. The app says so on screen.

## What stage 3 adds so far

* **Official DOC track information** for the 68 peaks a DOC track reaches.
  Their walking time, their distance, their grading, and a link to the DOC
  page. Where DOC gives a time it is shown as the one to trust and mine is
  demoted to a second opinion. For Mount Arthur DOC says 3 hr 30 min to
  4 hr 30 min one way. Naismith put the whole return trip at 4 hr 46 min,
  which is badly optimistic for New Zealand alpine ground.
* **Live DOC alerts**, shown above everything else on the panel. 32 peaks
  currently carry one, including "Limited access to Flora car park up Graham
  Valley Road", which is the road you drive to climb Mount Arthur.
* Where several DOC tracks pass a summit, the app names the alternatives and
  says which one it picked and why.

Matching a DOC track to a summit is not just "nearest line". Mount Arthur has
both the Summit Route and the two day Ellis Basin Route passing close by. The
build ranks candidates by whether the track name mentions the peak, then by
how short the walk is, so a multi-day traverse that happens to cross the top
does not get presented as the way up.

DOC publishes track geometry on the New Zealand map grid rather than in
degrees, so `build_doc.py` carries the conversion. It is checked against a
known point: the Mount Arthur Summit Route ends 60 m from the recorded summit
and starts at the Flora car park.

## What it deliberately does not do yet

**Trip reports and the full filter bar.** Still to come.

## Keys

Three, all free, none of them in the repository.

| File | What for | How to get it |
|---|---|---|
| `doc-api-key.local.json` | DOC tracks and alerts | Register at api.doc.govt.nz, then subscribe the key to v1-tracks and v2-alerts |
| `linz-api-key.local.json` | LINZ Data Service | data.linz.govt.nz, your name, API Keys, Create |
| in `index.html` | LINZ Basemaps tiles | The shared key from LINZ's docs, until a developer key arrives |

A DOC key that exists but is not subscribed returns a bare "Forbidden" with no
explanation. That is the usual cause.

The LINZ Data Service key also serves Topo50 tiles, and those do not expire.
The local copy uses it through `data/linz-key.local.json`. The **published**
copy deliberately does not: a key sitting in a public web page can be lifted
and spent by anyone, and that key is tied to your account. The published page
stays on the shared key until LINZ issue a site-restricted developer key.

## Two data files, and why

| File | Measured from | Published? |
|---|---|---|
| `data/peaks.json` | your own front door | no, git-ignored |
| `data/peaks.public.json` | Waimea College, Richmond | yes |

The page loads the private one when it is there and falls back to the public
one. The same `index.html` therefore works both on this machine and on the web.

Waimea College is 610 m from the house, so the published drive times differ from
the real ones by 42 seconds at the median and 77 seconds at worst. Close enough
to plan a day around, and it points at a public school rather than a home.

`home.local.json` and `data/home.local.json` hold the actual coordinates. Both
are git-ignored and neither is ever written into a peaks file.

## Rebuilding the data

```
python build_data.py             # private copy, from your address
python build_data.py --public    # published copy, from Waimea College
python build_data.py --refresh   # re-download everything from OpenStreetMap
python build_routes.py           # routes, climb figures and height profiles
```

`build_routes.py` writes into both data files, because they share the same
geometry and differ only in where the drive times start. Ground heights are
cached in `data/elevation_cache.json`, so a second run costs nothing.

Raw downloads are cached in `data/`, so a rerun is quick.

To change your home address, edit `home.local.json`:

```json
{ "name": "home", "lat": -41.3XXXXX, "lon": 173.2XXXXX }
```

Then run `python build_data.py` again.

## The LINZ API key

`index.html` currently uses the public key from LINZ's own documentation. LINZ
documents that standard keys need renewing every 90 days and allow a million
tile requests a month. Developer keys do not expire and have no cap.
`LINZ-key-request-email.txt` is a ready email asking LINZ for one. When it
arrives, replace the single marked line near the top of the `<script>` block.

## Where every number comes from

| Thing | Source | Licence |
|---|---|---|
| Basemap | LINZ Topo50 and aerial | CC BY 4.0 |
| Peak name, position, height | OpenStreetMap | ODbL |
| Tracks and roads | OpenStreetMap | ODbL |
| Public access | Herenga a Nuku Aotearoa, Public Access Areas | CC BY 3.0 NZ |
| Drive time | OSRM routing over OpenStreetMap roads | ODbL |
| Ground height | LINZ NZ 8m DEM, via OpenTopoData | CC BY 4.0 |
| Forecast | ECMWF IFS and Open-Meteo | CC BY 4.0 |

Rules the build script follows:

* No geographic fact is invented. Anything that cannot be established is
  written as null, and the app says "not established" rather than guessing.
* A mapped track is not the same as public access. The access badge comes from
  the official access map, and "not shown as public land" is reported as
  exactly that, not as "private".
* Where the mapped track stops more than 100 m short of the summit, the app
  says so, because the last part is a route rather than a path.

## How the trailhead is worked out

There is no tidy list of NZ trailheads, so the build script derives them:

1. Build a graph of every walking track in the region from OpenStreetMap.
2. Find which connected track network reaches within 1 km of the summit.
3. Within that network, find every point lying within 250 m of a drivable
   public road.
4. Of those, take the one closest to the mountain. That is the trailhead.
5. Route to it and record the time.

Forestry tracks are treated as walking, not driving, so the trailhead is where
a normal car would actually stop. If no point in the network comes within 250 m
of a road, no trailhead is recorded and the drive time is left blank. That
happens for 13 of the 209 peaks.
