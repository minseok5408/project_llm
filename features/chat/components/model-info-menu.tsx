'use client';

import { Cpu } from 'lucide-react';
import { cn } from '@/lib/utils';

export function ModelInfoMenu({
  runtime,
}: {
  runtime: {
    ready: boolean;
    online: boolean;
    backend: string;
    model: string;
    detail: string;
  };
}) {
  return (
    <details className="chat-menu relative" data-chat-menu>
      <summary
        className="flex min-h-9 items-center gap-1.5 rounded-full px-2.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        aria-label="모델 정보"
        title="모델 정보"
      >
        <Cpu className="size-4" strokeWidth={1.6} aria-hidden="true" />
        <span>모델 정보</span>
      </summary>
      <div className="chat-popover absolute bottom-11 right-0 z-20 max-h-[calc(100dvh-7rem)] w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-2xl border border-border/80 bg-popover p-5 text-sm text-popover-foreground">
        <h2 className="mb-3 text-xs font-medium text-muted-foreground">
          현재 모델
        </h2>
        <p className="break-words font-medium leading-6">{runtime.model}</p>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          {runtime.detail}
        </p>
        <div className="mt-4 flex items-center gap-2 border-t border-border/70 pt-3 text-xs">
          <span
            className={cn(
              'size-1.5 rounded-full',
              runtime.ready ? 'bg-emerald-600' : 'bg-amber-600',
            )}
            aria-hidden="true"
          />
          <span>{runtime.backend}</span>
          <span className="ml-auto text-muted-foreground">
            API {runtime.online ? '연결됨' : '확인 중'}
          </span>
        </div>
      </div>
    </details>
  );
}
