import { useState, type ReactNode } from 'react'
import Markdown from 'react-markdown'
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

const CITATION_RE = /\[([^[\]\s:]+):(\d+)(?:-(\d+))?\]/g

/** One markdown block (a paragraph, a list, a code fence — see `splitBlocks`) plus the citations it named. */
type Block = { text: string; citations: Citation[] }

/**
 * Split content into blocks on blank lines (a fenced code block's internal blank lines don't
 * count), then strip any `[path:a-b]` bracket the backend verified out of each block's text,
 * collecting it instead — citations render as a chip row below the block, not inline in the
 * sentence. A bracket the backend didn't back stays as plain text, matching the old behaviour.
 */
function splitBlocks(content: string, citations: Citation[]): Block[] {
  const known = new Map(citations.map((c) => [formatCitation(c), c]))

  const rawBlocks: string[] = []
  let current: string[] = []
  let inFence = false
  for (const line of content.split('\n')) {
    if (/^\s*```/.test(line)) inFence = !inFence
    if (!inFence && line.trim() === '') {
      if (current.length > 0) rawBlocks.push(current.join('\n'))
      current = []
    } else {
      current.push(line)
    }
  }
  if (current.length > 0) rawBlocks.push(current.join('\n'))

  return rawBlocks.map((raw) => {
    const found: Citation[] = []
    const seen = new Set<string>()
    const text = raw
      .replace(CITATION_RE, (whole, path: string, start: string, end?: string) => {
        const key = `${path}:${end && end !== start ? `${start}-${end}` : start}`
        const citation = known.get(key)
        if (!citation) return whole
        if (!seen.has(key)) {
          seen.add(key)
          found.push(citation)
        }
        return ''
      })
      .replace(/[ \t]+([.,;:!?])/g, '$1')
      .replace(/[ \t]{2,}/g, ' ')
      .trim()
    return { text, citations: found }
  })
}

const markdownComponents = {
  code: ({ className, children }: { className?: string; children?: ReactNode }) => (
    <code className={`rounded bg-zinc-900 px-1 py-0.5 font-mono text-[13px] ${className ?? ''}`}>{children}</code>
  ),
  pre: ({ children }: { children?: ReactNode }) => (
    <pre className="my-2 overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-900 p-3 font-mono text-[13px]">
      {children}
    </pre>
  ),
  a: ({ href, children }: { href?: string; children?: ReactNode }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-indigo-400 underline">
      {children}
    </a>
  ),
  p: ({ children }: { children?: ReactNode }) => <p className="my-2">{children}</p>,
  ul: ({ children }: { children?: ReactNode }) => (
    <ul className="my-3 list-disc space-y-1.5 pl-6 leading-relaxed">{children}</ul>
  ),
  ol: ({ children }: { children?: ReactNode }) => (
    <ol className="my-3 list-decimal space-y-1.5 pl-6 leading-relaxed">{children}</ol>
  ),
  h1: ({ children }: { children?: ReactNode }) => <h3 className="mt-3 mb-1 font-semibold text-white">{children}</h3>,
  h2: ({ children }: { children?: ReactNode }) => <h3 className="mt-3 mb-1 font-semibold text-white">{children}</h3>,
  h3: ({ children }: { children?: ReactNode }) => <h3 className="mt-3 mb-1 font-semibold text-white">{children}</h3>,
}

/** One chat message: markdown answer with reference chips, plus the tool steps behind it. */
export function MessageBubble({ role, content, citations, toolTrace, streaming, onOpenCitation }: Props) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-[15px] text-white">
          {content}
        </div>
      </div>
    )
  }

  const blocks = splitBlocks(content, citations)
  // Citations never mentioned in any block (older answers, or a fallback set) still need to
  // reach the user — shown as one trailing row rather than silently dropped.
  const used = new Set(blocks.flatMap((b) => b.citations.map(formatCitation)))
  const leftover = citations.filter((c) => !used.has(formatCitation(c)))

  return (
    <div className="flex flex-col gap-2">
      {toolTrace.length > 0 && <StepsBlock steps={toolTrace} live={Boolean(streaming)} />}
      {(content || !streaming) && (
        <div className="prose-chat max-w-none text-[15px] leading-relaxed text-zinc-200">
          {blocks.map((block, i) => (
            <div key={i}>
              {block.text && <Markdown components={markdownComponents}>{block.text}</Markdown>}
              {block.citations.length > 0 && <CitationRow citations={block.citations} onOpenCitation={onOpenCitation} />}
            </div>
          ))}
        </div>
      )}
      {streaming && !content && toolTrace.length === 0 && <p className="text-sm text-zinc-500">Thinking…</p>}
      {!streaming && leftover.length > 0 && <CitationRow citations={leftover} onOpenCitation={onOpenCitation} />}
    </div>
  )
}

/** A row of tappable `path:lines` reference chips, each opening the file at that range. */
function CitationRow({ citations, onOpenCitation }: { citations: Citation[]; onOpenCitation: (c: Citation) => void }) {
  return (
    <div className="mt-2 mb-1 flex flex-wrap gap-2">
      {citations.map((c) => (
        <button
          key={formatCitation(c)}
          type="button"
          onClick={() => onOpenCitation(c)}
          className="inline-flex items-center rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-1.5 font-mono text-[13px] text-indigo-300 transition hover:bg-indigo-500/20"
        >
          {formatCitation(c)}
        </button>
      ))}
    </div>
  )
}

/** The agent's tool calls, collapsed once the answer is in so they don't crowd it out. */
function StepsBlock({ steps, live }: { steps: ToolStep[]; live: boolean }) {
  const [open, setOpen] = useState(false)
  const expanded = live || open
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 text-xs text-zinc-400">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left hover:text-zinc-200"
      >
        <span className={`transition ${expanded ? 'rotate-90' : ''}`}>▸</span>
        {live ? 'Working…' : `${steps.length} step${steps.length === 1 ? '' : 's'}`}
      </button>
      {expanded && (
        <ul className="space-y-1 border-t border-zinc-800 px-4 py-2.5">
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
