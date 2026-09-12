import assert from 'node:assert/strict';
import { register } from 'node:module';
import { it } from 'node:test';
import { Children, createElement, isValidElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  CODE_HIGHLIGHT_DELAY_MS,
  CODE_HIGHLIGHT_MAX_CHARS,
  CODE_HIGHLIGHT_MAX_LINES,
  canHighlightCode,
  codeLanguage,
  scheduleCodeHighlight,
} from '../components/content/code-highlight.ts';
import { highlightCode } from '../components/content/code-highlight-engine.ts';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { CodeBlockView, CodeTokens } =
  await import('../components/content/code-block.tsx');
const { MarkdownText } =
  await import('../components/content/markdown-text.tsx');
const { CopyButton } = await import('../components/content/copy-button.tsx');
const flush = () => new Promise((resolve) => setImmediate(resolve));
const originalText = (tokens) =>
  tokens
    .map((token) =>
      typeof token === 'string' ? token : originalText(token.children),
    )
    .join('');
const render = (component, props) =>
  renderToStaticMarkup(createElement(component, props));
function elements(node) {
  if (!isValidElement(node)) return [];
  return [node, ...Children.toArray(node.props.children).flatMap(elements)];
}

it('지원 언어의 별칭과 대소문자는 같은 문법을 사용하고 모르는 언어는 추측하지 않는다', () => {
  for (const [alias, language] of Object.entries({
    JS: 'javascript',
    jsx: 'javascript',
    tsx: 'typescript',
    PY: 'python',
    sh: 'bash',
    html: 'xml',
    yml: 'yaml',
    md: 'markdown',
    'c++': 'cpp',
    cs: 'csharp',
  })) {
    assert.equal(codeLanguage(alias), language);
    const code = 'const answer = "한글";\n';
    assert.deepEqual(highlightCode(code, alias), highlightCode(code, language));
  }
  for (const language of [
    undefined,
    '',
    'plaintext',
    'unknown-language',
    '__proto__',
    'constructor',
  ]) {
    assert.equal(codeLanguage(language), null);
    assert.equal(canHighlightCode('const x = 1;', language), false);
  }
});

