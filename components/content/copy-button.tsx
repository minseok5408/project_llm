'use client';

import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, Copy } from 'lucide-react';
import { copyText } from './clipboard.ts';

export function CopyButton({
  text,
  label = '답변 복사',
  disabled = false,
}: {
  text: string;
  label?: string;
  disabled?: boolean;
}) {
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const copy = async () => {
    if (timer.current) clearTimeout(timer.current);
    try {
      await copyText(text);
      setStatus('copied');
    } catch {
      setStatus('failed');
    }
    timer.current = setTimeout(() => setStatus('idle'), 3000);
  };
  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={disabled || !text}
        onClick={() => void copy()}
        aria-label={label}
        title={status === 'copied' ? '복사됨' : label}
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-40"
      >
        {status === 'copied' ? (
          <Check className="size-4" strokeWidth={1.7} aria-hidden="true" />
        ) : status === 'failed' ? (
          <CircleAlert
            className="size-4"
            strokeWidth={1.7}
            aria-hidden="true"
          />
        ) : (
          <Copy className="size-4" strokeWidth={1.7} aria-hidden="true" />
        )}
      </button>
      <output
        className={
          status === 'failed' ? 'text-xs text-muted-foreground' : 'sr-only'
        }
      >
        {status === 'copied'
          ? '복사됨'
          : status === 'failed'
            ? '복사 실패 · 내용을 직접 선택해 주세요.'
            : ''}
      </output>
    </span>
  );
}
