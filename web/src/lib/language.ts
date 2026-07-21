const EXTENSION_LANGUAGE: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  json: 'json',
  md: 'markdown',
  html: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  yml: 'yaml',
  yaml: 'yaml',
  toml: 'toml',
  sh: 'shell',
  bash: 'shell',
  ps1: 'powershell',
  sql: 'sql',
  graphql: 'graphql',
  xml: 'xml',
  ini: 'ini',
  dockerfile: 'dockerfile',
  go: 'go',
  rs: 'rust',
  rb: 'ruby',
  php: 'php',
  java: 'java',
  kt: 'kotlin',
  swift: 'swift',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  hpp: 'cpp',
  cs: 'csharp',
  lua: 'lua',
  r: 'r',
}

/** Guess a Monaco language id from a file's extension, for syntax highlighting. Falls back to plain text. */
export function languageForPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return EXTENSION_LANGUAGE[ext] ?? 'plaintext'
}
