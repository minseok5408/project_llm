export type CodeToken = string | { className: string; children: CodeToken[] };
export type CodeHighlighter = (text: string, language: string) => CodeToken[];

export const CODE_HIGHLIGHT_MAX_CHARS = 20_000;
export const CODE_HIGHLIGHT_MAX_LINES = 500;
export const CODE_HIGHLIGHT_DELAY_MS = 150;

const languages: Record<string, string> = {
  javascript: 'javascript',
  js: 'javascript',
  jsx: 'javascript',
  typescript: 'typescript',
  ts: 'typescript',
  tsx: 'typescript',
  python: 'python',
  py: 'python',
  bash: 'bash',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  json: 'json',
  css: 'css',
  html: 'xml',
  xml: 'xml',
  svg: 'xml',
  sql: 'sql',
  yaml: 'yaml',
  yml: 'yaml',
  markdown: 'markdown',
  md: 'markdown',
  java: 'java',
  go: 'go',
  rust: 'rust',
  rs: 'rust',
  c: 'c',
  cpp: 'cpp',
  'c++': 'cpp',
  csharp: 'csharp',
  cs: 'csharp',
  dockerfile: 'dockerfile',
  docker: 'dockerfile',
  diff: 'diff',
};

export function codeLanguage(language?: string): string | null {
  const key = language?.toLowerCase();
  return key && Object.hasOwn(languages, key) ? languages[key] : null;
}

export function canHighlightCode(text: string, language?: string): boolean {
  return Boolean(
    codeLanguage(language) &&
    text.length > 0 &&
    text.length <= CODE_HIGHLIGHT_MAX_CHARS &&
    text.split('\n').length <= CODE_HIGHLIGHT_MAX_LINES,
  );
}

async function loadHighlighter(): Promise<CodeHighlighter> {
  const engine = await import('./code-highlight-engine.ts');
  return engine.highlightCode;
}

export function scheduleCodeHighlight(
  text: string,
  language: string | undefined,
  onReady: (tokens: CodeToken[]) => void,
  load: () => Promise<CodeHighlighter> = loadHighlighter,
): () => void {
  const normalized = codeLanguage(language);
  if (!normalized || !canHighlightCode(text, language)) return () => {};
  let active = true;
  // 스트리밍이 잠시 멈췄을 때만 문법을 읽고 이전 본문·닫힌 화면의 결과는 버린다.
  const timer = setTimeout(() => {
    if (!active) return;
    void load()
      .then((highlight) => {
        if (!active) return;
        const tokens = highlight(text, normalized);
        if (active) onReady(tokens);
      })
      .catch(() => {
        // 문법 로딩·분석 실패는 이미 표시한 원문과 복사를 그대로 유지한다.
      });
  }, CODE_HIGHLIGHT_DELAY_MS);
  return () => {
    active = false;
    clearTimeout(timer);
  };
}
