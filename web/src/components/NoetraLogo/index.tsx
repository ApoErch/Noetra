import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { DEFAULT_PARAMS, structuralKey, type LogoParams } from './params'
import { LogoEngine, SMALL_SIZE } from './engine'

// Only loaded when someone actually opens the dev panel, so leva and its Radix tree stay
// out of the bundle every real page ships.
const DevPanel = lazy(() => import('./DevPanel'))

type Props = {
  /** Shows the Leva panel and enables OrbitControls. Off for embedded marks. */
  devMode?: boolean
  className?: string
  /** Overrides for individual parameters, read once at mount. */
  params?: Partial<LogoParams>
}

/**
 * The Noetra "N" as a volumetric particle network.
 *
 * Fills its container, renders on a transparent canvas, and cleans up its WebGL context
 * on unmount. `devMode` is the only difference between the tuning rig and the embedded
 * mark: the same engine runs either way.
 */
export function NoetraLogo({ devMode = false, className, params }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const engineRef = useRef<LogoEngine | null>(null)
  const builtKeyRef = useRef('')

  const [live, setLive] = useState<LogoParams>(() => ({ ...DEFAULT_PARAMS, ...params }))
  const [unsupported, setUnsupported] = useState(false)
  const liveRef = useRef(live)
  liveRef.current = live

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // The canvas is created here rather than rendered by React, because disposing an
    // engine force-loses its WebGL context and a canvas can never get another one. Under
    // StrictMode the effect runs twice, so a React-owned canvas would hand the second
    // engine a permanently dead context — which is exactly the "no WebGL" path.
    const canvas = document.createElement('canvas')
    canvas.style.display = 'block'
    canvas.style.width = '100%'
    canvas.style.height = '100%'
    if (!devMode) canvas.style.pointerEvents = 'none'
    container.appendChild(canvas)

    const rect = container.getBoundingClientRect()
    const small = rect.width > 0 && Math.min(rect.width, rect.height) < SMALL_SIZE

    // No WebGL, a blocked context, a driver that will not give three its capabilities —
    // whatever the reason, a logo must not take the page down with it, so fall back to a
    // plain wordmark instead of letting the error reach React.
    let engine: LogoEngine
    try {
      engine = new LogoEngine(canvas, liveRef.current, devMode, small)
    } catch (error) {
      console.warn('Noetra logo: falling back to the flat mark —', error)
      canvas.remove()
      setUnsupported(true)
      return
    }
    engineRef.current = engine
    builtKeyRef.current = structuralKey(liveRef.current)

    const applySize = () => {
      const current = container.getBoundingClientRect()
      engine.setSize(current.width, current.height, Math.min(window.devicePixelRatio || 1, 2))
    }
    applySize()
    const observer = new ResizeObserver(applySize)
    observer.observe(container)

    return () => {
      observer.disconnect()
      engine.dispose()
      canvas.remove()
      engineRef.current = null
    }
  }, [devMode])

  // Uniforms and material properties: straight through, no rebuild, next frame.
  useEffect(() => {
    engineRef.current?.applyParams(live)
  }, [live])

  // Structural changes resample the whole cloud, so they wait for the slider to settle —
  // otherwise a drag would queue a rebuild per pixel of travel.
  const key = structuralKey(live)
  useEffect(() => {
    if (builtKeyRef.current === key) return
    const timer = setTimeout(() => {
      const engine = engineRef.current
      if (!engine) return
      builtKeyRef.current = key
      engine.rebuild(liveRef.current)
      engine.applyParams(liveRef.current)
    }, 120)
    return () => clearTimeout(timer)
  }, [key])

  return (
    <div ref={containerRef} className={`relative ${className ?? ''}`}>
      {/* Drawn, not an image file: the fallback exists for machines that cannot render
          the mark at all, and shipping an asset only they would ever load is waste. */}
      {unsupported && (
        <span
          aria-label="Noetra"
          className="flex h-full w-full items-center justify-center font-semibold text-violet-400"
          style={{ fontSize: '60%' }}
        >
          N
        </span>
      )}
      {devMode && !unsupported && (
        <Suspense fallback={null}>
          <DevPanel value={live} onChange={setLive} />
        </Suspense>
      )}
    </div>
  )
}
