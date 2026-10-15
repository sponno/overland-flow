# Overland flow

Team 4, Browny's Hackathon, 16 October 2026. Our problem: built structures such as
fences change where overland flood water goes, and Auckland Council has no data on them.

**Live: https://sponno.github.io/overland-flow/** lists every version, newest first.

| Version | What it adds | Link |
| --- | --- | --- |
| v7 | Mt Albert pilot tile: the v6 model and tests moved to the hackathon's starter tile, BA31_1000_4245 | https://sponno.github.io/overland-flow/indexv7.html |
| v6 | Would it need consent: every fence line against the Unitary Plan's two tests for a fence in a flow path | https://sponno.github.io/overland-flow/indexv6.html |
| v5 | Fences worth checking: the same results as one plain list, with a simpler way to pick a fence | https://sponno.github.io/overland-flow/indexv5.html |
| v4 | Is anything standing there: the LiDAR point cloud read along each boundary | https://sponno.github.io/overland-flow/indexv4.html |
| v3 | Which fences matter: a fence tried on every boundary a flow path crosses | https://sponno.github.io/overland-flow/indexv3.html |
| v2 | Buildings as obstacles | https://sponno.github.io/overland-flow/indexv2.html |
| v1 | Rain on bare ground | https://sponno.github.io/overland-flow/indexv1.html |

Each new piece gets its own numbered page, so the earlier ones stay as they were and the
list shows how the work built up. To add one: copy the latest `indexvN.html` to the next
number, add a card to `index.html`, and add a row here.

## The idea

**An active map, not a blanket rule.** Written up, with pictures, at
https://sponno.github.io/overland-flow/idea.html (`idea.html`).

1. **The rule.** A fence across an overland flow path must not obstruct it: no "ponding
   beyond the boundaries of the property", no "diverting the entry or exit point". It
   applies where more than 4,000 m² of land drains to the fence (Unitary Plan, E36).
2. **Our hypothesis.** Nobody can run that test on a fence without a site visit, so the
   4,000 m² line stands in for it, and the line is wrong about as often as it is right.
3. **What we found.** We ran the rule's own test on all 1,015 fence lines in one area.
   The rule is right on 400 (39%), wrong on 310 (31%), misses 98 (10%) and can't say on
   207 (20%). Wrong or missed 408 times against right 400, so the hypothesis holds.

| | Does harm if solid | No harm | Depends whose fence |
| --- | --- | --- | --- |
| Over the line (rule applies, 721) | 260, right | 310, wrong | 151, can't say |
| Under the line (rule silent, 294) | 98, missed | 140, right | 56, can't say |

Right means the line and the test agree. Wrong means the rule catches a fence line where
a solid fence does no harm. Missed means a solid fence does harm but the path is under
the line. Can't say means water stands only on the uphill neighbour's land, and the
wording doesn't decide whether that counts for a shared fence.

About a third of fence lines do harm in every catchment size band, so size is not the
signal. What decides it is the ground round the fence.

So: test every fence line and keep the answers on the flow path map Council already has.
Each fence line gets a status (any fence, keep it open, to decide), tied to the properties
either side, and the map re-runs when the ground or the facts change. The rule keeps its
test and drops the 4,000 m² line.

## What is here

This repo is our shared starting point: a small overland flow model that runs in the
browser on real Auckland data, plus the script that fetches that data.

## What the map does

Click anywhere and a drop of water follows the ground downhill until it reaches the sea
or the edge of the map. "Rain" animates the whole area. You can overlay Council's own
mapped flow paths to compare.

The area is about 2 km by 1.3 km around Vermont St, Ponsonby, at 1 m resolution.

v2 adds buildings as solid blocks the water has to go round, and lets you switch
between that and the ground-only view. It colours every building that a ground-only flow
path runs through, and shows as a dashed line where a drop would have gone without them.

v3 asks which fences are worth finding. It does not detect any. It takes every property
boundary that a flow path crosses, stands a solid 1.8 m fence on it, runs the model again
and measures what moved. The map shows all of them, graded, with the ten that matter most
numbered. Click one to put the fence up: the new route, the old route and the water
backing up are drawn, and the rain follows the new route. `indexv3.html#3` opens with
fence 3 up.

