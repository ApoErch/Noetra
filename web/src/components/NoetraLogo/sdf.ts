/**
 * The Noetra "N" as a signed distance field.
 *
 * The glyph is three capsule bars — two verticals and a diagonal — unioned in 2D, then
 * thickened along Z into a real solid with rounded front/back edges. Everything is
 * normalised so the glyph is exactly 1 unit tall and centred on the origin; the numbers
 * below were measured off `NoetraLogo.png` (stroke 0.215 of the height, vertical
 * centrelines at ±0.33).
 *
 * `sdf()` is negative inside the solid, zero on the surface, positive outside — which is
 * the whole sampling contract: the volume fill keeps points where it is negative, the
 * inner contour shell keeps a thin band just below zero, and the halo keeps a band just
 * above it.
 */

/** Where each vertical bar's centreline sits, as a fraction of glyph height from centre. */
const VERTICAL_X = 0.33

/** A 2D line segment: the centreline of one capsule bar. */
type Segment = readonly [ax: number, ay: number, bx: number, by: number]

export type GlyphMetrics = {
  /** Half the stroke width — also the pill caps' radius in the XY plane. */
  halfWidth: number
  /** Half the extrusion depth along Z. */
  halfDepth: number
  /** Rounding radius on the four long edges where the face meets the side. */
  edgeRadius: number
  /** Centrelines of the left vertical, the diagonal, and the right vertical. */
  segments: readonly Segment[]
  /** Half-extents of the solid's bounding box, origin-centred. */
  half: readonly [number, number, number]
}

/** Build the glyph's measurements for a given stroke width and extrusion depth. */
export function glyphMetrics(strokeWidth: number, extrusionDepth: number): GlyphMetrics {
  const halfWidth = strokeWidth / 2
  const halfDepth = extrusionDepth / 2
  // Segment ends are pulled in by the cap radius so the rounded caps land exactly on
  // ±0.5 — the glyph is then precisely one unit tall, whatever the stroke width.
  const capY = 0.5 - halfWidth
  return {
    halfWidth,
    halfDepth,
    edgeRadius: halfDepth * 0.35,
    segments: [
      [-VERTICAL_X, -capY, -VERTICAL_X, capY], // left vertical
      [-VERTICAL_X, capY, VERTICAL_X, -capY], // diagonal, top-left to bottom-right
      [VERTICAL_X, -capY, VERTICAL_X, capY], // right vertical
    ],
    half: [VERTICAL_X + halfWidth, 0.5, halfDepth],
  }
}

/** Distance from a point to a 2D line segment. */
function distanceToSegment(px: number, py: number, s: Segment): number {
  const [ax, ay, bx, by] = s
  const pax = px - ax
  const pay = py - ay
  const bax = bx - ax
  const bay = by - ay
  const len = bax * bax + bay * bay
  let h = len > 0 ? (pax * bax + pay * bay) / len : 0
  h = h < 0 ? 0 : h > 1 ? 1 : h
  return Math.hypot(pax - bax * h, pay - bay * h)
}

/** The 2D glyph outline: the union of the three pills, negative inside. */
export function sdf2(m: GlyphMetrics, x: number, y: number): number {
  let d = Infinity
  for (const s of m.segments) {
    const c = distanceToSegment(x, y, s)
    if (c < d) d = c
  }
  return d - m.halfWidth
}

/**
 * The solid: the 2D outline extruded along Z with rounded edges.
 *
 * This is the standard rounded-box formula applied in (2D distance, Z depth) space — the
 * same trick that turns a square into a rounded rectangle, one dimension up.
 */
export function sdf(m: GlyphMetrics, x: number, y: number, z: number): number {
  const r = m.edgeRadius
  const qx = sdf2(m, x, y) + r
  const qy = Math.abs(z) - m.halfDepth + r
  const outside = Math.hypot(Math.max(qx, 0), Math.max(qy, 0))
  return Math.min(Math.max(qx, qy), 0) + outside - r
}

/**
 * Control points for the letter's centre-line, in stroke order: bottom of the left
 * vertical, up, along the diagonal, down to the bottom of the right vertical, up again.
 * Intermediate points keep each leg straight; a centripetal Catmull-Rom through them
 * rounds the two corners on its own.
 */
export function skeletonPoints(m: GlyphMetrics): readonly [number, number][] {
  const capY = 0.5 - m.halfWidth
  const x = VERTICAL_X
  return [
    [-x, -capY],
    [-x, -capY * 0.25],
    [-x, capY * 0.5],
    [-x, capY],
    [-x / 3, capY / 3],
    [x / 3, -capY / 3],
    [x, -capY],
    [x, -capY * 0.5],
    [x, capY * 0.25],
    [x, capY],
  ]
}
