'use client';

import { FileText, LoaderCircle, RotateCcw, X } from 'lucide-react';
import type { AttachedFile } from '../types.ts';

const statuses = {
  uploaded: '처리 대기',
  scanning: '파일 검사 중',
  parsing: '텍스트 추출 중',
  indexing: '검색 준비 중',
  ready: '준비 완료',
  failed: '처리 실패',
};

export function FileAttachments({
  files,
  busy,
  error,
  onDelete,
  onRetry,
}: {
  files: AttachedFile[];
  busy: boolean;
  error: string | null;
  onDelete: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  if (!files.length && !busy && !error) return null;
  return (
    <div className="px-3 pt-2 pb-1 text-xs" aria-label="첨부 파일">
      {!!files.length && (
        <p className="mb-2 text-muted-foreground">
          이 대화에서 참고할 파일 · 대화 참여자에게 공유됩니다.
        </p>
      )}
      <ul className="max-h-36 space-y-2 overflow-y-auto">
        {files.map((file) => (
          <li
            key={file.id}
            className="flex items-start gap-2 rounded-xl border border-border/70 p-2.5"
          >
            {['ready', 'failed'].includes(file.status) ? (
              <FileText
                className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
            ) : (
              <LoaderCircle
                className="mt-0.5 size-4 shrink-0 motion-safe:animate-spin"
                aria-hidden="true"
              />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium" title={file.filename}>
                {file.filename}
              </p>
              <output className="mt-1 block text-muted-foreground">
                {statuses[file.status]}
                {file.status === 'ready' && ` · ${file.pages}페이지`}
              </output>
              {file.error && (
                <p className="mt-1 leading-5 text-destructive">
                  {file.error}
                  {file.dead_letter ? ' 재시도 한도에 도달했습니다.' : ''}
                </p>
              )}
            </div>
            {file.can_retry && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onRetry(file.id)}
                aria-label={`${file.filename} 재시도`}
                className="rounded-md p-1.5 hover:bg-accent disabled:opacity-40"
              >
                <RotateCcw className="size-3.5" />
              </button>
            )}
            {file.can_delete && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onDelete(file.id)}
                aria-label={`${file.filename} 삭제`}
                className="rounded-md p-1.5 hover:bg-accent disabled:opacity-40"
              >
                <X className="size-3.5" />
              </button>
            )}
          </li>
        ))}
      </ul>
      {busy && (
        <output className="mt-2 block text-muted-foreground">
          첨부 파일 요청 처리 중…
        </output>
      )}
      {error && (
        <p className="mt-2 text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
