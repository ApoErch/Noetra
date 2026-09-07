import { AddEquation, CanvasTexture, CustomBlending, OneFactor, ShaderMaterial, Vector3 } from 'three'

/**
 * The two materials the logo draws with, plus the sprite the particles use.
 *
 * Both premultiply colour by alpha themselves and use explicit `CustomBlending` rather
 * than `AdditiveBlending`. Three's additive preset multiplies source colour by source
 * alpha a second time, and — the part that matters here — we need destination alpha to
 * accumulate, because the canvas is transparent and alpha is what the page composites
 * against.
 */

/**
 * The wavefront, shared verbatim by particles and edges.
 *
 * `intensity` is a pure function of the vertex's own `aTraceT` against the head's current
 * position, which is why nothing needs a "return to rest" state: once the head moves past,
 * the same expression evaluates back to zero on its own.
 */
const GLSL_TRACE = /* glsl */ `
uniform float uTraceHead;
uniform float uWaveWidth;
uniform float uBubbleStrength;
uniform float uPushZBias;

attribute float aTraceT;
attribute vec3  aRadial;

float traceIntensity() {
  float w = max(uWaveWidth, 1e-4);
  float d = aTraceT - uTraceHead;
  return exp(-(d * d) / (w * w));
}

vec3 bubblePush(float intensity) {
  vec3 dir = mix(aRadial, vec3(0.0, 0.0, 1.0), uPushZBias);
  float len = length(dir);
  dir = len > 1e-5 ? dir / len : vec3(0.0, 0.0, 1.0);
  return dir * intensity * uBubbleStrength;
}
`

/**
 * Palette resolution, shared by particles and edges.
 *
 * Nodes store a position in the ramp and which population they belong to; the finished
 * colour is worked out here against uniforms, which is what keeps every colour stop and
 * the size range live sliders rather than a reason to resample the whole cloud.
 */
const GLSL_PALETTE = /* glsl */ `
attribute float aRampT;
attribute float aKind;
attribute float aAccent;

uniform vec3 uColorDeep;
uniform vec3 uColorViolet;
uniform vec3 uColorLavender;
uniform vec3 uColorMagenta;
uniform vec3 uHaloColor;
uniform vec3 uHubCore;

vec3 rampColor(float t) {
  float s = clamp(t, 0.0, 0.9999) * 2.0;
  return s < 1.0 ? mix(uColorDeep, uColorViolet, s) : mix(uColorViolet, uColorLavender, s - 1.0);
}

vec3 nodeColor() {
  if (aKind < 0.5) return mix(rampColor(aRampT), uColorMagenta, aAccent);
  if (aKind < 1.5) return mix(uColorLavender, vec3(1.0), 0.55);
  if (aKind < 2.5) return mix(uColorDeep, uHaloColor, min(aRampT * 1.4, 1.0));
  return uHubCore;
}
`

const PARTICLE_VERTEX = /* glsl */ `
${GLSL_TRACE}
${GLSL_PALETTE}

attribute float aSizeT;
attribute float aAlpha;
attribute float aSeed;

uniform float uTime;
uniform float uSizeMin;
uniform float uSizeMax;
uniform float uHubSize;
uniform float uSizeScale;
uniform float uAttenuation;
uniform float uTwinkleSpeed;
uniform float uTwinkleAmount;
uniform float uTraceGlow;
uniform float uTraceBoost;
uniform float uOpacity;

varying vec3  vColor;
varying float vAlpha;

void main() {
  float intensity = traceIntensity();
  vec4 mv = modelViewMatrix * vec4(position + bubblePush(intensity), 1.0);
  gl_Position = projectionMatrix * mv;

  float twinkle = 1.0 + uTwinkleAmount * sin(uTime * uTwinkleSpeed + aSeed * 6.2831853);
  float world = mix(uSizeMin, uSizeMax, aSizeT) * (aKind > 2.5 ? uHubSize : 1.0);
  float size = world * uSizeScale * twinkle * uAttenuation / max(-mv.z, 1e-3);
  gl_PointSize = clamp(size, 1.0, 64.0);

  vColor = mix(nodeColor(), uHubCore, clamp(intensity * uTraceGlow, 0.0, 1.0));
  vAlpha = clamp(aAlpha * uOpacity * (1.0 + intensity * uTraceBoost) * twinkle, 0.0, 6.0);
}
`

