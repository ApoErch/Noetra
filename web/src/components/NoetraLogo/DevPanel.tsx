import { useEffect, useRef } from 'react'
import { Leva, folder, useControls } from 'leva'
import { DEFAULT_PARAMS, type LogoParams } from './params'

type Props = {
  value: LogoParams
  onChange: (params: LogoParams) => void
}

const d = DEFAULT_PARAMS
const range = (value: number, min: number, max: number, step: number) => ({ value, min, max, step })

/**
 * The live tuning panel.
 *
 * Its own module, lazily imported, for two reasons: hooks cannot be called conditionally,
 * so `useControls` needs a component that only mounts in dev mode; and leva then never
 * reaches a production bundle.
 *
 * One `useControls` call for every folder, because leva flattens folder contents into a
 * single returned object — which is already the shape `LogoParams` wants.
 */
export default function DevPanel({ value, onChange }: Props) {
  const controls = useControls({
    Structure: folder({
      seed: range(d.seed, 1, 999, 1),
      nodeCount: range(d.nodeCount, 300, 6000, 50),
      strokeWidth: range(d.strokeWidth, 0.12, 0.34, 0.005),
      extrusionDepth: range(d.extrusionDepth, 0.05, 0.5, 0.005),
      shellRatio: range(d.shellRatio, 0, 0.5, 0.01),
      shellInset: range(d.shellInset, 0.005, 0.06, 0.001),
      haloRatio: range(d.haloRatio, 0, 0.4, 0.01),
      haloDistance: range(d.haloDistance, 0.01, 0.15, 0.005),
      hubRatio: range(d.hubRatio, 0, 0.25, 0.005),
    }),
    Edges: folder(
      {
        edgeNeighbors: range(d.edgeNeighbors, 2, 8, 1),
        edgeMaxDistance: range(d.edgeMaxDistance, 0.02, 0.2, 0.005),
        edgeOpacity: range(d.edgeOpacity, 0, 1, 0.01),
        edgeColor: d.edgeColor,
      },
      { collapsed: true },
    ),
    Particles: folder(
      {
        sizeMin: range(d.sizeMin, 0.002, 0.04, 0.001),
        sizeMax: range(d.sizeMax, 0.004, 0.08, 0.001),
        sizeScale: range(d.sizeScale, 0.2, 4, 0.05),
        hubSizeMultiplier: range(d.hubSizeMultiplier, 1, 6, 0.1),
        particleOpacity: range(d.particleOpacity, 0, 2, 0.02),
        twinkleSpeed: range(d.twinkleSpeed, 0, 8, 0.1),
        twinkleAmount: range(d.twinkleAmount, 0, 1, 0.01),
      },
      { collapsed: true },
    ),
    Colours: folder(
      {
        colorDeep: d.colorDeep,
        colorViolet: d.colorViolet,
        colorLavender: d.colorLavender,
        colorMagenta: d.colorMagenta,
        hubCore: d.hubCore,
        haloColor: d.haloColor,
      },
      { collapsed: true },
    ),
    Trace: folder({
      traceLoop: d.traceLoop,
      traceDuration: range(d.traceDuration, 0.5, 12, 0.1),
      traceWaveWidth: range(d.traceWaveWidth, 0.01, 0.4, 0.005),
      tracePause: range(d.tracePause, 0, 5, 0.1),
      traceGlow: range(d.traceGlow, 0, 2, 0.05),
      traceBoost: range(d.traceBoost, 0, 8, 0.1),
    }),
    Bubble: folder({
      bubbleStrength: range(d.bubbleStrength, 0, 0.3, 0.002),
      bubbleZBias: range(d.bubbleZBias, 0, 1, 0.02),
    }),
    Glow: folder(
      {
        bloomEnabled: d.bloomEnabled,
        bloomStrength: range(d.bloomStrength, 0, 4, 0.05),
        bloomThreshold: range(d.bloomThreshold, 0, 2, 0.01),
        bloomRadius: range(d.bloomRadius, 0.2, 6, 0.1),
        fringeStrength: range(d.fringeStrength, 0, 0.03, 0.0002),
      },
      { collapsed: true },
    ),
    Camera: folder({
      zoom: range(d.zoom, 0.3, 2, 0.01),
      azimuth: range(d.azimuth, -180, 180, 1),
      elevation: range(d.elevation, -89, 89, 1),
      fov: range(d.fov, 15, 80, 1),
    }),
  })

  // leva hands back a fresh object every render, so publish on a value signature rather
  // than on object identity — otherwise this would loop forever.
  const latest = useRef<LogoParams>(value)
  latest.current = { ...value, ...controls }
  const signature = JSON.stringify(controls)
  useEffect(() => {
    onChange(latest.current)
  }, [signature, onChange])

  return <Leva collapsed={false} titleBar={{ title: 'Noetra logo' }} />
}
