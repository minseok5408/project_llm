import { createLowlight } from 'lowlight';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import sql from 'highlight.js/lib/languages/sql';
import yaml from 'highlight.js/lib/languages/yaml';
import markdown from 'highlight.js/lib/languages/markdown';
import java from 'highlight.js/lib/languages/java';
import go from 'highlight.js/lib/languages/go';
import rust from 'highlight.js/lib/languages/rust';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import csharp from 'highlight.js/lib/languages/csharp';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import diff from 'highlight.js/lib/languages/diff';
import { canHighlightCode, codeLanguage } from './code-highlight.ts';
import type { CodeToken } from './code-highlight.ts';

// 자동 언어 탐지와 전체 문법 등록을 피하고 지원하는 코드만 분석한다.
const lowlight = createLowlight({
  javascript,
  typescript,
  python,
  bash,
  json,
  css,
  xml,
  sql,
  yaml,
  markdown,
  java,
  go,
  rust,
  c,
  cpp,
  csharp,
  dockerfile,
  diff,
});
type SyntaxNode = ReturnType<typeof lowlight.highlight>['children'][number];

export function highlightCode(text: string, language: string): CodeToken[] {
  const normalized = codeLanguage(language);
  if (!normalized || !canHighlightCode(text, language)) return [text];
  try {
    const root = lowlight.highlight(normalized, text);
    let remaining = 8_000;
    const original: string[] = [];
    const convert = (nodes: SyntaxNode[], depth = 0): CodeToken[] => {
      if (depth > 40) throw new Error('코드 색상 중첩 한도');
      return nodes.map((node) => {
        if (--remaining < 0) throw new Error('코드 색상 토큰 한도');
        if (node.type === 'text') {
          original.push(node.value);
          return node.value;
        }
        // 문법 결과도 텍스트·span만 허용해 임의 HTML·속성을 렌더링하지 않는다.
        if (node.type !== 'element' || node.tagName !== 'span') {
          throw new Error('지원하지 않는 코드 색상 노드');
        }
        const classes = node.properties.className;
        return {
          className: Array.isArray(classes)
            ? classes
                .filter(
                  (name) => typeof name === 'string' && /^[\w-]+$/.test(name),
                )
                .join(' ')
            : '',
          children: convert(node.children, depth + 1),
        };
      });
    };
    const tokens = convert(root.children);
    return original.join('') === text ? tokens : [text];
  } catch {
    return [text];
  }
}
