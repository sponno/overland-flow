// Which fences would matter? Put a solid fence on every property boundary that a
// flow path crosses, one at a time, run the model again and measure what moved.
//
//   node rank_fences.js      needs data.js and parcels.js, writes fences.js
//
// A "fence" here is the whole boundary two parcels share. Nothing is detected: this
// asks where a fence would change the outcome if there were one.
const { Worker, isMainThread, parentPort, workerData } = require('worker_threads');
const fs = require('fs');
const os = require('os');
const path = require('path');
require('./data.js');
require('./parcels.js');
const Flow = require('./flow.js');

const MIN_AREA = 2000;     // m², the smallest catchment Council maps as a flow path
const FENCE_HEIGHT = 1.8;  // metres, solid
const BUILDING_HEIGHT = 3; // metres, as in v2
const NEAR = 5;            // metres: a path that shifts less than this hasn't really moved
const MIN_RUN = 10;        // metres of new flow path before we say the fence moved it
const MAX_LENGTH = 200;    // metres: a longer shared boundary isn't one fence
const SHALLOW = 0.5;       // metres: holding back more water than this is a dam's job, not a fence's
const PUBLIC = new Set(['Road', 'Hydro']);

const D = FLOW_DATA, P = PARCEL_DATA, W = D.width, H = D.height, N = W * H;
const z = Flow.decode(D);
const buildingAt = Flow.decodeBuildings(D);
const parcelAt = Flow.decodeRuns(P.runs, P.values, N);
const isPublic = (p) => p === 0 || PUBLIC.has(P.parcels[p - 1].intent);

// The starting point is v2: bare ground plus buildings.
const walls = new Float32Array(N);
const skip = new Uint8Array(N); // cells that can't carry a flow path: buildings, and the fence being tested
for (let i = 0; i < N; i++) if (buildingAt[i]) { walls[i] = BUILDING_HEIGHT; skip[i] = 1; }
const base = Flow.compute(z, W, H, walls);

// Every boundary a flow path crosses on open ground, with the cells a fence would stand on.
function findFences() {
  const size = new Int32Array(P.parcels.length + 1);
  for (let i = 0; i < N; i++) size[parcelAt[i]]++;
  const key = (a, b) => a < b ? a * 65536 + b : b * 65536 + a;
  // The fence stands just inside the private lot, or the smaller lot of two.
  const fenceSide = (a, b) => {
    if (isPublic(a) !== isPublic(b)) return isPublic(a) ? b : a;
    return size[a] < size[b] || (size[a] === size[b] && a < b) ? a : b;
  };

  const fences = new Map();
  for (let i = 0; i < N; i++) {
    const r = base.receiver[i];
    if (base.acc[i] < MIN_AREA || r < 0 || skip[i] || skip[r]) continue;
    const a = parcelAt[i], b = parcelAt[r];
    if (a === b || (isPublic(a) && isPublic(b))) continue;
    let f = fences.get(key(a, b));
    if (!f) {
      const side = fenceSide(a, b), other = side === a ? b : a;
      // LINZ parcel ids either side, the one the fence stands in first.
      const between = [side, other].map((p) => p ? P.parcels[p - 1].id : null);
      fences.set(key(a, b), f = { side, between, street: isPublic(a) || isPublic(b), area: 0, cells: [] });
    }
    f.area = Math.max(f.area, base.acc[i]);
  }
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x, a = parcelAt[i];
      if (skip[i]) continue;
      for (let d = 0; d < 8; d++) {
        const nx = x + Flow.DX[d], ny = y + Flow.DY[d];
        if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
        const b = parcelAt[ny * W + nx];
        if (b === a) continue;
        const f = fences.get(key(a, b));
        if (f && f.side === a && f.cells[f.cells.length - 1] !== i) f.cells.push(i);
      }
    }
  }
  return [...fences.values()].filter((f) => f.cells.length <= MAX_LENGTH);
}

// Flow path cells on open ground per parcel, and whether each building has one against it.
const pathCells = new Int32Array(P.parcels.length + 1);
const besideBefore = new Uint8Array(D.buildings.linz_ids.length + 1);
for (let i = 0; i < N; i++) {
  if (skip[i] || base.acc[i] < MIN_AREA) continue;
  pathCells[parcelAt[i]]++;
  for (const b of buildingsBeside(i)) besideBefore[b] = 1;
}

