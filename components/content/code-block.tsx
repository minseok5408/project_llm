'use client';

import { Children, isValidElement, useEffect, useId, useState } from 'react';
import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { WrapText } from 'lucide-react';
import { CopyButton } from './copy-button.tsx';
import { scheduleCodeHighlight } from './code-highlight.ts';
import type { CodeToken } from './code-highlight.ts';
import './code-block.css';

export function CodeTokens({ tokens }: { tokens: CodeToken[] }): ReactNode {
  return tokens.map((token, index) =>
    typeof token === 'string' ? (
      token
    ) : (
      <span key={index} className={token.className}>
        <CodeTokens tokens={token.children} />
      </span>
    ),
  );
}

export function CodeBlockView({
  text,
  language,
  codeId,
  wrapped,
  onWrapChange,
  tokens,
}: {
  text: string;
  language?: string;
  codeId: string;
  wrapped: boolean;
  onWrapChange: (wrapped: boolean) => void;
  tokens?: CodeToken[];
}) {
  return (
    <div className="markdown-code-block">
      <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-1">
        <span className="min-w-0 truncate text-xs text-muted-foreground">
          {language || '코드'}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            aria-label="코드 줄바꿈"
            aria-pressed={wrapped}
            aria-controls={codeId}
            title={wrapped ? '줄바꿈 끄기' : '줄바꿈 켜기'}
            onClick={() => onWrapChange(!wrapped)}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring aria-pressed:bg-background aria-pressed:text-foreground"
          >
            <WrapText className="size-4" strokeWidth={1.7} aria-hidden="true" />
            줄바꿈
          </button>
          <CopyButton text={text} label="코드 복사" />
        </div>
      </div>
      {/* 긴 코드의 가로 스크롤을 키보드로도 이동할 수 있게 한다. */}
      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <pre id={codeId} tabIndex={0} aria-label="코드" data-wrap={wrapped}>
        <code className={language ? `language-${language}` : undefined}>
          {tokens ? <CodeTokens tokens={tokens} /> : text}
        </code>
      </pre>
    </div>
  );
}

export function CodeBlock({ children }: ComponentPropsWithoutRef<'pre'>) {
  const code = Children.toArray(children).find((child) =>
    isValidElement(child),
  );
  const properties = isValidElement<{
    children?: ReactNode;
    className?: string;
  }>(code)
    ? code.props
    : null;
  const text =
    typeof properties?.children === 'string' ? properties.children : '';
  const language = properties?.className?.match(/(?:^|\s)language-(\S+)/)?.[1];
  const codeId = useId();
  const [wrapped, setWrapped] = useState(false);
  const [highlight, setHighlight] = useState<{
    text: string;
    language: string | undefined;
    tokens: CodeToken[];
  } | null>(null);
  useEffect(
    () =>
      scheduleCodeHighlight(text, language, (tokens) => {
        setHighlight({ text, language, tokens });
      }),
    [text, language],
  );
  // 새 조각이 도착하면 색상 분석을 기다리지 않고 그 원문을 즉시 표시한다.
  const tokens =
    highlight?.text === text && highlight.language === language
      ? highlight.tokens
      : undefined;
  return (
    <CodeBlockView
      text={text}
      language={language}
      codeId={codeId}
      wrapped={wrapped}
      onWrapChange={setWrapped}
      tokens={tokens}
    />
  );
}