it('실제 문법의 색상 토큰은 공백·줄바꿈·한글·HTML 코드 원문을 보존한다', () => {
  const code = 'def greet():\n    return "<script>안녕</script>"\n';
  const tokens = highlightCode(code, 'python');
  assert.equal(originalText(tokens), code);
  const html = render(CodeTokens, { tokens });
  assert.match(html, /class="hljs-keyword"/);
  assert.match(html, /class="hljs-string"/);
  assert.match(html, /&lt;script&gt;안녕&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

it('HTML 문법도 태그·이벤트·실행 URL을 코드 텍스트로만 렌더링한다', () => {
  const code =
    '<img src=x onerror="alert(1)"><script>alert(2)</script>\n<a href="javascript:alert(3)">링크</a>';
  const tokens = highlightCode(code, 'html');
  assert.equal(originalText(tokens), code);
  const html = render(CodeTokens, { tokens });
  assert.match(html, /hljs-tag/);
  assert.doesNotMatch(html, /<(?:img|script|a)(?:\s|>)/);
  assert.doesNotMatch(html, /<span[^>]*(?:onerror|href)=/);
});

it('긴 코드·많은 줄·알 수 없는 언어는 잘라내지 않고 평문 전체를 보존한다', () => {
  for (const [code, language] of [
    ['x'.repeat(CODE_HIGHLIGHT_MAX_CHARS + 1), 'js'],
    ['x\n'.repeat(CODE_HIGHLIGHT_MAX_LINES), 'py'],
    ['<script>unknown</script>\n', 'unknown-language'],
  ]) {
    assert.equal(canHighlightCode(code, language), false);
    assert.deepEqual(highlightCode(code, language), [code]);
  }
});

it('서버 렌더는 즉시 평문과 복사·기본 가로 스크롤을 제공하고 닫히지 않은 코드도 보존한다', () => {
  for (const content of [
    '```python\ndef greet():\n    return "<script>안녕</script>"\n```',
    '먼저 실행하세요.\n\n```sh\necho "한글',
  ]) {
    const html = render(MarkdownText, { content });
    assert.match(html, /aria-label="코드 복사"/);
    assert.match(html, /aria-label="코드 줄바꿈" aria-pressed="false"/);
    assert.match(html, /<pre[^>]*tabindex="0"[^>]*data-wrap="false"/);
    assert.doesNotMatch(html, /hljs-|<script>/);
  }
  assert.match(
    render(MarkdownText, { content: '```sh\necho "한글' }),
    /echo &quot;한글/,
  );
});

it('줄바꿈 토글은 해당 코드의 표시 상태만 바꾸고 복사에는 항상 원문을 전달한다', () => {
  const text = 'const line = "  공백  ";\n' + 'x'.repeat(400);
  let wrapped = false;
  const props = {
    text,
    language: 'js',
    codeId: 'code-one',
    onWrapChange: (next) => {
      wrapped = next;
    },
    tokens: highlightCode(text, 'js'),
  };
  for (const expected of [false, true, false]) {
    const tree = CodeBlockView({ ...props, wrapped });
    const nodes = elements(tree);
    const button = nodes.find((node) => node.type === 'button');
    const pre = nodes.find((node) => node.type === 'pre');
    const copy = nodes.find((node) => node.type === CopyButton);
    assert.equal(wrapped, expected);
    assert.equal(button.props['aria-pressed'], expected);
    assert.equal(button.props['aria-controls'], pre.props.id);
    assert.equal(pre.props['data-wrap'], expected);
    assert.equal(copy.props.text, text);
    button.props.onClick();
  }
});

it('짧은 스트리밍 조각은 문법 분석 전에 취소하고 최신 본문만 색칠한다', async (t) => {
  const timers = [];
  const cleared = [];
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    const timer = { callback, delay };
    timers.push(timer);
    return timer;
  });
  t.mock.method(globalThis, 'clearTimeout', (timer) => cleared.push(timer));
  const parsed = [];
  const results = [];
  let loads = 0;
  const load = async () => {
    loads += 1;
    return (text, language) => {
      parsed.push([text, language]);
      return [text];
    };
  };
  const stopOld = scheduleCodeHighlight(
    'const x',
    'JS',
    (tokens) => results.push(tokens),
    load,
  );
  stopOld();
  const stopNew = scheduleCodeHighlight(
    'const x = 1;',
    'JS',
    (tokens) => results.push(tokens),
    load,
  );
  assert.equal(timers[1].delay, CODE_HIGHLIGHT_DELAY_MS);
  assert.deepEqual(cleared, [timers[0]]);
  timers[0].callback();
  timers[1].callback();
  await flush();
  assert.equal(loads, 1);
  assert.deepEqual(parsed, [['const x = 1;', 'javascript']]);
  assert.deepEqual(results, [['const x = 1;']]);
  stopNew();
});

it('늦게 로딩된 문법은 화면 종료 뒤 실행하지 않고 실패 시 평문을 유지한다', async (t) => {
  const timers = [];
  t.mock.method(globalThis, 'setTimeout', (callback) => {
    timers.push(callback);
    return timers.length;
  });
  t.mock.method(globalThis, 'clearTimeout', () => {});
  let resolve;
  const stop = scheduleCodeHighlight(
    'const x = 1;',
    'js',
    () => assert.fail('종료 후 갱신'),
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  timers[0]();
  stop();
  resolve(() => assert.fail('종료 후 분석'));
  await flush();
  scheduleCodeHighlight(
    'const x = 1;',
    'js',
    () => assert.fail('실패 후 갱신'),
    async () => {
      throw new Error('청크 로딩 실패');
    },
  );
  timers[1]();
  await flush();
});

it('알 수 없거나 상한을 넘는 코드는 문법 청크·타이머를 준비하지 않는다', (t) => {
  t.mock.method(globalThis, 'setTimeout', () => assert.fail('평문 타이머'));
  for (const [text, language] of [
    ['abc', 'unknown'],
    ['x'.repeat(CODE_HIGHLIGHT_MAX_CHARS + 1), 'js'],
  ]) {
    const stop = scheduleCodeHighlight(
      text,
      language,
      () => assert.fail('평문 갱신'),
      async () => assert.fail('평문 청크 로딩'),
    );
    stop();
  }
});
