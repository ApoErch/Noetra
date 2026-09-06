import {
  BufferAttribute,
  BufferGeometry,
  LineSegments,
  LinearSRGBColorSpace,
  PerspectiveCamera,
  Points,
  Scene,
  Vector3,
  WebGLRenderer,
  type ShaderMaterial,
} from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { LogoParams } from './params'
import { buildGeometry, hexToRgb } from './sampler'
import { createEdgeMaterial, createParticleMaterial, createSpriteTexture, createTraceUniforms } from './materials'
import { PostChain } from './post'

/**
 * The imperative half of the logo: one WebGL renderer, one scene, one animation loop.
 *
 * Kept out of React on purpose — none of this belongs in a render pass. The component
 * creates an engine, feeds it parameters, and disposes it; the engine owns everything
 * that has to be torn down by hand.
 *
 * No colour management: `outputColorSpace` is linear-sRGB and hex values go to the GPU
 * as-is. Three only injects sRGB conversion into its own materials, and every material
 * here is hand-written, so converting on the way in would leave nothing to convert back
 * on the way out.
 */

/** Half the glyph's width, in the same units as `sdf.ts` — its height is exactly 1. */
const GLYPH_HALF_WIDTH = 0.4375

/** Below this many CSS pixels the mark is a glyph, not a diagram — spend less on it. */
export const SMALL_SIZE = 120

type Attributes = Record<string, { array: Float32Array; itemSize: number }>

function makeGeometry(attributes: Attributes): BufferGeometry {
  const geometry = new BufferGeometry()
  for (const [name, { array, itemSize }] of Object.entries(attributes)) {
    geometry.setAttribute(name, new BufferAttribute(array, itemSize))
  }
  return geometry
}

function setColor(uniform: { value: Vector3 }, hex: string): void {
  const [r, g, b] = hexToRgb(hex)
  uniform.value.set(r, g, b)
}

export class LogoEngine {
  private readonly renderer: WebGLRenderer
  private readonly scene = new Scene()
  private readonly camera: PerspectiveCamera
  private readonly post: PostChain
  private readonly controls: OrbitControls | null
  private readonly sprite = createSpriteTexture()
  private readonly shared = createTraceUniforms()
  private readonly particleMaterial: ShaderMaterial
  private readonly edgeMaterial: ShaderMaterial

  private points: Points | null = null
  private lines: LineSegments | null = null

  private params: LogoParams
  private readonly small: boolean
  private width = 1
  private height = 1
  private pixelRatio = 1
  private userMovedCamera = false
  private disposed = false

  private frame = 0
  private lastTime = 0
  private elapsed = 0
  private traceClock = 0

