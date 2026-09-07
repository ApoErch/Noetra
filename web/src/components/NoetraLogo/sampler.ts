import { CatmullRomCurve3, Vector3 } from 'three'
import type { GlyphMetrics } from './sdf'
import { glyphMetrics, sdf, sdf2, skeletonPoints } from './sdf'
import type { LogoParams } from './params'

/**
 * Turns the glyph's distance field into the actual point cloud and edge mesh.
 *
 * Pure: no WebGL, no DOM, no `three` beyond `CatmullRomCurve3` (which is plain maths).
 * Everything is seeded, so the same params always produce the same cloud — dragging a
 * slider nudges the model instead of reshuffling it.
 *
 * Three populations, each answering to a different part of the reference image:
 *   - the volume fill, blue-noise sampled through the solid;
 *   - the contour shell, a ring of brighter dots just inside the 2D outline, which is
 *     what draws the bright dotted line tracing every bar in the reference;
 *   - the outer halo, sparse speckles fading from violet to blue outside the surface.
 */

/**
 * Nodes carry *how* to colour and size themselves, not the finished colour and size.
 *
 * `rampT` is a position along the palette, `kind` says which population the node belongs
 * to, `accent` is how much magenta to fold in, `sizeT` is a position in the size range.
 * The shader resolves all four against uniforms, so every colour stop, the hub multiplier
 * and the size range stay live sliders instead of forcing a rebuild of the cloud.
 */
export type LogoGeometryData = {
  count: number
  positions: Float32Array
  sizeT: Float32Array
  rampT: Float32Array
  kind: Float32Array
  accent: Float32Array
  alphas: Float32Array
  traceT: Float32Array
  radial: Float32Array
  seeds: Float32Array
  edgeCount: number
  edgePositions: Float32Array
  edgeRampT: Float32Array
  edgeKind: Float32Array
  edgeAccent: Float32Array
  edgeAlphas: Float32Array
  edgeTraceT: Float32Array
  edgeRadial: Float32Array
}

/** Which population a node belongs to; the vertex shader branches on this. */
export const NODE_KIND = { body: 0, shell: 1, halo: 2, hub: 3 } as const

/** Small, fast, seedable PRNG — the point of it is repeatability, not crypto. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** sRGB hex to a 0–1 triple. No colour-space conversion — see `engine.ts`. */
export function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.replace('#', ''), 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

/** A point in Bridson's [r, 2r] annulus — spherical, or flat in the XY plane. */
function annulusOffset(rng: () => number, radius: number, flat: boolean): [number, number, number] {
  const d = radius * (1 + rng())
  const phi = rng() * Math.PI * 2
  if (flat) return [Math.cos(phi) * d, Math.sin(phi) * d, 0]
  const u = rng() * 2 - 1
  const s = Math.sqrt(1 - u * u)
  return [s * Math.cos(phi) * d, s * Math.sin(phi) * d, u * d]
}

/**
 * Bridson's Poisson-disc sampling — blue noise, in O(n).
 *
 * Random points clump and leave holes, which on a dense mesh reads as blotches; this
 * keeps an even, organic spacing instead. Both populations run through here and differ
 * only in two things, which is why they are parameters:
 *
 * `propose` turns an active point into a candidate, or returns null to reject it: an
 * annulus point inside the solid for the volume fill, an annulus point projected onto
 * the outline for the contour.
 *
 * `flat` measures spacing in XY only and collapses the grid to one layer in Z. That is
 * what puts exactly one contour dot per station around the outline while leaving each
 * one free to sit anywhere through the thickness.
 */
