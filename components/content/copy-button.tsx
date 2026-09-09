'use client';

import { useEffect, useRef, useState } from 'react';
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
    <span className="inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={disabled || !text}
        onClick={() => void copy()}
        className="min-h-8 px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
      >
        {label}
      </button>
      <output className="text-[11px] text-muted-foreground">
        {status === 'copied'
          ? '복사됨'
          : status === 'failed'
            ? '복사 실패 · 내용을 직접 선택해 주세요.'
            : ''}
      </output>
    </span>
  );
}
