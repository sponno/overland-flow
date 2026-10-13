// Would a solid fence here break the Unitary Plan's rules? Stand a fence on every
// boundary in fences.js again, one at a time, and run the two tests in chapter E36.
//
//   ponding    A fence in an overland flow path is permitted only if it "must not cause
//              ponding beyond the boundaries of the property". E36.4.1 (A39), standard
//              E36.6.1.10(1).
//   diverting  "Diverting the entry or exit point" of an overland flow path needs
//              consent. Moving it within a site does not. E36.4.1 (A41).
//
// Both apply only on an overland flow path as the Plan defines one: rain from more than
// 4,000 m² of land. Council maps flow paths from 2,000 m², so some of ours fall short.
//
//   node test_rules.js      needs data.js, parcels.js and fences.js, writes rules.js
//
// rules.js lines up with fences.js entry for entry. Rule numbers are from Council's
// practice note RC 3.2.18 (February 2020). Nothing here is a legal opinion.
const { Worker, isMainThread, parentPort, workerData } = require('worker_threads');
const fs = require('fs');
const os = require('os');
const path = require('path');
require('./data.js');
require('./parcels.js');
require('./fences.js');
const Flow = require('./flow.js');

const PLAN_AREA = 4000;  // m², the Plan's own definition of an overland flow path
const POND_DEPTH = 0.1;  // metres of water standing where there was none before we call it ponding
const POND_AREA = 5;     // m² of it on one property before that property counts
const ENTRY_RUN = 3;     // metres of new flow path inside a property before its entry point counts as moved
const PUBLIC = new Set(['Road', 'Hydro']);

const D = FLOW_DATA, P = PARCEL_DATA, F = FENCE_DATA, W = D.width, H = D.height, N = W * H;
const z = Flow.decode(D);
const buildingAt = Flow.decodeBuildings(D);
const parcelAt = Flow.decodeRuns(P.runs, P.values, N);
const isPublic = (p) => p === 0 || PUBLIC.has(P.parcels[p - 1].intent);
const numberOf = new Map(P.parcels.map((p, k) => [p.id, k + 1]));
const idOf = (p) => P.parcels[p - 1].id;

// The same starting point as the fence test: bare ground plus buildings.
const walls = new Float32Array(N);
const skip = new Uint8Array(N);
for (let i = 0; i < N; i++) if (buildingAt[i]) { walls[i] = F.buildingHeight; skip[i] = 1; }
const base = Flow.compute(z, W, H, walls);
const isGained = new Uint8Array(N);
// Lots with a flow path on open ground before any fence goes up.
const hadPath = new Uint8Array(P.parcels.length + 1);
for (let i = 0; i < N; i++) if (!skip[i] && base.acc[i] >= F.minArea) hadPath[parcelAt[i]] = 1;

