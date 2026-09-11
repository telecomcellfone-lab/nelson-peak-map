# Waimea Tramping Club trip reports: what indexing would involve

Written so the club can see exactly what is proposed before agreeing to it.

## In one line

Read the club's existing trip report list page once, keep only the title, the
date and the link of each report, and use those to point people from a peak on
a map to the club's own page for that trip.

## Why it is paused

The club's `robots.txt` currently reads:

```
User-agent: *
Disallow: /
```

That is a standing instruction to every automated client not to fetch anything
from the site. It has been obeyed. Nothing has been extracted, and nothing
will be unless the club says otherwise.

## What the map is

A personal, non-commercial planning map of the 209 peaks around Nelson and
Tasman that have a walking track to them. Each peak shows the drive to the road
end, the climb measured along the actual track, the weather at summit height,
DOC's own track information and alerts, and links to trip reports about it.

It is already public at
<https://telecomcellfone-lab.github.io/nelson-peak-map/> and the code is open at
<https://github.com/telecomcellfone-lab/nelson-peak-map>.

The Nelson Tramping Club archive is already linked this way, 688 reports, and
the Wellington Tramping and Mountaineering Club, 1,107 reports.

## The process, step by step

### 1. One request, not a crawl

The club publishes every report on a single page:

```
https://www.waimeatrampingclub.org.nz/reports/trip-reports-list
```

That page already lists all of them. Observed: 490 report links, about 370 KB.
Adding `?limit=0` returns the same 490, so no pagination is needed.

So this is **one HTTP GET**, not a crawl of the site. Individual report pages
are never opened. Nothing else on the site is touched.

### 2. What is read out of that page

Each entry in the list is a link of this shape:

```
/reports/trip-reports-list/629-top-of-the-south-interclub-weekend
/reports/trip-reports-list/625-brook-maitai-crossover
/reports/trip-reports-list/626-champion-mine-mt-malita-circuit-2
/reports/trip-reports-list/630-conical-hill-2
```

Three things are taken from each:

| Field | Example | Where from |
|---|---|---|
| Title | Brook Maitai Crossover | the link text |
| Date | if the listing shows one | the listing markup |
| Link | the club's own URL, unchanged | the link itself |

That is the whole extraction. Specifically **not** taken:

* the body of any report
* photographs
* author names, member names, or any personal detail
* anything behind a login
* anything from any other part of the site

### 3. Matching reports to peaks

Each title is compared against the peak names on the map. A report counts as
being *about* a peak when the peak is named in the destination part of the
title, and as *passed through* when the peak is only mentioned later. The
second kind is shown dimmed.

This is the same method already used for the Nelson club, and it is
deliberately conservative. Where it is unsure, the full title is displayed so
the reader can judge for themselves.

### 4. How it appears on the map

Clicking a peak shows a section like this:

> **Trip reports** — 6 reports from 3 clubs, most recent 12 Apr 2026
>
> Brook Maitai Crossover
> *Waimea &middot; 12 Apr 2026*
>
> Conical Hill
> *Waimea &middot; 3 Feb 2026*

Every line is a link to the club's own page. The report text is never copied,
reproduced or summarised. A reader who wants the report goes to the club's
site to read it.

Alongside is a standing note that reports are one person's day in one set of
conditions, often years ago, and should be read as experience rather than as
fact about the track.

### 5. How often

Once, then refreshed manually perhaps two or three times a year. There is no
scheduled job and no continuous polling.

### 6. Rate and identification

If the club prefers a crawl of individual pages instead of the single list
page, requests are spaced 1.5 seconds apart and the client identifies itself
as:

```
nelson-peak-map/0.6 (personal, non-commercial hiking planner)
```

## What the club gets out of it

* Inbound links from a map of 209 local peaks straight to club reports.
* The club named on every entry.
* No content of the club's reproduced anywhere, so a reader has to visit.

## What the club can ask for

Any of these are easy to honour:

* Only index reports from a given year onward.
* Exclude particular reports or categories.
* A different label than "Waimea".
* A `Crawl-delay`, or a specific path allowance in `robots.txt` instead of a
  blanket permission.
* Removal at any time, which takes one line and a rebuild.

## If the answer is no

Nothing changes. The site stays untouched. The map can instead carry a plain
link to the club's trip report list page, with no indexing of individual
reports, if the club would like that.
