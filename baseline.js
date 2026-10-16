// The terrain-only baseline the memo asks for. Council's flow paths come from bare ground
// alone. Run the same model three times over the tile and compare:
//   terrain   bare ground only, the way Council's paths are made
//   built     plus buildings, 3 m solid (as in v2)
//   fences    plus every stretch v12 detected as a fence or wall, 1.8 m solid (as in v3)
// Hedges, can't-tells and openings stay open; retaining walls are already in the ground.
//
//   AREA=mtalbert node baseline.js     needs data.js, parcels.js and allfences.js; writes baseline.js
const fs = require('fs');
const path = require('path');
const DIR = path.join(__dirname, process.env.AREA || '');
require(path.join(DIR, 'data.js'));
require(path.join(DIR, 'parcels.js'));
require(path.join(DIR, 'allfences.js'));
const Flow = require('./flow.js');

const MIN_AREA = 2000;     // m², the smallest catchment Council maps as a flow path
const NEAR = 5;            // metres: a path that shifts less than this hasn't really moved
const FENCE_HEIGHT = 1.8;  // metres, solid
const BUILDING_HEIGHT = 3; // metres
const POND = 0.1;          // metres of water standing deeper than before counts as ponding
const PUBLIC = new Set(['Road', 'Hydro']);

const D = FLOW_DATA, P = PARCEL_DATA, A = ALLFENCES_DATA, W = D.width, H = D.height, N = W * H;
const z = Flow.decode(D);
const buildingAt = Flow.decodeBuildings(D);
const parcelAt = Flow.decodeRuns(P.runs, P.values, N);
const isPublic = (p) => p === 0 || PUBLIC.has(P.parcels[p - 1].intent);

// The grid is axis-aligned in Web Mercator, so lat/lng to cell is linear there.
const R = 6378137;
const mx = (lon) => R * lon * Math.PI / 180, my = (lat) => R * Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
const [south, west, north, east] = D.bounds;
const X0 = mx(west), X1 = mx(east), Y0 = my(north), Y1 = my(south);
const cellXY = ([lat, lon]) => [(mx(lon) - X0) / (X1 - X0) * W, (Y0 - my(lat)) / (Y0 - Y1) * H];
const latLng = (i) => {
  const x = i % W + 0.5, y = Math.floor(i / W) + 0.5;
  const lon = (X0 + x / W * (X1 - X0)) / R * 180 / Math.PI;
  const lat = (2 * Math.atan(Math.exp((Y0 - y / H * (Y0 - Y1)) / R)) - Math.PI / 2) * 180 / Math.PI;
  return [+lat.toFixed(7), +lon.toFixed(7)];
};

// Only the pilot tile counts, as in rank_fences.js.
function inFocus(i) {
  const q = D.focus.cells, x = i % W + 0.5, y = Math.floor(i / W) + 0.5;
  let sign = 0;
  for (let k = 0; k < 4; k++) {
    const [x1, y1] = q[k], [x2, y2] = q[(k + 1) % 4];
    const s = Math.sign((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1));
    if (s && sign && s !== sign) return false;
    sign = sign || s;
  }
  return true;
}

// A fence on the grid, with no diagonal gaps water could slip through.
function burn(seg, walls, skip, height) {
  const [ax, ay] = cellXY(seg[0]), [bx, by] = cellXY(seg[1]);
  const n = Math.ceil(Math.hypot(bx - ax, by - ay) / 0.2) + 1;
  let prev = -1;
  for (let k = 0; k <= n; k++) {
    const x = Math.floor(ax + (bx - ax) * k / n), y = Math.floor(ay + (by - ay) * k / n);
    if (x < 0 || y < 0 || x >= W || y >= H) { prev = -1; continue; }
    const i = y * W + x;
    if (prev >= 0 && i !== prev) {
      const px = prev % W, py = (prev - px) / W;
      if (px !== x && py !== y) { const j = py * W + x; walls[j] = Math.max(walls[j], height); skip[j] = 1; }
    }
    walls[i] = Math.max(walls[i], height); skip[i] = 1;
    prev = i;
  }
}