function test(fence) {
  let c = 0;
  const cells = fence.cells.map((step) => (c += step));
  const side = parcelAt[cells[0]];                 // the lot the fence stands in
  const other = numberOf.get(fence.between[1]) || 0; // the lot or street on the far side
  for (const i of cells) { walls[i] = F.height; skip[i] = 1; }
  const next = Flow.compute(z, W, H, walls);
  const { gained } = Flow.changes(base, next, W, H, F.minArea, F.near, skip);

  // Ponding: where water now stands that didn't, and on whose land. Public land is one lot, 0.
  const ponded = new Map();
  for (let i = 0; i < N; i++) {
    if (skip[i]) continue;
    const rise = next.filled[i] - base.filled[i];
    if (rise < POND_DEPTH) continue;
    const p = isPublic(parcelAt[i]) ? 0 : parcelAt[i];
    const lot = ponded.get(p) || { area: 0, depth: 0 };
    lot.area++;
    if (rise > lot.depth) lot.depth = rise;
    ponded.set(p, lot);
  }
  for (const [p, lot] of ponded) if (lot.area < POND_AREA) ponded.delete(p);
  const street = isPublic(other);
  const far = street ? 0 : other;
  const onSide = ponded.has(side), onFar = ponded.has(far);
  const others = [...ponded.keys()].filter((p) => p !== side && p !== far);
  // A frontage fence belongs to the lot behind it, so water on that lot alone stays within
  // its boundary. A fence between neighbours belongs to both. Water on the uphill one alone
  // is beyond the boundary only if the downhill owner put the fence up, which we can't know.
  let pond = 'none';
  if (others.length || (onSide && onFar) || (street && onFar)) pond = 'beyond';
  else if (onSide || onFar) pond = street ? 'own' : 'uphill';
  // The lot the fence backs water up on, which doesn't count as "beyond".
  const home = pond === 'beyond' && !street && onSide !== onFar ? (onSide ? side : far)
    : pond === 'beyond' && street ? side : -1;
  const beyond = [...ponded.entries()].filter(([p]) => pond === 'beyond' && p !== home);

  // Diverting: lots the flow path now enters or leaves at a point it didn't use before.
  // A lot that had no flow path and now has one counts however the water got in.
  for (const i of gained) isGained[i] = 1;
  const runIn = new Map(), crossed = new Set();
  for (const i of gained) runIn.set(parcelAt[i], (runIn.get(parcelAt[i]) || 0) + 1);
  for (const p of runIn.keys()) if (!isPublic(p) && !hadPath[p]) crossed.add(p);
  for (let i = 0; i < N; i++) {
    const r = next.receiver[i];
    if (next.acc[i] < F.minArea || r < 0 || skip[i] || skip[r]) continue;
    const a = parcelAt[i], b = parcelAt[r];
    if (a === b || (!isGained[i] && !isGained[r])) continue;
    if (!isPublic(a)) crossed.add(a);
    if (!isPublic(b)) crossed.add(b);
  }
  const moved = [...crossed].filter((p) => (runIn.get(p) || 0) >= ENTRY_RUN);

  for (const i of gained) isGained[i] = 0;
  for (const i of cells) { walls[i] = 0; skip[i] = 0; }
  return {
    plan: fence.area > PLAN_AREA ? 1 : 0,
    pond,
    // Water standing beyond the boundary: how many private lots, how much street, how deep.
    pondLots: beyond.filter(([p]) => p !== 0).map(([p]) => idOf(p)),
    pondStreet: beyond.filter(([p]) => p === 0).reduce((s, [, lot]) => s + lot.area, 0),
    pondDepth: Math.round(Math.max(0, ...(pond === 'beyond' ? beyond : [...ponded.entries()]).map(([, lot]) => lot.depth)) * 100) / 100,
    pondArea: [...ponded.values()].reduce((s, lot) => s + lot.area, 0),
    // Lots whose entry or exit point moved, by LINZ parcel id.
    moved: fence.newRun >= F.minRun ? moved.map(idOf) : [],
  };
}

// What the two tests add up to, if the fence is solid.
function outcome(r) {
  if (!r.plan) return 'below';                              // not an overland flow path under the Plan
  if (r.pond === 'beyond' || r.moved.length) return 'consent';
  return r.pond === 'uphill' ? 'depends' : 'permitted';
}

async function main() {
  const threads = Math.max(1, Math.min(os.cpus().length, 8));
  console.log(`${F.fences.length} fence lines, ${threads} threads`);
  const started = Date.now();
  const chunks = Array.from({ length: threads }, (_, t) => F.fences.map((f, k) => [k, f]).filter(([k]) => k % threads === t));
  const done = (await Promise.all(chunks.map((chunk) => new Promise((resolve, reject) => {
    new Worker(__filename, { workerData: chunk }).once('message', resolve).once('error', reject);
  })))).flat();
  console.log(`tested in ${Math.round((Date.now() - started) / 1000)} s`);
  const rules = [];
  for (const [k, r] of done) rules[k] = { ...r, outcome: outcome(r) };

  const tally = (list, key) => list.reduce((t, r) => { t[r[key]] = (t[r[key]] || 0) + 1; return t; }, {});
  const counts = tally(rules, 'outcome');
  console.log('outcome', counts);
  console.log('ponding', tally(rules, 'pond'), 'moved', rules.filter((r) => r.moved.length).length);

  const out = { planArea: PLAN_AREA, pondDepth: POND_DEPTH, pondArea: POND_AREA, entryRun: ENTRY_RUN, counts, fences: rules };
  const file = path.join(__dirname, 'rules.js');
  fs.writeFileSync(file, 'globalThis.RULE_DATA = ' + JSON.stringify(out) + ';\n');
  console.log('wrote', file, (fs.statSync(file).size / 1e6).toFixed(2), 'MB');
}

if (isMainThread) main(); else parentPort.postMessage(workerData.map(([k, f]) => [k, test(f)]));