function poissonDisc(
  half: readonly [number, number, number],
  radius: number,
  rng: () => number,
  seeds: readonly [number, number, number][],
  limit: number,
  propose: (x: number, y: number, z: number) => [number, number, number] | null,
  flat = false,
): number[] {
  const cell = radius / (flat ? Math.SQRT2 : Math.sqrt(3))
  const nx = Math.ceil((half[0] * 2) / cell) + 1
  const ny = Math.ceil((half[1] * 2) / cell) + 1
  const nz = flat ? 1 : Math.ceil((half[2] * 2) / cell) + 1
  const grid = new Int32Array(nx * ny * nz).fill(-1)
  const out: number[] = []
  const active: number[] = []
  const r2 = radius * radius

  const axis = (v: number, extent: number, count: number) =>
    Math.min(count - 1, Math.max(0, Math.floor((v + extent) / cell)))

  const farEnough = (x: number, y: number, z: number) => {
    const i = axis(x, half[0], nx)
    const j = axis(y, half[1], ny)
    const k = flat ? 0 : axis(z, half[2], nz)
    for (let dk = flat ? 0 : -2; dk <= (flat ? 0 : 2); dk++) {
      const kk = k + dk
      if (kk < 0 || kk >= nz) continue
      for (let dj = -2; dj <= 2; dj++) {
        const jj = j + dj
        if (jj < 0 || jj >= ny) continue
        for (let di = -2; di <= 2; di++) {
          const ii = i + di
          if (ii < 0 || ii >= nx) continue
          const other = grid[(kk * ny + jj) * nx + ii]
          if (other < 0) continue
          const dx = out[other * 3] - x
          const dy = out[other * 3 + 1] - y
          const dz = flat ? 0 : out[other * 3 + 2] - z
          if (dx * dx + dy * dy + dz * dz < r2) return false
        }
      }
    }
    return true
  }

  const push = (x: number, y: number, z: number) => {
    const id = out.length / 3
    out.push(x, y, z)
    const k = flat ? 0 : axis(z, half[2], nz)
    grid[(k * ny + axis(y, half[1], ny)) * nx + axis(x, half[0], nx)] = id
    active.push(id)
  }

  for (const [x, y, z] of seeds) push(x, y, z)

  while (active.length > 0 && out.length / 3 < limit) {
    const pick = Math.floor(rng() * active.length)
    const id = active[pick]
    let placed = false
    for (let attempt = 0; attempt < 20; attempt++) {
      const candidate = propose(out[id * 3], out[id * 3 + 1], out[id * 3 + 2])
      if (!candidate || !farEnough(candidate[0], candidate[1], candidate[2])) continue
      push(candidate[0], candidate[1], candidate[2])
      placed = true
      break
    }
    if (!placed) {
      active[pick] = active[active.length - 1]
      active.pop()
    }
  }
  return out
}

/**
 * Pull a point onto the 2D iso-line `sdf2 == -inset`, walking down the field's gradient.
 *
 * Sampling a band that thin by rejection alone would throw away almost every candidate;
 * projecting first makes nearly all of them usable.
 */
function projectToOutline(m: GlyphMetrics, inset: number, x: number, y: number): [number, number] | null {
  let px = x
  let py = y
  for (let i = 0; i < 4; i++) {
    const d = sdf2(m, px, py) + inset
    if (Math.abs(d) < 1e-5) break
    const e = 1e-4
    const gx = sdf2(m, px + e, py) - sdf2(m, px - e, py)
    const gy = sdf2(m, px, py + e) - sdf2(m, px, py - e)
    const length = Math.hypot(gx, gy)
    if (length < 1e-9) return null
    px -= (d * gx) / length
    py -= (d * gy) / length
  }
  return Math.abs(sdf2(m, px, py) + inset) < 2e-3 ? [px, py] : null
}

/** The contour ring: blue-noise stations around the outline, at any depth. */
function sampleContour(
  m: GlyphMetrics,
  inset: number,
  spacing: number,
  rng: () => number,
  limit: number,
): number[] {
  const zLimit = Math.max(m.halfDepth - inset, 0)
  const randomZ = () => (rng() * 2 - 1) * zLimit
  const inBounds = (x: number, y: number) => Math.abs(x) <= m.half[0] && Math.abs(y) <= m.half[1]

  // Seeds are taken off each bar's centreline, never on it: exactly on the centreline the
  // distance field's gradient is zero, so the projection has no direction to move in.
  let seed: [number, number] | null = null
  for (const [ax, ay, bx, by] of m.segments) {
    const length = Math.hypot(bx - ax, by - ay) || 1
    seed = projectToOutline(
      m,
      inset,
      (ax + bx) / 2 - ((by - ay) / length) * m.halfWidth * 0.6,
      (ay + by) / 2 + ((bx - ax) / length) * m.halfWidth * 0.6,
    )
    if (seed) break
  }
  if (!seed) return []

  return poissonDisc(
    m.half,
    spacing,
    rng,
    [[seed[0], seed[1], randomZ()]],
    limit,
    (x, y) => {
      const [ox, oy] = annulusOffset(rng, spacing, true)
      const projected = projectToOutline(m, inset, x + ox, y + oy)
      return projected && inBounds(projected[0], projected[1])
        ? [projected[0], projected[1], randomZ()]
        : null
    },
    true,
  )
}

