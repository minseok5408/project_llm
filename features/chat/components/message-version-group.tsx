'use client';

import { Fragment, useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { ChatMessage } from '../state/conversation-types.ts';
import {
  resolveVersionSelection,
  type MessageReveal,
  type MessageVersionGroup as VersionGroup,
  type VersionSelection,
} from '../state/message-versions.ts';

export function MessageVersionGroup({
  group,
  activeMessageId,
  reveal,
  registerMessage,
  onVersionChange,
  children,
}: {
  group: VersionGroup;
  activeMessageId?: string | null;
  reveal?: MessageReveal | null;
  registerMessage: (id: string, element: HTMLElement | null) => void;
  onVersionChange?: () => void;
  children: (message: ChatMessage) => ReactNode;
}) {
  const [selection, setSelection] = useState<VersionSelection>({
    messageId: null,
    activeMessageId: null,
    revealKey: null,
  });
  const resolved = resolveVersionSelection(
    group,
    selection,
    activeMessageId,
    reveal,
  );
  // 선택을 바꾸는 요청이 달라진 렌더에서만 동기화해 이전 버전이 잠깐 보이지 않게 한다.
  if (resolved !== selection) setSelection(resolved);
  const register = useCallback(
    (element: HTMLElement | null) => {
      // 접힌 버전의 검색 결과와 이전 페이지 앵커도 같은 표시 위치를 찾을 수 있다.
      for (const message of group.messages)
        registerMessage(message.id, element);
    },
    [group.messages, registerMessage],
  );
  const index = group.messages.findIndex(
    (message) => message.id === resolved.messageId,
  );
  const selected = group.messages[index];
  if (!selected) return null;
  const choose = (offset: number) => {
    const message = group.messages[index + offset];
    if (!message) return;
    onVersionChange?.();
    setSelection({ ...resolved, messageId: message.id });
  };
  return (
    <article
      ref={register}
      data-message-id={selected.id}
      className="px-5 py-5 first:pt-8 last:pb-8 sm:px-6 sm:py-7"
    >
      {group.messages.length > 1 && (
        <nav
          aria-label="답변 버전"
          className="mb-2 flex w-fit items-center gap-1 text-xs text-muted-foreground"
        >
          <button
            type="button"
            aria-label="이전 답변 버전"
            disabled={index === 0}
            onClick={() => choose(-1)}
            className="inline-flex size-11 items-center justify-center rounded-lg hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-30"
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
          </button>
          <output
            aria-live="polite"
            aria-atomic="true"
            className="tabular-nums"
          >
            <span className="sr-only">답변 버전 </span>
            {index + 1} / {group.messages.length}
          </output>
          <button
            type="button"
            aria-label="다음 답변 버전"
            disabled={index === group.messages.length - 1}
            onClick={() => choose(1)}
            className="inline-flex size-11 items-center justify-center rounded-lg hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-30"
          >
            <ChevronRight className="size-4" aria-hidden="true" />
          </button>
          {selected.is_current === false && <span>이전 답변</span>}
        </nav>
      )}
      <Fragment key={selected.id}>{children(selected)}</Fragment>
    </article>
  );
}
