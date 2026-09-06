import {
  HalfFloatType,
  LinearFilter,
  NoBlending,
  Mesh,
  OrthographicCamera,
  PlaneGeometry,
  RGBAFormat,
  Scene,
  ShaderMaterial,
  Vector2,
  WebGLRenderTarget,
  type Camera,
  type WebGLRenderer,
} from 'three'

/**
 * Bloom, the chromatic fringe, and the alpha maths — one chain, ending in one composite.
 *
 * Why not `UnrealBloomPass`: its composite shader writes `alpha = 1.0`, so on a
 * transparent canvas the whole viewport comes out as an opaque black rectangle
 * (three.js #14104). The published fixes all monkey-patch a private shader string. Since
 * we also need a chromatic offset — the magenta/blue fringe the reference has around
 * every edge — the composite had to be ours anyway, and doing both in one pass is less
 * code than patching someone else's.
 *
 * The scene is drawn into a half-float target because additive glow accumulates well past
 * 1.0, and an 8-bit target would clip the highlights the bright pass is looking for.
 */

const QUAD_VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`

const BRIGHT_FRAGMENT = /* glsl */ `
uniform sampler2D tSource;
uniform float uThreshold;
varying vec2 vUv;

void main() {
  vec3 c = texture2D(tSource, vUv).rgb;
  float l = max(max(c.r, c.g), c.b);
  gl_FragColor = vec4(c * (max(l - uThreshold, 0.0) / max(l, 1e-4)), 1.0);
}
`

/** Five-tap separable Gaussian, run once per axis per level. */
const BLUR_FRAGMENT = /* glsl */ `
uniform sampler2D tSource;
uniform vec2 uDirection;
varying vec2 vUv;

void main() {
  vec3 sum = texture2D(tSource, vUv).rgb * 0.227027;
  sum += (texture2D(tSource, vUv + uDirection * 1.3846154).rgb +
          texture2D(tSource, vUv - uDirection * 1.3846154).rgb) * 0.3162162;
  sum += (texture2D(tSource, vUv + uDirection * 3.2307692).rgb +
          texture2D(tSource, vUv - uDirection * 3.2307692).rgb) * 0.0702702;
  gl_FragColor = vec4(sum, 1.0);
}
`

const COMPOSITE_FRAGMENT = /* glsl */ `
uniform sampler2D tScene;
uniform sampler2D tBloomNear;
uniform sampler2D tBloomFar;
uniform float uBloomStrength;
uniform float uFringe;
varying vec2 vUv;