/** The volume fill: blue-noise points throughout the solid. */
function sampleVolume(m: GlyphMetrics, radius: number, rng: () => number, limit: number): number[] {
  return poissonDisc(m.half, radius, rng, [[-0.33, 0, 0]], limit, (x, y, z) => {
    const [ox, oy, oz] = annulusOffset(rng, radius, false)
    const p: [number, number, number] = [x + ox, y + oy, z + oz]
    const outside = p.some((v, i) => Math.abs(v) > m.half[i])
    return !outside && sdf(m, p[0], p[1], p[2]) <= 0 ? p : null
  })
}

/** Monte-Carlo estimate of the solid's volume, used to pick a Poisson radius. */
function estimateVolume(m: GlyphMetrics, rng: () => number): number {
  const boxVolume = 8 * m.half[0] * m.half[1] * m.half[2]
  const trials = 20000
  let inside = 0
  for (let i = 0; i < trials; i++) {
    const x = (rng() * 2 - 1) * m.half[0]
    const y = (rng() * 2 - 1) * m.half[1]
    const z = (rng() * 2 - 1) * m.half[2]
    if (sdf(m, x, y, z) <= 0) inside++
  }
  return (boxVolume * inside) / trials
}

/** Nearest-neighbour edges over a subset of nodes, via a uniform spatial hash. */
function buildEdges(
  positions: Float32Array,
  indices: Int32Array,
  k: number,
  maxDistance: number,
  into: Set<number>,
): void {
  const cell = maxDistance
  const buckets = new Map<string, number[]>()
  const key = (i: number, j: number, l: number) => `${i},${j},${l}`
  for (const id of indices) {
    const i = Math.floor(positions[id * 3] / cell)
    const j = Math.floor(positions[id * 3 + 1] / cell)
    const l = Math.floor(positions[id * 3 + 2] / cell)
    const b = buckets.get(key(i, j, l))
    if (b) b.push(id)
    else buckets.set(key(i, j, l), [id])
  }

  const max2 = maxDistance * maxDistance
  const candidates: { id: number; d: number }[] = []
  for (const id of indices) {
    const x = positions[id * 3]
    const y = positions[id * 3 + 1]
    const z = positions[id * 3 + 2]
    const ci = Math.floor(x / cell)
    const cj = Math.floor(y / cell)
    const cl = Math.floor(z / cell)
    candidates.length = 0
    for (let di = -1; di <= 1; di++)
      for (let dj = -1; dj <= 1; dj++)
        for (let dl = -1; dl <= 1; dl++) {
          const b = buckets.get(key(ci + di, cj + dj, cl + dl))
          if (!b) continue
          for (const other of b) {
            if (other === id) continue
            const dx = positions[other * 3] - x
            const dy = positions[other * 3 + 1] - y
            const dz = positions[other * 3 + 2] - z
            const d = dx * dx + dy * dy + dz * dz
            if (d <= max2) candidates.push({ id: other, d })
          }
        }
    candidates.sort((a, b) => a.d - b.d)
    for (let n = 0; n < Math.min(k, candidates.length); n++) {
      const other = candidates[n].id
      const lo = Math.min(id, other)
      const hi = Math.max(id, other)
      into.add(lo * 1e6 + hi)
    }
  }
}