  constructor(canvas: HTMLCanvasElement, params: LogoParams, devMode: boolean, small: boolean) {
    this.params = params
    this.small = small

    this.renderer = new WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      // Straight alpha out of the composite; see `post.ts`.
      premultipliedAlpha: false,
      powerPreference: 'high-performance',
    })
    this.renderer.outputColorSpace = LinearSRGBColorSpace
    this.renderer.setClearColor(0x000000, 0)
    this.renderer.autoClear = false

    this.scene.background = null

    this.camera = new PerspectiveCamera(params.fov, 1, 0.05, 20)
    this.post = new PostChain(params)

    this.particleMaterial = createParticleMaterial(this.sprite, this.shared)
    this.edgeMaterial = createEdgeMaterial(this.shared)

    this.controls = devMode ? new OrbitControls(this.camera, canvas) : null
    if (this.controls) {
      this.controls.enableDamping = true
      this.controls.dampingFactor = 0.08
      this.controls.enablePan = false
      this.controls.minDistance = 0.6
      this.controls.maxDistance = 12
      this.controls.addEventListener('start', () => {
        this.userMovedCamera = true
      })
    }

    this.rebuild(params)
    this.applyParams(params)
    this.placeCamera()
    this.loop = this.loop.bind(this)
    this.frame = requestAnimationFrame(this.loop)
  }

  /** Resample the cloud. Only the structural parameters can require this. */
  rebuild(params: LogoParams): void {
    if (this.disposed) return
    this.params = params
    const scaled: LogoParams = this.small
      ? { ...params, nodeCount: Math.round(params.nodeCount * 0.45) }
      : params
    const data = buildGeometry(scaled)
    this.disposeGeometry()

    const pointGeometry = makeGeometry({
      position: { array: data.positions, itemSize: 3 },
      aRadial: { array: data.radial, itemSize: 3 },
      aSizeT: { array: data.sizeT, itemSize: 1 },
      aRampT: { array: data.rampT, itemSize: 1 },
      aKind: { array: data.kind, itemSize: 1 },
      aAccent: { array: data.accent, itemSize: 1 },
      aAlpha: { array: data.alphas, itemSize: 1 },
      aTraceT: { array: data.traceT, itemSize: 1 },
      aSeed: { array: data.seeds, itemSize: 1 },
    })
    this.points = new Points(pointGeometry, this.particleMaterial)
    this.points.frustumCulled = false
    this.points.renderOrder = 2

    const edgeGeometry = makeGeometry({
      position: { array: data.edgePositions, itemSize: 3 },
      aRadial: { array: data.edgeRadial, itemSize: 3 },
      aRampT: { array: data.edgeRampT, itemSize: 1 },
      aKind: { array: data.edgeKind, itemSize: 1 },
      aAccent: { array: data.edgeAccent, itemSize: 1 },
      aAlpha: { array: data.edgeAlphas, itemSize: 1 },
      aTraceT: { array: data.edgeTraceT, itemSize: 1 },
    })
    this.lines = new LineSegments(edgeGeometry, this.edgeMaterial)
    this.lines.frustumCulled = false
    this.lines.renderOrder = 1

    this.scene.add(this.lines, this.points)
  }

  /** Everything that is a uniform or a material property — no geometry touched. */
  applyParams(params: LogoParams): void {
    if (this.disposed) return
    const previous = this.params
    this.params = params
    const shared = this.shared
    shared.uWaveWidth.value = params.traceWaveWidth
    shared.uBubbleStrength.value = params.bubbleStrength
    shared.uPushZBias.value = params.bubbleZBias
    shared.uTraceGlow.value = params.traceGlow
    shared.uTraceBoost.value = params.traceBoost
    setColor(shared.uColorDeep, params.colorDeep)
    setColor(shared.uColorViolet, params.colorViolet)
    setColor(shared.uColorLavender, params.colorLavender)
    setColor(shared.uColorMagenta, params.colorMagenta)
    setColor(shared.uHaloColor, params.haloColor)
    setColor(shared.uHubCore, params.hubCore)

    const particle = this.particleMaterial.uniforms
    particle.uSizeMin.value = params.sizeMin
    particle.uSizeMax.value = params.sizeMax
    particle.uHubSize.value = params.hubSizeMultiplier
    // A 28px mark gets the same world-space dots as a 600px one, which is invisible; lift
    // them so the small version still reads as a network rather than a haze.
    particle.uSizeScale.value = params.sizeScale * (this.small ? 1.8 : 1)
    particle.uTwinkleSpeed.value = params.twinkleSpeed
    particle.uTwinkleAmount.value = params.twinkleAmount
    particle.uOpacity.value = params.particleOpacity

    const edge = this.edgeMaterial.uniforms
    edge.uOpacity.value = params.edgeOpacity
    setColor(edge.uEdgeColor, params.edgeColor)

    this.post.setParams(this.small ? { ...params, bloomEnabled: false } : params)

    if (this.camera.fov !== params.fov) {
      this.camera.fov = params.fov
      this.camera.updateProjectionMatrix()
    }
    // Moving a camera slider is an explicit instruction, so it overrides a manual orbit —
    // unlike a resize, which must not yank the view the user dragged to.
    const framing = (p: LogoParams) => `${p.fov}|${p.zoom}|${p.azimuth}|${p.elevation}`
    if (framing(params) !== framing(previous)) this.placeCamera()
  }

  setSize(cssWidth: number, cssHeight: number, pixelRatio: number): void {
    if (this.disposed) return
    this.width = Math.max(1, cssWidth)
    this.height = Math.max(1, cssHeight)
    this.pixelRatio = pixelRatio
    this.renderer.setPixelRatio(pixelRatio)
    this.renderer.setSize(this.width, this.height, false)
    this.post.setSize(this.width * pixelRatio, this.height * pixelRatio)
    this.camera.aspect = this.width / this.height
    this.camera.updateProjectionMatrix()
    if (!this.userMovedCamera) this.placeCamera()
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    cancelAnimationFrame(this.frame)
    this.controls?.dispose()
    this.disposeGeometry()
    this.particleMaterial.dispose()
    this.edgeMaterial.dispose()
    this.sprite.dispose()
    this.post.dispose()
    this.renderer.dispose()
    // React's StrictMode mounts effects twice in development, so a leaked context here
    // would cost a real GPU context on every hot reload.
    this.renderer.forceContextLoss()
  }

  /**
   * Frame the glyph, fitting whichever axis is tighter so it never crops on a wide or a
   * tall container. `zoom` then scales that fit — it is the one number that decides how
   * big the mark reads inside whatever box it is given.
   */
  private placeCamera(): void {
    const tan = Math.tan((this.camera.fov * Math.PI) / 360)
    // Fit whichever axis is tighter, so the mark never crops on a wide or a tall
    // container, then let `zoom` scale that. zoom = 1 puts the letter exactly edge to
    // edge, which is the geometric maximum for the box it is given.
    //
    // What can still overhang at 1 is the trace pulse, not the letter: `bubbleZBias`
    // aims the push at the camera, and a particle riding the wave is briefly nearer, so
    // perspective throws it outward on screen. Clearing that costs real size, so it is a
    // `zoom` value the caller picks rather than a margin baked in here.
    const fitDistance = Math.max(
      0.5 / tan,
      GLYPH_HALF_WIDTH / (tan * this.camera.aspect),
    )
    const distance = fitDistance / Math.max(this.params.zoom, 0.05)
    const azimuth = (this.params.azimuth * Math.PI) / 180
    const elevation = (this.params.elevation * Math.PI) / 180
    this.camera.position.set(
      Math.sin(azimuth) * Math.cos(elevation) * distance,
      Math.sin(elevation) * distance,
      Math.cos(azimuth) * Math.cos(elevation) * distance,
    )
    this.camera.lookAt(0, 0, 0)
    this.controls?.target.set(0, 0, 0)
    this.controls?.update()
  }

  private disposeGeometry(): void {
    for (const object of [this.points, this.lines]) {
      if (!object) continue
      this.scene.remove(object)
      object.geometry.dispose()
    }
    this.points = null
    this.lines = null
  }

  /**
   * Advance the wavefront: it runs from just before 0 to just past 1 so the wave enters
   * and leaves off the ends of the letter instead of popping into existence at the tip,
   * then holds for the pause before starting over.
   */
  private advanceTrace(delta: number): void {
    const p = this.params
    const span = p.traceWaveWidth * 2
    if (!p.traceLoop) {
      this.shared.uTraceHead.value = -span
      return
    }
    this.traceClock += delta
    const cycle = p.traceDuration + p.tracePause
    const t = this.traceClock % cycle
    if (t > p.traceDuration) {
      this.shared.uTraceHead.value = 1 + span
      return
    }
    this.shared.uTraceHead.value = -span + (t / p.traceDuration) * (1 + span * 2)
  }

  private loop(now: number): void {
    if (this.disposed) return
    this.frame = requestAnimationFrame(this.loop)
    const delta = this.lastTime === 0 ? 0 : Math.min((now - this.lastTime) / 1000, 0.1)
    this.lastTime = now
    this.elapsed += delta

    this.shared.uTime.value = this.elapsed
    this.advanceTrace(delta)

    this.controls?.update()

    // Point size is a world-space diameter, so the attenuation term carries the drawing
    // buffer height — which is where devicePixelRatio actually enters the picture.
    const buffer = this.height * this.pixelRatio
    this.particleMaterial.uniforms.uAttenuation.value =
      buffer / (2 * Math.tan((this.camera.fov * Math.PI) / 360))

    this.post.render(this.renderer, this.scene, this.camera)
  }
}
