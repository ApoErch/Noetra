/**
 * Every knob the logo has, in one place.
 *
 * Two classes, and the split matters: STRUCTURAL keys are baked into geometry at sample
 * time, so changing one rebuilds the point cloud (debounced). Everything else is a
 * uniform or a material property and updates on the next frame, so its slider feels
 * instant. `DEFAULT_PARAMS` is what `devMode={false}` renders with — the dev panel only
 * ever edits a copy.
 */

export type LogoParams = {
  // ── structure (rebuilds geometry) ──────────────────────────────────────────
  seed: number
  nodeCount: number
  strokeWidth: number
  extrusionDepth: number
  shellRatio: number
  shellInset: number
  haloRatio: number
  haloDistance: number
  edgeNeighbors: number
  edgeMaxDistance: number
  hubRatio: number

  // ── particles ──────────────────────────────────────────────────────────────
  sizeMin: number
  sizeMax: number
  sizeScale: number
  hubSizeMultiplier: number
  particleOpacity: number

  // ── colours ────────────────────────────────────────────────────────────────
  colorViolet: string
  colorLavender: string
  colorDeep: string
  colorMagenta: string
  hubCore: string
  haloColor: string
  edgeColor: string

  // ── edges ──────────────────────────────────────────────────────────────────
  edgeOpacity: number

  // ── trace wavefront ────────────────────────────────────────────────────────
  traceLoop: boolean
  traceDuration: number
  traceWaveWidth: number
  tracePause: number
  traceGlow: number
  traceBoost: number

  // ── bubble push ────────────────────────────────────────────────────────────
  bubbleStrength: number
  bubbleZBias: number

  // ── idle twinkle ───────────────────────────────────────────────────────────
  twinkleSpeed: number
  twinkleAmount: number

  // ── glow / finish ──────────────────────────────────────────────────────────
  bloomEnabled: boolean
  bloomStrength: number
  bloomThreshold: number
  bloomRadius: number
  fringeStrength: number

  // ── camera ─────────────────────────────────────────────────────────────────
  fov: number
  /**
   * How much of the frame the mark fills — the knob for making the logo bigger.
   * 1 puts the letter exactly edge to edge, the geometric maximum for its container.
   * Around 0.85 also clears the trace pulse, which briefly overhangs (see `placeCamera`).
   */
  zoom: number
  /** Degrees the camera is swung to the side. 0 faces the glyph straight on. */
  azimuth: number
  /** Degrees the camera is lifted above the glyph. 0 is level with it. */
  elevation: number
}

/** The keys that are compiled into the point cloud, so a change means a rebuild. */
export const STRUCTURAL_KEYS = [
  'seed',
  'nodeCount',
  'strokeWidth',
  'extrusionDepth',
  'shellRatio',
  'shellInset',
  'haloRatio',
  'haloDistance',
  'edgeNeighbors',
  'edgeMaxDistance',
  'hubRatio',
] as const satisfies readonly (keyof LogoParams)[]

/** A string that changes exactly when the geometry needs rebuilding. */
export function structuralKey(p: LogoParams): string {
  return STRUCTURAL_KEYS.map((k) => p[k]).join('|')
}

export const DEFAULT_PARAMS: LogoParams = {
  seed: 7,
  nodeCount: 1400,
  strokeWidth: 0.215,
  extrusionDepth: 0.215,
  shellRatio: 0.2,
  shellInset: 0.022,
  haloRatio: 0.1,
  haloDistance: 0.05,
  edgeNeighbors: 5,
  edgeMaxDistance: 0.075,
  hubRatio: 0.05,

  sizeMin: 0.009,
  sizeMax: 0.02,
  sizeScale: 1,
  hubSizeMultiplier: 2.4,
  particleOpacity: 0.6,

  // One violet everywhere. The ramp, hub core and halo stay separate parameters so any
  // of them can be pulled apart again in the panel without touching code.
  colorViolet: '#8b5cf6',
  colorLavender: '#8b5cf6',
  colorDeep: '#8b5cf6',
  colorMagenta: '#8b5cf6',
  hubCore: '#8b5cf6',
  haloColor: '#8b5cf6',
  edgeColor: '#8b5cf6',

  edgeOpacity: 0.33,

  traceLoop: true,
  traceDuration: 6.0,
  traceWaveWidth: 0.25,
  tracePause: 1.0,
  traceGlow: 0.85,
  traceBoost: 3,

  bubbleStrength: 0.17,
  bubbleZBias: 0.80,

  twinkleSpeed: 1.8,
  twinkleAmount: 0.16,

  bloomEnabled: true,
  bloomStrength: 1.0,
  bloomThreshold: 1,
  bloomRadius: 1,
  fringeStrength: 0.0035,

  fov: 30,
  zoom: 0.75,
  azimuth: 25,
  elevation: 10,
}