/** Build the whole point cloud and edge list for one set of parameters. */
export function buildGeometry(params: LogoParams): LogoGeometryData {
  const m = glyphMetrics(params.strokeWidth, params.extrusionDepth)
  const rng = mulberry32(params.seed * 2654435761)

  const shellTarget = Math.round(params.nodeCount * params.shellRatio)
  const haloTarget = Math.round(params.nodeCount * params.haloRatio)
  const fillTarget = Math.max(params.nodeCount - shellTarget - haloTarget, 1)

  // ── volume fill ────────────────────────────────────────────────────────────
  const volume = estimateVolume(m, rng)
  // Let the fill run to completion and correct the radius, rather than capping the run at
  // the target. Bridson emits points in flood-fill order out from its seed, so stopping it
  // early — or keeping only the first N — starves whatever is furthest from that seed. The
  // right leg came out visibly sparser than the left, which reads as the whole letter
  // being turned away from you. Count goes as 1/r³, so one correction lands close.
  const radius = Math.cbrt((0.62 * volume) / fillTarget)
  let fill = sampleVolume(m, radius, rng, fillTarget * 4)
  const firstPass = Math.max(fill.length / 3, 1)
  if (Math.abs(firstPass - fillTarget) > fillTarget * 0.08) {
    fill = sampleVolume(m, radius * Math.cbrt(firstPass / fillTarget), rng, fillTarget * 4)
  }
  // Any residual overshoot is a few per cent, so dropping a random handful costs nothing
  // in evenness — unlike truncating the list, which would cost a whole region.
  let fillCount = fill.length / 3
  while (fillCount > fillTarget) {
    const victim = Math.floor(rng() * fillCount)
    const last = fillCount - 1
    for (let axis = 0; axis < 3; axis++) fill[victim * 3 + axis] = fill[last * 3 + axis]
    fillCount = last
  }

  // ── contour shell ──────────────────────────────────────────────────────────
  // Space the ring from the target count and the outline's own length, so the contour
  // keeps the same look whether the cloud is 900 points or 4,000.
  let centreLine = 0
  for (const [ax, ay, bx, by] of m.segments) centreLine += Math.hypot(bx - ax, by - ay)
  const perimeter = 2 * centreLine + Math.PI * m.halfWidth
  const contourSpacing = shellTarget > 0 ? Math.max(perimeter / shellTarget, 0.004) : 1
  let shell = shellTarget > 0 ? sampleContour(m, params.shellInset, contourSpacing, rng, shellTarget) : []
  if (shellTarget > 0 && shell.length / 3 < shellTarget * 0.92) {
    // The perimeter estimate above treats the three bars as separate strokes, but they
    // overlap heavily at both joins, so the real outline is shorter and the first pass
    // runs out of contour before it runs out of budget. Count is linear in spacing along
    // a curve, so one correction lands within a few points of the target.
    const achieved = Math.max(shell.length / 3, 1)
    shell = sampleContour(m, params.shellInset, (contourSpacing * achieved) / shellTarget, rng, shellTarget)
  }
  const shellCount = shell.length / 3

  // ── outer halo ─────────────────────────────────────────────────────────────
  // Plain dart-throwing, not blue noise: the reference's speckles are irregular, and an
  // evenly spaced halo would read as a deliberate second outline.
  const halo: number[] = []
  const haloHalf: [number, number, number] = [
    m.half[0] + params.haloDistance,
    m.half[1] + params.haloDistance,
    m.half[2] + params.haloDistance,
  ]
  const haloDistances: number[] = []
  for (let attempt = 0; attempt < haloTarget * 400 && halo.length / 3 < haloTarget; attempt++) {
    const x = (rng() * 2 - 1) * haloHalf[0]
    const y = (rng() * 2 - 1) * haloHalf[1]
    const z = (rng() * 2 - 1) * haloHalf[2]
    const d = sdf(m, x, y, z)
    if (d <= 0 || d > params.haloDistance) continue
    if (rng() > Math.exp(-d / (params.haloDistance * 0.4))) continue
    halo.push(x, y, z)
    haloDistances.push(d / params.haloDistance)
  }
  const haloCount = halo.length / 3

  // ── pack positions ─────────────────────────────────────────────────────────
  const count = fillCount + shellCount + haloCount
  const positions = new Float32Array(count * 3)
  positions.set(fill.slice(0, fillCount * 3), 0)
  positions.set(shell, fillCount * 3)
  positions.set(halo, (fillCount + shellCount) * 3)

  // ── edges ──────────────────────────────────────────────────────────────────
  // Halo speckles stay unconnected: in the reference nothing links them to the body.
  const meshed = new Int32Array(fillCount + shellCount)
  for (let i = 0; i < meshed.length; i++) meshed[i] = i
  const edgeKeys = new Set<number>()
  buildEdges(positions, meshed, params.edgeNeighbors, params.edgeMaxDistance, edgeKeys)
  if (shellCount > 0) {
    // A second pass over the shell alone, so the contour always reads as a continuous
    // dotted line rather than dissolving into whichever body nodes happen to be nearer.
    const shellIds = new Int32Array(shellCount)
    for (let i = 0; i < shellCount; i++) shellIds[i] = fillCount + i
    buildEdges(positions, shellIds, 2, contourSpacing * 3, edgeKeys)
  }

  const degrees = new Int32Array(count)
  const edges: [number, number][] = []
  for (const packed of edgeKeys) {
    const a = Math.floor(packed / 1e6)
    const b = packed - a * 1e6
    edges.push([a, b])
    degrees[a]++
    degrees[b]++
  }

  // ── hubs: the best-connected body nodes, not random ones ───────────────────
  // In the reference the big white dots sit on genuine mesh junctions, so pick by degree.
  const hub = new Uint8Array(count)
  const hubCount = Math.round(fillCount * params.hubRatio)
  const byDegree = Array.from({ length: fillCount }, (_, i) => i).sort((a, b) => degrees[b] - degrees[a])
  for (let i = 0; i < hubCount; i++) hub[byDegree[i]] = 1

  // ── skeleton: arc-length parameter and push direction per node ─────────────
  const curve = new CatmullRomCurve3(
    skeletonPoints(m).map(([x, y]) => new Vector3(x, y, 0)),
    false,
    'centripetal',
  )
  const samples = curve.getSpacedPoints(600)
  const traceT = new Float32Array(count)
  const radial = new Float32Array(count * 3)
  for (let i = 0; i < count; i++) {
    const x = positions[i * 3]
    const y = positions[i * 3 + 1]
    const z = positions[i * 3 + 2]
    let best = 0
    let bestD = Infinity
    for (let s = 0; s < samples.length; s++) {
      const p = samples[s]
      const d = (p.x - x) ** 2 + (p.y - y) ** 2 + (p.z - z) ** 2
      if (d < bestD) {
        bestD = d
        best = s
      }
    }
    traceT[i] = best / (samples.length - 1)
    const p = samples[best]
    const dx = x - p.x
    const dy = y - p.y
    const dz = z - p.z
    const len = Math.hypot(dx, dy, dz)
    if (len > 1e-6) {
      radial[i * 3] = dx / len
      radial[i * 3 + 1] = dy / len
      radial[i * 3 + 2] = dz / len
    } else {
      radial[i * 3 + 2] = 1
    }
  }

  // ── per-node appearance ──────────────────────────────────────────────────
  const sizeT = new Float32Array(count)
  const rampT = new Float32Array(count)
  const kind = new Float32Array(count)
  const accent = new Float32Array(count)
  const alphas = new Float32Array(count)
  const seeds = new Float32Array(count)

  for (let i = 0; i < count; i++) {
    seeds[i] = rng()
    if (i < fillCount) {
      // Squaring the sample keeps the majority of dots small, with a long thin tail.
      sizeT[i] = rng() ** 2
      rampT[i] = rng()
      accent[i] = rng() < 0.12 ? 0.5 : 0
      alphas[i] = 0.75 + rng() * 0.25
      kind[i] = hub[i] ? NODE_KIND.hub : NODE_KIND.body
      if (hub[i]) alphas[i] = 1
    } else if (i < fillCount + shellCount) {
      sizeT[i] = 0.45
      kind[i] = NODE_KIND.shell
      alphas[i] = 1
    } else {
      const d = haloDistances[i - fillCount - shellCount]
      sizeT[i] = 0
      rampT[i] = d
      kind[i] = NODE_KIND.halo
      alphas[i] = 0.5 * (1 - d * 0.6)
    }
  }

  // ── edge buffers ───────────────────────────────────────────────────────────
  // Each line vertex carries *its own* endpoint's trace parameter and push direction,
  // never the average of the pair: averaging would move both ends of a line to the
  // midpoint's offset while the two dots move to their own, so lines would visibly
  // detach from the nodes they connect as the wavefront passes.
  const edgeCount = edges.length
  const edgePositions = new Float32Array(edgeCount * 6)
  const edgeRadial = new Float32Array(edgeCount * 6)
  const edgeRampT = new Float32Array(edgeCount * 2)
  const edgeKind = new Float32Array(edgeCount * 2)
  const edgeAccent = new Float32Array(edgeCount * 2)
  const edgeAlphas = new Float32Array(edgeCount * 2)
  const edgeTraceT = new Float32Array(edgeCount * 2)
  for (let e = 0; e < edgeCount; e++) {
    for (let end = 0; end < 2; end++) {
      const node = edges[e][end]
      const v = e * 2 + end
      edgePositions[v * 3] = positions[node * 3]
      edgePositions[v * 3 + 1] = positions[node * 3 + 1]
      edgePositions[v * 3 + 2] = positions[node * 3 + 2]
      edgeRadial[v * 3] = radial[node * 3]
      edgeRadial[v * 3 + 1] = radial[node * 3 + 1]
      edgeRadial[v * 3 + 2] = radial[node * 3 + 2]
      edgeRampT[v] = rampT[node]
      edgeKind[v] = kind[node]
      edgeAccent[v] = accent[node]
      edgeAlphas[v] = alphas[node]
      edgeTraceT[v] = traceT[node]
    }
  }

  return {
    count,
    positions,
    sizeT,
    rampT,
    kind,
    accent,
    alphas,
    traceT,
    radial,
    seeds,
    edgeCount,
    edgePositions,
    edgeRampT,
    edgeKind,
    edgeAccent,
    edgeAlphas,
    edgeTraceT,
    edgeRadial,
  }
}
