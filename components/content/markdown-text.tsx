'use client';

import { Children, isValidElement, memo, useId, useMemo } from 'react';
import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import Markdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CopyButton } from './copy-button.tsx';
import { safeMarkdownUrl } from './markdown-policy.ts';

function MarkdownLink({
  href,
  children,
  title,
  id,
  'aria-label': label,
  'aria-describedby': describedBy,
}: ComponentPropsWithoutRef<'a'>) {
  const safe = href ? safeMarkdownUrl(href) : undefined;
  if (!safe) return <span>{children}</span>;
  return (
    <a
      href={safe}
      title={title}
      id={id}
      aria-label={label}
      aria-describedby={describedBy}
      target={safe.startsWith('#') ? undefined : '_blank'}
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
    >
      {children}
    </a>
  );
}

function CodeBlock({ children }: ComponentPropsWithoutRef<'pre'>) {
  const code = Children.toArray(children).find((child) =>
    isValidElement<{ children?: ReactNode; className?: string }>(child),
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
  return (
    <div className="markdown-code-block">
      <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-1">
        <span className="min-w-0 truncate text-xs text-muted-foreground">
          {language || '코드'}
        </span>
        <CopyButton text={text} label="코드 복사" />
      </div>
      {/* 긴 코드의 가로 스크롤을 키보드로도 이동할 수 있게 한다. */}
      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <pre tabIndex={0} aria-label="코드">
        {children}
      </pre>
    </div>
  );
}

const components: Components = {
  a: MarkdownLink,
  pre: CodeBlock,
  img: ({ src, alt }) => (
    <span className="markdown-image-link">
      이미지: {alt || '설명 없음'}
      {typeof src === 'string' && safeMarkdownUrl(src) && (
        <>
          {' '}
          · <MarkdownLink href={src}>이미지 링크 열기</MarkdownLink>
        </>
      )}
    </span>
  ),
  table: ({ children }) => (
    <section
      className="markdown-table-scroll"
      aria-label="표"
      // 표의 가로 스크롤도 키보드로 이동할 수 있게 한다.
      // oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
    >
      <table>{children}</table>
    </section>
  ),
};
const plugins = [remarkGfm];

export const MarkdownText = memo(function MarkdownText({
  content,
}: {
  content: string;
}) {
  const id = useId();
  const rehypeOptions = useMemo(
    () => ({
      footnoteLabel: '각주',
      footnoteBackLabel: '본문으로 돌아가기',
      // 같은 화면의 여러 답변에서 각주 번호가 겹쳐도 자기 본문으로 이동한다.
      clobberPrefix: `message-${id.replace(/\W/g, '')}-`,
    }),
    [id],
  );
  return (
    <div className="chat-markdown">
      <Markdown
        remarkPlugins={plugins}
        remarkRehypeOptions={rehypeOptions}
        components={components}
        skipHtml
        urlTransform={safeMarkdownUrl}
      >
        {content}
      </Markdown>
    </div>
  );
});