v4 asks whether anything is actually standing on those boundaries. It reads the 2024 LiDAR
point cloud along each one and reports how much of the boundary has something 0.5 to 2.6 m
high on it, how high, and how level the top is. Pick a boundary to see a side view of
every laser point on it. The same numbering as v3, so `indexv4.html#3` is the same
boundary.

v5 adds no new analysis. It is v3 and v4 rebuilt so someone new can read it: one list of
the 91 fence lines that matter, in plain words ("Looks like a fence, 1.6 m high. If solid,
sends water through 4 more properties"). Pick one from the list or the map and it opens in
place, the fence goes up on the map, and a round X beside it takes it down again. The
colour chips filter the list and the map. "Drop water" at the top right switches the map
to dropping: click anywhere and one drop runs downhill, as in v1, and with a fence up a
dashed line shows where it would have gone without it. Same numbering again:
`indexv5.html#3`.

v6 puts every fence line through the rule Council itself applies. The Unitary Plan
(chapter E36) permits a fence in an overland flow path only if it does not obstruct the
path, and the standard for that is that it "must not cause ponding beyond the boundaries
of the property". Separately, "diverting the entry or exit point" of a flow path needs
consent. v6 stands the solid fence up on each line again and asks both questions: does
water now stand on someone else's land, and does the flow path now cross a boundary at a
new place. Each fence gets an outcome (would need consent, depends whose fence it is,
would be permitted, or outside the rule because the path drains under 4,000 m²) and says
why. The list is ordered by how many properties a solid fence would affect, so the numbers
are new: each fence says which boundary it was in v3 to v5.

## Files

| File | What it is |
| --- | --- |
| `index.html` | The list of versions. |
| `idea.html` | The write-up: what the versions add up to, and how the rule could work, with five real fence lines drawn before and after. |
| `figures.html`, `save_figures.py`, `figs/` | The pictures in the write-up. `figures.html` draws them from the data and the model; `python3 save_figures.py` saves them into `figs/` (needs Chrome). `figures.html?try=12,34` draws any other fence lines by their place in `fences.js`. |
| `indexv1.html` | v1, the ground-only map. Opens straight from disk, no build step or server. |
| `indexv2.html` | v2, the same map with buildings as obstacles. |
| `indexv3.html` | v3, the fence test. Runs the model again in the browser when you pick a fence. |
| `indexv4.html` | v4, v3 plus what the LiDAR point cloud shows along each boundary. |
| `indexv5.html` | v5, the same data as v4 as a plain-language list. |
| `indexv6.html` | v6, every fence line against the Unitary Plan's two tests. |
| `indexv7.html` | v7, v6 on the Mt Albert starter tile. Reads its data from `mtalbert/`. Start here. |
| `check.html` | Checking fence lines by eye: each Mt Albert fence line on the aerial photo with Street View and 3D links, answered in the brief's own words (structure, continuity, height, permeability), our laser's answer hidden. Answers stay in the browser until downloaded as CSV or GeoJSON. |
| `make_sample.py` | Puts the fence lines in a fixed random order with their LINZ boundary lines and writes `sample.js` for `check.html`: `AREA=mtalbert uv run make_sample.py`. |
| `mtalbert/` | `data.js`, `parcels.js`, `fences.js`, `rules.js`, `lidar.js` and `sample.js` for Mt Albert, made by the same scripts with `AREA=mtalbert`. |
| `thumbs/` | Screenshots for the version list. |
| `flow.js` | The model: fill hollows, send each cell to its steepest downhill neighbour (D8), count what drains through each cell. |
| `fetch_data.py` | Pulls the terrain, building outlines and Council's flow paths for any point and writes `data.js`. `AREAS` at the top names the areas: Vermont St (files in the root) and Mt Albert (files in `mtalbert/`). |
| `data.js` | Generated by `fetch_data.py`. Don't edit by hand. |
| `fetch_parcels.py` | Pulls LINZ parcels for the area `data.js` covers, burns them onto the same grid and writes `parcels.js`. |
| `rank_fences.js` | The fence test. Finds every boundary a flow path crosses, tries a fence on each, ranks them and writes `fences.js`. |
| `test_rules.js` | The rule test. Stands a fence on every boundary in `fences.js` again and checks it against the two tests in the Unitary Plan. Writes `rules.js`, which names the LINZ parcels each fence would affect. |
| `scan_fences.py` | The LiDAR scan. Downloads the point cloud tiles into `lidar/`, looks along every boundary in `fences.js` and writes `lidar.js`. Keeps a side view for the fences `rules.js` says would need consent. |
| `parcels.js`, `fences.js`, `rules.js`, `lidar.js` | Generated by the four scripts above. Don't edit by hand. `rules.js` and `lidar.js` line up with `fences.js` entry for entry, so re-run them after the fence test, in that order. |

`Flow.compute(z, width, height, walls)` takes an optional `walls` array of extra height
per cell. v2 uses it for buildings (3 m on every cell inside an outline). v3 uses it for
fences: raise the cells under a barrier and run it again. `Flow.changes(before, after, ...)`
compares two runs and returns the cells that gained or lost a flow path.

## Run it for somewhere else

Needs [uv](https://docs.astral.sh/uv/). Distances are metres from the point.

```
uv run fetch_data.py <lat> <lon> <west> <east> <north> <south> "<label>"
uv run fetch_data.py -36.85300 174.74517 1742 258 590 710 "Top of Vermont St"
```

It reads only the window it needs from the cloud-hosted terrain files, so it takes
seconds and downloads a few megabytes.

Then, for the fence test (needs Node 18 or later):

```
uv run fetch_parcels.py
node rank_fences.js
```

`rank_fences.js` runs the model once per boundary across up to eight threads. For this
area that is 1,015 runs and about four minutes on a laptop.

Then, for the rule test, about another four minutes:

```
node test_rules.js
```

Then, for the LiDAR scan:

```
uv run scan_fences.py
```

The first run downloads 15 point cloud tiles, 450 MB, into `lidar/` (not committed) and
takes about ten minutes. After that it reuses them and takes about a minute.

The Mt Albert starter tile (v7) is the same five steps with `AREA=mtalbert` in front of
each, and the files go into `mtalbert/`. Its map runs 300 m past the tile on every side,
and only fence lines a flow path crosses inside the tile are tested, so the whole run
takes under a minute and four point cloud tiles.

```
AREA=mtalbert uv run fetch_data.py
AREA=mtalbert uv run fetch_parcels.py
AREA=mtalbert node rank_fences.js
AREA=mtalbert node test_rules.js
AREA=mtalbert uv run scan_fences.py
```

## Data

| Data | Source | Licence |
| --- | --- | --- |
| Auckland Part 1 LiDAR 1m DEM (2024), bare earth | LINZ open bucket: `s3://nz-elevation/auckland/auckland-part-1_2024/dem_1m/2193/` | CC BY 4.0, Toitū Te Whenua LINZ |
| Same survey, 1 m surface model (not used yet) | `.../auckland-part-1_2024/dsm_1m/2193/` | CC BY 4.0, Toitū Te Whenua LINZ |
| NZ Building Outlines | LINZ on ArcGIS Online, no API key: `https://services.arcgis.com/xdsHIIxuCWByZiCB/arcgis/rest/services/LINZ_NZ_Building_Outlines/FeatureServer/0` | CC BY 4.0, Crown copyright, Toitū Te Whenua LINZ |
| Auckland Part 1 LiDAR point cloud (2024), about 18 points per m² | OpenTopography open bucket, no key: `https://opentopography.s3.sdsc.edu/pc-bulk/NZ24_Auckland/CL2_<sheet>_2024_1000_<row><col>.laz` | Published by LINZ, whose elevation data is CC BY 4.0. Not checked for this survey: see its OpenTopography page |
| NZ Primary Parcels | LINZ on ArcGIS Online, no API key: `https://services.arcgis.com/xdsHIIxuCWByZiCB/arcgis/rest/services/LINZ_NZ_Primary_Parcels/FeatureServer/0` | CC BY 4.0, Crown copyright, Toitū Te Whenua LINZ |
| Overland Flow Paths | Auckland Council: `https://services1.arcgis.com/n4yPwebTjJCmXB6W/arcgis/rest/services/Overland_Flow_Paths/FeatureServer/0` | "Creative Commons (refer to Healthy Waters)". Credit: WSP; Auckland Council Healthy Waters, Geospatial |
| Basemaps (loaded live, not stored here) | OpenStreetMap, or Esri World Street Map when a page is opened from disk; Esri World Imagery | As credited on the map |

Terrain tiles are named by NZ Topo50 sheet, for example `BA31_10000_0405.tiff`.
`tile_name()` in `fetch_data.py` works out the tile for a point. Point cloud tiles are
smaller, 480 m by 720 m: `tile_1000()` in `scan_fences.py` does the same for those.

**Don't commit raw LiDAR or whole terrain tiles.** They are hundreds of megabytes each.
Add a fetch script instead; `.gitignore` blocks `.laz`, `.las` and `.tif`.

## What we know so far

Checked against the data, not assumed:

- Council's flow paths are GIS lines drawn from a bare-earth 1 m DEM (WSP, 2019, using
  2016/17 LiDAR ground points), not hydraulic modelling. Buildings are not in them, and
  neither are fences.
- The public flow path layer carries one attribute, a catchment size class:
  2,000 to 4,000 m², 4,000 m² to 1 ha, 1 to 3 ha, 3 to 100 ha, and 100 ha and above.
- This model, on the 2024 terrain, lands close to Council's lines.
- In this map there are 4,371 buildings. A ground-only flow path draining 2,000 m² or
  more (Council's cut-off) crosses the outline of 443 of them, about one in ten.

- Most fences would change nothing. A flow path of Council's size crosses 1,015 property
  boundaries on open ground here. A solid 1.8 m fence on each, one at a time: at 735
  (72%) the route does not move, because the water goes round the end (616) or over the
  top (119). At 189 it makes 10 m or more of new flow path. At 91 (9%) it puts a flow path
  through a property that had none, up to 6 properties for one fence.
- 50 of those 91 do it with half a metre of water or less standing against the fence. The
  other 41 need more, which is a lot to ask of a fence.
- 74 of the 91 are between neighbours and 17 are on a street frontage.

So the job is not finding every fence. It is checking a short list of boundaries, and v3
says which ones.

From the LiDAR scan in v4:

- Fences do show up in the point cloud. Something 0.5 to 2.6 m high stands along 70% or
  more of 683 of the 994 boundaries with open ground to scan (69%). On the same lines
  shifted 4 m sideways, where no boundary is, the same test fires on 531 of 1,971 (27%).
- Almost every boundary here has something on it. Another 226 (23%) have something along
  part, and only 74 (7%) have nothing standing.
- Of the 91 that matter: 63 have something all along, 20 along part, 6 nothing, and 2
  could not be scanned. 23 of the 63 have a level top, and 19 of those tops are between
  1.6 and 2.1 m, which is what a fence or wall looks like.
- The survey's own point classes do not pick fences out. Points on one level-topped line
  can be labelled unclassified, vegetation and building along its length.

So whether something is there is mostly not the open question. The laser rules out 6 of
the 91. What it cannot say is whether the thing is solid or see-through, and that decides
whether it moves water. That needs a photo.

From the rule test in v6:

- The Plan defines an overland flow path as draining more than 4,000 m². Council maps
  them from 2,000 m², so 294 of our 1,015 fence lines fall outside the rule, including 37
  of the 91 from v3.
- Of the 721 the rule covers, a solid fence would fail on 260 (36%): 173 because water
  would stand beyond the boundary, 141 because the flow path would cross a boundary at a
  new place, 54 of them both.
- At another 151 the water would stand only on the uphill neighbour's land. Whether that
  is "beyond the boundaries of the property" depends on whose fence it is, which we can't
  know. At 310 a solid fence passes both tests.
- "Most fences would change nothing" was about the route. 100 of the 735 fences that leave
  the route alone would still fail on ponding.
- Most of the ponding would not take a rare storm. Of the 173 ponds that cross a boundary,
  half fill with under 3 mm of rain running off the land that drains into them, and 156
  (nine in ten) with 25 mm or less. Six need more than 100 mm. `rules.js` gives the figure
  for each fence as `fill`.
- From the air, 64 of the 260 look like a fence, 105 like a hedge or trees, 59 have gaps
  and 27 are mostly open. 53 are on a street front and 207 between neighbours.

So in Council's own terms the list is 260 fence lines, 64 of them worth a look first. What
would settle each one is whether the bottom of the fence is open, which is the fix
Council's leaflet asks for (palings cut 150 mm clear of the ground, or drainage holes in
masonry).

The rule, as we read it: Unitary Plan E36.4.1 (A39) with standard E36.6.1.10(1), and
E36.4.1 (A41). Rule numbers and wording are from Council's
[practice note RC 3.2.18](https://www.aucklanddesignmanual.co.nz/content/dam/adm/adm-website/aup-hub/unitary-plan-practice-and-guidance-notes/RC_3.2.18_E36_Overland_Flow_Paths.pdf)
(February 2020), not the Plan itself, so check they are current. The fixes are from
Council's leaflet
[Addressing fences that block overland flow paths](https://www.aucklandcouncil.govt.nz/content/dam/ac/docs/environment/OLFP-fences.pdf).
None of this is a legal opinion.

From the same tests on the Mt Albert starter tile in v7 (BA31_1000_4245, 480 x 720 m,
much of it the slopes of Ōwairaka):

- A flow path crosses 103 fence lines in the tile. The rule covers 47 of them, and a solid
  fence would fail on 14: 12 by moving where the water crosses a boundary, 3 by ponding,
  1 both. 3 of the 14 look like a fence from the air.
- Against all 103, the 4,000 m² line is right on 47 (46%), wrong on 26 (25%), misses 11
  (11%) and can't say on 19 (18%). At Vermont St it was 39/31/10/20%. The tile is a tenth
  the size, so each fence line moves its percentages by one point.
- More of the tile is under the line (56 of 103) because it sits high on the mountain,
  where catchments are small.

Not tested yet: any of this against what is on the ground.

## What the model ignores

- No real fences, kerbs or stormwater pipes. Water moves to the lowest neighbouring square
  metre, as if every drain were already full.
- The fences in v3 are hypothetical. Each is the whole boundary two parcels share, solid,
  1.8 m tall, never leaking or falling over, with every other boundary open. Real fences
  have gaps and gates, and a street frontage usually has a driveway.
- A property in v3 is one LINZ parcel. A school or a block of flats can be several.
- The LiDAR scan in v4 has not been checked on the ground. It cannot tell a paling fence
  from a clipped hedge, or solid from see-through. Its cut-offs (70% for all along, a top
  that varies by 0.4 m or less for level) are our choices. It looks up to 1.5 m either
  side of the surveyed boundary, so a fence further off it than that is missed.
- Shared boundaries longer than 200 m are left out of the fence test.
- The rule test in v6 calls it ponding when water stands 10 cm or deeper over 5 m² or more
  of a property, and a moved crossing when the flow path crosses a boundary more than 5 m
  from where any flow path ran before. Those cut-offs are ours. The Plan gives none.
- v6 treats a street-front fence as belonging to the property behind it, and a fence
  between neighbours as belonging to either. Water standing on the road counts as beyond
  the boundary.
- The Plan's definition of an overland flow path leaves out permanent and intermittent
  streams. We can't tell which of the big flow paths here are streams.
- In v2 a building is a solid 3 m block on a 1 m grid. Real houses leak: water gets
  under floors on piles and in through doors. Gaps under a metre between houses can close.
- Hollows assume culverts are blocked, so a road embankment across a gully reads as a dam.
- Catchment sizes only count land inside the map, so treat them as minimums.
- No depths, speeds or timing. The animation speed is for looks. The depth v3 gives for a
  fence is how deep water must stand before it finds a way out, not how deep it would get
  in a given storm.
