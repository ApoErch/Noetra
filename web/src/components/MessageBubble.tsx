import { useState } from 'react'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import { formatCitation, type Citation, type ToolStep } from '../lib/chat'

type Props = {
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  toolTrace: ToolStep[]
  /** True while this assistant message is still being streamed. */
  streaming?: boolean
  onOpenCitation: (citation: Citation) => void
}

const CITATION_SCHEME = 'noetra-cite:'
const CITATION_RE = /\[([^[\]\s:]+):(\d+)(?:-(\d+))?\]/g

/**
 * Turn the agent's `[path:a-b]` citations into markdown links with a private URL scheme, so
 * the markdown renderer treats them as links and we can render those links as buttons. Any
 * citation the backend verified has an entry in `citations`; the rest stay plain text (the
 * backend already un-bracketed those, so they never reach here as `[...]`).
 */
function linkifyCitations(content: string, citations: Citation[]): string {
  const known = new Set(citations.map(formatCitation))
  return content.replace(CITATION_RE, (whole, path: string, start: string, end?: string) => {
    const key = `${path}:${end && end !== start ? `${start}-${end}` : start}`
    return known.has(key) ? `[${key}](${CITATION_SCHEME}${encodeURIComponent(key)})` : whole
  })
}

/** One chat message: markdown answer with clickable citations, plus the tool steps behind it. */
export function MessageBubble({ role, content, citations, toolTrace, streaming, onOpenCitation }: Props) {
  const byKey = new Map(citations.map((c) => [formatCitation(c), c]))
  // Citations render inline, next to the sentence they support. The chip row below is only a
  // fallback for an answer that has citations but wrote none inline (older models do this).
  const inlineCount = citations.filter((c) => content.includes(`[${formatCitation(c)}]`)).length

  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-sm text-white">
          {content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {toolTrace.length > 0 && <StepsBlock steps={toolTrace} live={Boolean(streaming)} />}
      {(content || !streaming) && (
        <div className="prose-chat max-w-none text-sm leading-relaxed text-zinc-200">
          <Markdown
            // react-markdown drops hrefs with unknown schemes by default, which turned our
            // citation links into href="" — a click then reloaded the app to the dashboard.
            urlTransform={(url) => (url.startsWith(CITATION_SCHEME) ? url : defaultUrlTransform(url))}
            components={{
              a: ({ href, children }) => {
                if (href?.startsWith(CITATION_SCHEME)) {
                  const key = decodeURIComponent(href.slice(CITATION_SCHEME.length))
                  const citation = byKey.get(key)
                  if (citation) return <CitationChip label={key} onClick={() => onOpenCitation(citation)} />
                }
                return (
                  <a href={href} target="_blank" rel="noreferrer" className="text-indigo-400 underline">
                    {children}
                  </a>
                )
              },
              code: ({ className, children }) => (
                <code className={`rounded bg-zinc-900 px-1 py-0.5 font-mono text-[13px] ${className ?? ''}`}>
                  {children}
                </code>
              ),
              pre: ({ children }) => (
                <pre className="my-2 overflow-x-auto rounded-md border border-zinc-800 bg-zinc-900 p-3 font-mono text-[13px]">
                  {children}
                </pre>
              ),
              p: ({ children }) => <p className="my-2">{children}</p>,
              ul: ({ children }) => <ul className="my-2 list-disc pl-5">{children}</ul>,
              ol: ({ children }) => <ol className="my-2 list-decimal pl-5">{children}</ol>,
              h1: ({ children }) => <h3 className="mt-3 mb-1 font-semibold text-white">{children}</h3>,
              h2: ({ children }) => <h3 className="mt-3 mb-1 font-semibold text-white">{children}</h3>,
              h3: ({ children }) => <h3 className="mt-3 mb-1 font-semibold text-white">{children}</h3>,
            }}
          >
            {linkifyCitations(content, citations)}
          </Markdown>
        </div>
      )}
      {streaming && !content && toolTrace.length === 0 && (
        <p className="text-sm text-zinc-500">Thinking…</p>
      )}
      {!streaming && citations.length > 0 && inlineCount === 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {citations.map((c) => (
            <CitationChip key={formatCitation(c)} label={formatCitation(c)} onClick={() => onOpenCitation(c)} />
          ))}
        </div>
      )}
    </div>
  )
}

/** A clickable `path:lines` chip that opens the file at that range. */
function CitationChip({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center rounded bg-indigo-500/10 px-1.5 py-0.5 font-mono text-xs text-indigo-400 ring-1 ring-indigo-500/20 transition hover:bg-indigo-500/20"
    >
      {label}
    </button>
  )
}

/** The agent's tool calls, collapsed once the answer is in so they don't crowd it out. */
function StepsBlock({ steps, live }: { steps: ToolStep[]; live: boolean }) {
  const [open, setOpen] = useState(false)
  const expanded = live || open
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/60 text-xs text-zinc-400">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:text-zinc-200"
      >
        <span className={`transition ${expanded ? 'rotate-90' : ''}`}>▸</span>
        {live ? 'Working…' : `${steps.length} step${steps.length === 1 ? '' : 's'}`}
      </button>
      {expanded && (
        <ul className="border-t border-zinc-800 px-3 py-1.5">
          {steps.map((step, i) => (
            <li key={i} className="flex gap-2 py-0.5">
              <span className="shrink-0 font-mono text-zinc-500">{step.name}</span>
              <span className="truncate">{step.summary}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