const bare = new Float32Array(N);
const built = new Float32Array(N), skipBuilt = new Uint8Array(N);
for (let i = 0; i < N; i++) if (buildingAt[i]) { built[i] = BUILDING_HEIGHT; skipBuilt[i] = 1; }
const fenced = Float32Array.from(built), skip = Uint8Array.from(skipBuilt);
let fenceMetres = 0;
for (const r of A.lines) for (const [cls, seg] of r.parts) if (cls === 'fence') {
  burn(seg, fenced, skip, FENCE_HEIGHT);
  const [ax, ay] = cellXY(seg[0]), [bx, by] = cellXY(seg[1]);
  fenceMetres += Math.hypot(bx - ax, by - ay);
}

console.log('running terrain, built, fences');
const runs = { terrain: Flow.compute(z, W, H, bare), built: Flow.compute(z, W, H, built), fences: Flow.compute(z, W, H, fenced) };

// Compare on the same ground in every run: inside the tile, off buildings and fences.
const counted = (i) => inFocus(i) && !skip[i];
const pathMetres = (run) => { let n = 0; for (let i = 0; i < N; i++) if (counted(i) && run.acc[i] >= MIN_AREA) n++; return n; };
function properties(run) {
  const cells = new Map();
  for (let i = 0; i < N; i++) {
    if (!counted(i) || run.acc[i] < MIN_AREA) continue;
    const p = parcelAt[i];
    if (!isPublic(p)) cells.set(p, (cells.get(p) || 0) + 1);
  }
  return new Set([...cells].filter(([, n]) => n >= 3).map(([p]) => p));
}
function compare(before, after) {
  const { gained, lost } = Flow.changes(runs[before], runs[after], W, H, MIN_AREA, NEAR, skip);
  const pb = properties(runs[before]), pa = properties(runs[after]);
  const pond = new Map();
  let pondCells = 0;
  for (let i = 0; i < N; i++) {
    if (!counted(i) || runs[after].filled[i] - runs[before].filled[i] <= POND) continue;
    pondCells++;
    const p = parcelAt[i];
    if (!isPublic(p)) pond.set(p, (pond.get(p) || 0) + 1);
  }
  return {
    newPathMetres: gained.filter(inFocus).length, lostPathMetres: lost.filter(inFocus).length,
    propertiesGainingPath: [...pa].filter((p) => !pb.has(p)).length, propertiesLosingPath: [...pb].filter((p) => !pa.has(p)).length,
    pondingSquareMetres: pondCells, propertiesPonded: [...pond.values()].filter((n) => n >= 5).length,
    gained: gained.filter(inFocus), lost: lost.filter(inFocus),
  };
}

const builtVsTerrain = compare('terrain', 'built');
const fencesVsBuilt = compare('built', 'fences');
const fencesVsTerrain = compare('terrain', 'fences');
const strip = ({ gained, lost, ...rest }) => rest;
const summary = {
  tile: D.focus.name, fenceMetres: Math.round(fenceMetres), minArea: MIN_AREA, near: NEAR, fenceHeight: FENCE_HEIGHT, pond: POND,
  pathMetres: { terrain: pathMetres(runs.terrain), built: pathMetres(runs.built), fences: pathMetres(runs.fences) },
  propertiesWithPath: { terrain: properties(runs.terrain).size, built: properties(runs.built).size, fences: properties(runs.fences).size },
  builtVsTerrain: strip(builtVsTerrain), fencesVsBuilt: strip(fencesVsBuilt), fencesVsTerrain: strip(fencesVsTerrain),
};
console.log(JSON.stringify(summary, null, 1));

// The paths for the map: each run's flow path cells in the tile, and what the fences moved.
const cellsOf = (run) => { const out = []; for (let i = 0; i < N; i++) if (counted(i) && run.acc[i] >= MIN_AREA) out.push(latLng(i)); return out; };
const file = path.join(DIR, 'baseline.js');
fs.writeFileSync(file, 'globalThis.BASELINE_DATA = ' + JSON.stringify({
  ...summary,
  terrain: cellsOf(runs.terrain), fences: cellsOf(runs.fences),
  gained: fencesVsTerrain.gained.map(latLng), lost: fencesVsTerrain.lost.map(latLng),
}) + ';\n');
console.log('wrote', file, (fs.statSync(file).size / 1e6).toFixed(1), 'MB');
