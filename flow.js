// Naive overland flow on a grid. No hydraulics: water just runs to its lowest
// neighbour. Same family of GIS technique Council's mapped flow paths use.
//
//   1. fill      - raise every hollow until it can spill (priority flood)
//   2. direction - each cell drains to its steepest downhill neighbour (D8)
//   3. accumulate- count how many cells drain through each cell
//
// Works in the browser (globalThis.Flow) and in node (module.exports).
(function () {
  const DX = [1, 1, 0, -1, -1, -1, 0, 1];
  const DY = [0, 1, 1, 1, 0, -1, -1, -1];
  const DIST = [1, Math.SQRT2, 1, Math.SQRT2, 1, Math.SQRT2, 1, Math.SQRT2];
  const EPS = 1e-5; // tiny tilt so filled hollows and flats still drain

  // Binary min-heap of cell indices keyed by a Float64Array of elevations.
  class Heap {
    constructor(keys, capacity) {
      this.keys = keys;
      this.items = new Int32Array(capacity);
      this.size = 0;
    }
    push(i) {
      const { items, keys } = this;
      let n = this.size++;
      while (n > 0) {
        const p = (n - 1) >> 1;
        if (keys[items[p]] <= keys[i]) break;
        items[n] = items[p];
        n = p;
      }
      items[n] = i;
    }
    pop() {
      const { items, keys } = this;
      const top = items[0];
      const last = items[--this.size];
      let n = 0;
      for (;;) {
        let c = 2 * n + 1;
        if (c >= this.size) break;
        if (c + 1 < this.size && keys[items[c + 1]] < keys[items[c]]) c++;
        if (keys[items[c]] >= keys[last]) break;
        items[n] = items[c];
        n = c;
      }
      items[n] = last;
      return top;
    }
  }

  // z: Float32Array of ground heights (NaN = no data, treated as an outlet).
  // walls: optional Float32Array of extra height added on top of the ground.
  function compute(z, width, height, walls) {
    const n = width * height;
    const surface = new Float64Array(n);
    for (let i = 0; i < n; i++) surface[i] = z[i] + (walls ? walls[i] : 0);

    // 1. Priority flood from the edges inward.
    const filled = new Float64Array(n);
    const done = new Uint8Array(n);
    const heap = new Heap(filled, n);
    const isOutlet = new Uint8Array(n);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const i = y * width + x;
        if (Number.isNaN(surface[i])) { done[i] = 1; continue; }
        let edge = x === 0 || y === 0 || x === width - 1 || y === height - 1;
        if (!edge) {
          for (let d = 0; d < 8; d++) {
            if (Number.isNaN(surface[i + DX[d] + DY[d] * width])) { edge = true; break; }
          }
        }
        if (edge) { filled[i] = surface[i]; done[i] = 1; isOutlet[i] = 1; heap.push(i); }
      }
    }
    while (heap.size > 0) {
      const c = heap.pop();
      const cx = c % width, cy = (c - cx) / width;
      for (let d = 0; d < 8; d++) {
        const x = cx + DX[d], y = cy + DY[d];
        if (x < 0 || y < 0 || x >= width || y >= height) continue;
        const i = y * width + x;
        if (done[i]) continue;
        done[i] = 1;
        filled[i] = Math.max(surface[i], filled[c] + EPS);
        heap.push(i);
      }
    }

    // 2. Steepest-descent receiver for each cell (-1 = leaves the map).
    const receiver = new Int32Array(n).fill(-1);
    const slope = new Float32Array(n);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const i = y * width + x;
        if (Number.isNaN(surface[i])) continue;
        let best = 0, to = -1;
        for (let d = 0; d < 8; d++) {
          const nx = x + DX[d], ny = y + DY[d];
          if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
          const j = ny * width + nx;
          if (Number.isNaN(surface[j])) continue;
          const s = (filled[i] - filled[j]) / DIST[d];
          if (s > best) { best = s; to = j; }
        }
        // Edge cells are where water leaves, unless they clearly drain inward.
        if (isOutlet[i] && best < 1e-3) to = -1;
        receiver[i] = to;
        slope[i] = best;
      }
    }

    // 3. Accumulate upstream cell counts, sources first.
    const indegree = new Int32Array(n);
    for (let i = 0; i < n; i++) if (receiver[i] >= 0) indegree[receiver[i]]++;
    const acc = new Float32Array(n).fill(1);
    const stack = new Int32Array(n);
    let top = 0;
    for (let i = 0; i < n; i++) if (indegree[i] === 0) stack[top++] = i;
    while (top > 0) {
      const i = stack[--top];
      const r = receiver[i];
      if (r < 0) continue;
      acc[r] += acc[i];
      if (--indegree[r] === 0) stack[top++] = r;
    }

    const pond = new Float32Array(n);
    for (let i = 0; i < n; i++) pond[i] = Math.max(0, filled[i] - surface[i]);

    return { receiver, slope, acc, pond, filled };
  }

  // Follow the water from one cell until it leaves the map.
  function trace(receiver, start, maxSteps) {
    const path = [start];
    let i = start;
    const limit = maxSteps || receiver.length;
    while (receiver[i] >= 0 && path.length < limit) {
      i = receiver[i];
      path.push(i);
    }
    return path;
  }

  function decode(data) {
    const bin = typeof atob === 'function' ? atob(data.dem) : Buffer.from(data.dem, 'base64').toString('binary');
    const n = data.width * data.height;
    const z = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
      z[i] = v === data.nodata ? NaN : data.zmin + v * data.zscale;
    }
    return z;
  }

  const Flow = { compute, trace, decode, DX, DY, DIST };
  if (typeof module !== 'undefined' && module.exports) module.exports = Flow;
  else globalThis.Flow = Flow;
})();