void main() {
  // Radial per-channel offset: red pushed out, blue pulled in. On a silhouette this is
  // exactly the magenta rim and blue outer speckle the reference shows.
  vec2 offset = (vUv - 0.5) * uFringe;
  vec4 cr = texture2D(tScene, vUv + offset);
  vec4 cg = texture2D(tScene, vUv);
  vec4 cb = texture2D(tScene, vUv - offset);
  vec3 base = vec3(cr.r, cg.g, cb.b);
  float baseAlpha = max(max(cr.a, cg.a), cb.a);

  vec3 bloom = (texture2D(tBloomNear, vUv).rgb + texture2D(tBloomFar, vUv).rgb)
             * 0.5 * uBloomStrength;
  vec3 rgb = base + bloom;

  // Everything above wrote premultiplied colour onto a cleared, fully transparent buffer,
  // so alpha has to carry the glow too — otherwise bloom would be invisible against the
  // page. Tying it to luminance as well keeps the un-premultiply below stable.
  float lum = max(max(rgb.r, rgb.g), rgb.b);
  float alpha = clamp(max(baseAlpha, lum), 0.0, 1.0);
  if (alpha <= 0.0) discard;

  // The canvas is straight-alpha (premultipliedAlpha: false), so undo the premultiply.
  gl_FragColor = vec4(rgb / alpha, alpha);
}
`

export type PostParams = {
  bloomEnabled: boolean
  bloomStrength: number
  bloomThreshold: number
  bloomRadius: number
  fringeStrength: number
}

function makeTarget(width: number, height: number): WebGLRenderTarget {
  return new WebGLRenderTarget(Math.max(1, width), Math.max(1, height), {
    type: HalfFloatType,
    format: RGBAFormat,
    minFilter: LinearFilter,
    magFilter: LinearFilter,
    depthBuffer: false,
    stencilBuffer: false,
  })
}

/** The post chain: owns its render targets and the fullscreen quad that drives them. */
export class PostChain {
  private readonly quadScene = new Scene()
  private readonly quadCamera = new OrthographicCamera(-1, 1, 1, -1, 0, 1)
  private readonly quad: Mesh
  private readonly bright: ShaderMaterial
  private readonly blur: ShaderMaterial
  private readonly composite: ShaderMaterial

  private scene: WebGLRenderTarget
  private bright2: WebGLRenderTarget
  private near: [WebGLRenderTarget, WebGLRenderTarget]
  private far: [WebGLRenderTarget, WebGLRenderTarget]
  private needsClear = true
  private params: PostParams

  constructor(params: PostParams) {
    this.params = params
    this.bright = new ShaderMaterial({
      uniforms: { tSource: { value: null }, uThreshold: { value: params.bloomThreshold } },
      vertexShader: QUAD_VERTEX,
      fragmentShader: BRIGHT_FRAGMENT,
      depthTest: false,
      depthWrite: false,
    })
    this.blur = new ShaderMaterial({
      uniforms: { tSource: { value: null }, uDirection: { value: new Vector2() } },
      vertexShader: QUAD_VERTEX,
      fragmentShader: BLUR_FRAGMENT,
      depthTest: false,
      depthWrite: false,
    })
    this.composite = new ShaderMaterial({
      uniforms: {
        tScene: { value: null },
        tBloomNear: { value: null },
        tBloomFar: { value: null },
        uBloomStrength: { value: params.bloomEnabled ? params.bloomStrength : 0 },
        uFringe: { value: params.fringeStrength },
      },
      vertexShader: QUAD_VERTEX,
      fragmentShader: COMPOSITE_FRAGMENT,
      // The composite writes the finished image straight onto a cleared framebuffer, so
      // there is nothing to blend against — blending here would re-premultiply the alpha
      // we just undid.
      blending: NoBlending,
      depthTest: false,
      depthWrite: false,
    })
    this.quad = new Mesh(new PlaneGeometry(2, 2), this.composite)
    this.quad.frustumCulled = false
    this.quadScene.add(this.quad)

    this.scene = makeTarget(1, 1)
    this.bright2 = makeTarget(1, 1)
    this.near = [makeTarget(1, 1), makeTarget(1, 1)]
    this.far = [makeTarget(1, 1), makeTarget(1, 1)]
  }

  setParams(params: PostParams): void {
    this.params = params
    this.bright.uniforms.uThreshold.value = params.bloomThreshold
    this.composite.uniforms.uBloomStrength.value = params.bloomEnabled ? params.bloomStrength : 0
    this.composite.uniforms.uFringe.value = params.fringeStrength
  }

  /** Sizes are in device pixels — the caller has already applied the pixel ratio. */
  setSize(width: number, height: number): void {
    const w = Math.max(1, Math.round(width))
    const h = Math.max(1, Math.round(height))
    this.scene.setSize(w, h)
    const halfW = Math.max(1, Math.round(w / 2))
    const halfH = Math.max(1, Math.round(h / 2))
    const quarterW = Math.max(1, Math.round(w / 4))
    const quarterH = Math.max(1, Math.round(h / 4))
    this.bright2.setSize(halfW, halfH)
    for (const t of this.near) t.setSize(halfW, halfH)
    for (const t of this.far) t.setSize(quarterW, quarterH)
    this.needsClear = true
  }

  /** The scene target, so the caller can render into it. */
  get sceneTarget(): WebGLRenderTarget {
    return this.scene
  }

  private draw(renderer: WebGLRenderer, material: ShaderMaterial, target: WebGLRenderTarget | null): void {
    this.quad.material = material
    renderer.setRenderTarget(target)
    renderer.clear()
    renderer.render(this.quadScene, this.quadCamera)
  }

  private blurInto(
    renderer: WebGLRenderer,
    source: WebGLRenderTarget,
    pair: [WebGLRenderTarget, WebGLRenderTarget],
    radius: number,
  ): WebGLRenderTarget {
    const w = pair[0].width
    const h = pair[0].height
    this.blur.uniforms.tSource.value = source.texture
    this.blur.uniforms.uDirection.value.set(radius / w, 0)
    this.draw(renderer, this.blur, pair[0])
    this.blur.uniforms.tSource.value = pair[0].texture
    this.blur.uniforms.uDirection.value.set(0, radius / h)
    this.draw(renderer, this.blur, pair[1])
    return pair[1]
  }

  /** Render `scene` through the chain and composite it onto the canvas. */
  render(renderer: WebGLRenderer, scene: Scene, camera: Camera): void {
    renderer.setRenderTarget(this.scene)
    renderer.clear()
    renderer.render(scene, camera)

    if (this.params.bloomEnabled) {
      this.bright.uniforms.tSource.value = this.scene.texture
      this.draw(renderer, this.bright, this.bright2)
      // Blur the bright pass at half res, then take that result down another level, so
      // the glow has both a tight and a wide falloff instead of one flat smear.
      const nearResult = this.blurInto(renderer, this.bright2, this.near, this.params.bloomRadius)
      this.blurInto(renderer, nearResult, this.far, this.params.bloomRadius)
    } else if (this.needsClear) {
      // A freshly allocated target has undefined contents, and the composite still reads
      // both bloom textures even when the strength is zero.
      for (const target of [this.near[1], this.far[1]]) {
        renderer.setRenderTarget(target)
        renderer.clear()
      }
      this.needsClear = false
    }

    this.composite.uniforms.tScene.value = this.scene.texture
    this.composite.uniforms.tBloomNear.value = this.near[1].texture
    this.composite.uniforms.tBloomFar.value = this.far[1].texture
    this.draw(renderer, this.composite, null)
  }

  dispose(): void {
    this.quad.geometry.dispose()
    this.bright.dispose()
    this.blur.dispose()
    this.composite.dispose()
    this.scene.dispose()
    this.bright2.dispose()
    for (const t of this.near) t.dispose()
    for (const t of this.far) t.dispose()
  }
}