function buildingsBeside(i) {
  const x = i % W, y = (i - x) / W, found = [];
  for (let d = 0; d < 8; d++) {
    const nx = x + Flow.DX[d], ny = y + Flow.DY[d];
    if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
    const b = buildingAt[ny * W + nx];
    if (b) found.push(b);
  }
  return found;
}

// Stand the fence up, run the model, and compare with the run without it.
function measure(fence) {
  for (const c of fence.cells) { walls[c] = FENCE_HEIGHT; skip[c] = 1; }
  const next = Flow.compute(z, W, H, walls);
  const { gained, lost } = Flow.changes(base, next, W, H, MIN_AREA, NEAR, skip);

  // Properties and buildings that had no flow path and now have one.
  const perParcel = new Map(), buildings = new Set();
  for (const i of gained) {
    const p = parcelAt[i];
    if (!isPublic(p) && pathCells[p] === 0) perParcel.set(p, (perParcel.get(p) || 0) + 1);
    for (const b of buildingsBeside(i)) if (!besideBefore[b]) buildings.add(b);
  }
  let newParcels = 0;
  for (const n of perParcel.values()) if (n >= 3) newParcels++;

  // Water has to stand this deep behind the fence before it finds a way out.
  let depth = 0, volume = 0;
  for (let i = 0; i < N; i++) {
    if (skip[i]) continue;
    const rise = next.filled[i] - base.filled[i];
    if (rise > 0.01) { volume += rise; if (rise > depth) depth = rise; }
  }
  const over = fence.cells.some((c) => next.acc[c] >= MIN_AREA);

  for (const c of fence.cells) { walls[c] = 0; skip[c] = 0; }
  return {
    ...fence, newRun: gained.length, lostRun: lost.length, newParcels, newBuildings: buildings.size,
    depth: Math.round(depth * 100) / 100, volume: Math.round(volume), over,
  };
}

// 0 = no change, 1 = moves a flow path, 2 = puts one through a property that had none.
const grade = (f) => f.newParcels ? 2 : f.newRun >= MIN_RUN ? 1 : 0;

async function main() {
  const fences = findFences();
  const threads = Math.max(1, Math.min(os.cpus().length, 8));
  console.log(`${fences.length} boundaries with a flow path across them, ${threads} threads`);
  const chunks = Array.from({ length: threads }, (_, t) => fences.filter((_, k) => k % threads === t));
  const started = Date.now();
  const results = (await Promise.all(chunks.map((chunk) => new Promise((resolve, reject) => {
    new Worker(__filename, { workerData: chunk }).once('message', resolve).once('error', reject);
  })))).flat();
  console.log(`tested in ${Math.round((Date.now() - started) / 1000)} s`);

  // Most significant first. Fences that do it in shallow water lead, then it is the number
  // of new properties, then how much water ran how far on a new route.
  const deep = (f) => grade(f) > 0 && f.depth > SHALLOW ? 1 : 0;
  results.sort((a, b) => grade(b) - grade(a) || deep(a) - deep(b) || b.newParcels - a.newParcels
    || b.area * b.newRun - a.area * a.newRun || b.area - a.area);
  const counts = [0, 0, 0];
  for (const f of results) counts[grade(f)]++;
  console.log(`no change ${counts[0]}, moves a flow path ${counts[1]}, new properties ${counts[2]}`
    + ` (${results.filter((f) => grade(f) === 2 && !deep(f)).length} of them with ${SHALLOW} m of water or less)`);

  const out = {
    height: FENCE_HEIGHT, buildingHeight: BUILDING_HEIGHT, minArea: MIN_AREA, near: NEAR, minRun: MIN_RUN,
    maxLength: MAX_LENGTH, shallow: SHALLOW, counts,
    shallowNew: results.filter((f) => grade(f) === 2 && !deep(f)).length,
    fences: results.map((f) => ({
      grade: grade(f), street: f.street ? 1 : 0, area: Math.round(f.area),
      newRun: f.newRun, lostRun: f.lostRun, newParcels: f.newParcels, newBuildings: f.newBuildings,
      depth: f.depth, volume: f.volume, over: f.over ? 1 : 0, between: f.between,
      // Cells in grid order, each stored as the step from the one before.
      cells: f.cells.map((c, k) => k ? c - f.cells[k - 1] : c),
    })),
  };
  const file = path.join(__dirname, 'fences.js');
  fs.writeFileSync(file, 'globalThis.FENCE_DATA = ' + JSON.stringify(out) + ';\n');
  console.log('wrote', file, (fs.statSync(file).size / 1e6).toFixed(1), 'MB');
}

if (isMainThread) main(); else parentPort.postMessage(workerData.map(measure));
