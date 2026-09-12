import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import ts from 'typescript';

// 실제 브라우저나 개발 서버 없이 React 컴포넌트를 렌더링하는 테스트 전용 로더다.
export async function resolve(specifier, context, nextResolve) {
  const path = specifier.startsWith('@/')
    ? new URL(`../../${specifier.slice(2)}`, import.meta.url)
    : specifier.startsWith('.') && context.parentURL
      ? new URL(specifier, context.parentURL)
      : null;
  if (path) {
    for (const extension of ['', '.ts', '.tsx']) {
      const candidate = new URL(`${path.href}${extension}`);
      if (existsSync(candidate))
        return { url: candidate.href, shortCircuit: true };
    }
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('.css'))
    return { source: '', format: 'module', shortCircuit: true };
  if (!url.endsWith('.tsx')) return nextLoad(url, context);
  const source = await readFile(new URL(url), 'utf8');
  const result = ts.transpileModule(source, {
    fileName: new URL(url).pathname,
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  return { source: result.outputText, format: 'module', shortCircuit: true };
}