const PARTICLE_FRAGMENT = /* glsl */ `
uniform sampler2D uSprite;

varying vec3  vColor;
varying float vAlpha;

void main() {
  float mask = texture2D(uSprite, gl_PointCoord).a;
  if (mask < 0.003) discard;
  float a = mask * vAlpha;
  gl_FragColor = vec4(vColor * a, a);
}
`

const EDGE_VERTEX = /* glsl */ `
${GLSL_TRACE}
${GLSL_PALETTE}

attribute float aAlpha;

uniform float uTraceBoost;
uniform float uTraceGlow;
uniform float uOpacity;

varying vec3  vColor;
varying float vAlpha;

void main() {
  float intensity = traceIntensity();
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position + bubblePush(intensity), 1.0);
  vColor = mix(nodeColor(), uHubCore, clamp(intensity * uTraceGlow, 0.0, 1.0));
  vAlpha = clamp(aAlpha * uOpacity * (1.0 + intensity * uTraceBoost), 0.0, 6.0);
}
`

const EDGE_FRAGMENT = /* glsl */ `
uniform vec3 uEdgeColor;

varying vec3  vColor;
varying float vAlpha;

void main() {
  // Half the node's own colour, half the flat edge violet: pure per-node colouring makes
  // the mesh look like scattered confetti, pure flat looks dead.
  vec3 col = mix(uEdgeColor, vColor, 0.5);
  gl_FragColor = vec4(col * vAlpha, vAlpha);
}
`

/** A soft radial dot: crisp core, quick falloff, long faint tail for the glow. */
export function createSpriteTexture(): CanvasTexture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  if (ctx) {
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
    gradient.addColorStop(0, 'rgba(255,255,255,1)')
    gradient.addColorStop(0.22, 'rgba(255,255,255,0.95)')
    gradient.addColorStop(0.45, 'rgba(255,255,255,0.25)')
    gradient.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, size, size)
  }
  const texture = new CanvasTexture(canvas)
  texture.needsUpdate = true
  return texture
}

/** Uniforms every trace-driven material shares, created once and handed to both. */
export function createTraceUniforms() {
  return {
    uTime: { value: 0 },
    uTraceHead: { value: -1 },
    uWaveWidth: { value: 0.09 },
    uBubbleStrength: { value: 0.045 },
    uPushZBias: { value: 0.35 },
    uTraceGlow: { value: 1 },
    uTraceBoost: { value: 2.5 },
    uColorDeep: { value: new Vector3(0.545, 0.361, 0.965) },
    uColorViolet: { value: new Vector3(0.655, 0.545, 0.98) },
    uColorLavender: { value: new Vector3(0.769, 0.71, 0.992) },
    uColorMagenta: { value: new Vector3(0.851, 0.275, 0.937) },
    uHaloColor: { value: new Vector3(0.267, 0.2, 1) },
    uHubCore: { value: new Vector3(1, 0.94, 1) },
  }
}

type TraceUniforms = ReturnType<typeof createTraceUniforms>

export function createParticleMaterial(sprite: CanvasTexture, shared: TraceUniforms): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      ...shared,
      uSprite: { value: sprite },
      uSizeMin: { value: 0.009 },
      uSizeMax: { value: 0.02 },
      uHubSize: { value: 2.4 },
      uSizeScale: { value: 1 },
      uAttenuation: { value: 1000 },
      uTwinkleSpeed: { value: 1.6 },
      uTwinkleAmount: { value: 0.15 },
      uOpacity: { value: 0.9 },
    },
    vertexShader: PARTICLE_VERTEX,
    fragmentShader: PARTICLE_FRAGMENT,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: CustomBlending,
    blendEquation: AddEquation,
    blendSrc: OneFactor,
    blendDst: OneFactor,
  })
}

export function createEdgeMaterial(shared: TraceUniforms): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      ...shared,
      uOpacity: { value: 0.22 },
      uEdgeColor: { value: new Vector3(0.655, 0.545, 0.98) },
    },
    vertexShader: EDGE_VERTEX,
    fragmentShader: EDGE_FRAGMENT,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: CustomBlending,
    blendEquation: AddEquation,
    blendSrc: OneFactor,
    blendDst: OneFactor,
  })
}
