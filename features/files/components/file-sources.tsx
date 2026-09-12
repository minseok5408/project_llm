'use client';

import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Download } from 'lucide-react';
import { responseError } from '../../../lib/http.ts';
import type { RequestFn } from '../../../lib/http.ts';
import { validFileSources } from '../types.ts';
import type { FileSource } from '../types.ts';

function Source({
  source,
  conversationId,
  request,
}: {
  source: FileSource;
  conversationId: string;
  request: RequestFn;
}) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const base = `/api/v1/conversations/${conversationId}/files/${source.document_id}`;
  const load = async () => {
    if (loading) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setText(null);
    setError(null);
    try {
      const response = await request(`${base}/chunks/${source.chunk_id}`, {
        signal: current.signal,
      });
      if (!response.ok)
        throw new Error(
          await responseError(
            response,
            '파일이 삭제되었거나 접근할 수 없습니다.',
          ),
        );
      const data = await response.json();
      if (!current.signal.aborted) setText(data.content);
    } catch (error) {
      if (!current.signal.aborted)
        setError(
          error instanceof Error
            ? error.message
            : '파일 근거를 불러오지 못했습니다.',
        );
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  };
  if (source.available === false)
    return (
      <p className="rounded-xl border border-border/60 px-3 py-2">
        [파일 {source.number}] {source.filename} · 삭제된 파일
      </p>
    );
  return (
    <details
      className="rounded-xl border border-border/60 px-3 py-2"
      onToggle={(event) => {
        if (event.currentTarget.open) void load();
        else {
          controller.current?.abort();
          setText(null);
          setLoading(false);
        }
      }}
    >
      <summary className="cursor-pointer break-words leading-5 focus-visible:outline-2 focus-visible:outline-ring">
        [파일 {source.number}] {source.filename} · {source.page}페이지 · 조각{' '}
        {source.chunk}
      </summary>
      <div className="mt-2 border-t border-border/60 pt-2">
        {loading && <output>원문 근거 확인 중…</output>}
        {error && <p role="alert">{error}</p>}
        {text !== null && (
          <>
            <p className="mb-2 text-muted-foreground">
              페이지 내 {source.start + 1}–{source.end}번째 문자
            </p>
            <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap break-words font-sans leading-6">
              {text}
            </pre>
            <a
              href={`${base}/download`}
              download
              className="mt-3 inline-flex items-center gap-1.5 underline underline-offset-4"
            >
              <Download className="size-3.5" />
              원본 다운로드
            </a>
          </>
        )}
      </div>
    </details>
  );
}

export function FileSources({
  sources,
  conversationId,
  request = fetch,
}: {
  sources?: FileSource[];
  conversationId: string;
  request?: RequestFn;
}) {
  if (
    !validFileSources(sources) ||
    !sources.length ||
    !/^[0-9a-f-]{36}$/i.test(conversationId)
  )
    return null;
  return (
    <details className="group/files mt-4 text-xs text-muted-foreground">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 [&::-webkit-details-marker]:hidden">
        <ChevronDown className="size-3.5 -rotate-90 group-open/files:rotate-0" />
        참고한 파일 근거 {sources.length}개
      </summary>
      <div className="mt-2 space-y-2">
        {sources.map((source) => (
          <Source
            key={`${source.chunk_id}:${source.available !== false}`}
            source={source}
            conversationId={conversationId}
            request={request}
          />
        ))}
      </div>
    </details>
  );
}
