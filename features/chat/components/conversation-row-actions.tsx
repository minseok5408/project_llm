'use client';

import type { MouseEvent } from 'react';
import {
  Archive,
  ArchiveRestore,
  Ellipsis,
  Pencil,
  Pin,
  PinOff,
  Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { Conversation } from '../state/chat-store.ts';

export function ConversationRowActions({
  conversation,
  selected,
  generating,
  onRename,
  onTogglePin,
  onToggleArchive,
  onDelete,
}: {
  conversation: Conversation;
  selected: boolean;
  generating: boolean;
  onRename: (conversation: Conversation) => void;
  onTogglePin: (conversation: Conversation) => void;
  onToggleArchive: (conversation: Conversation) => void;
  onDelete: (conversation: Conversation) => void;
}) {
  const act = (
    event: MouseEvent<HTMLButtonElement>,
    action: (conversation: Conversation) => void,
  ) => {
    const menu = event.currentTarget.closest('details');
    // 메뉴를 닫기 전에 시작점을 남겨 다음 확인창에서 초점을 돌려줄 수 있게 한다.
    menu?.querySelector('summary')?.focus();
    if (menu) menu.open = false;
    action(conversation);
  };

  return (
    <details className="group/actions open:pb-1" data-chat-menu>
      <summary
        className={cn(
          'absolute right-1 top-0 flex size-11 cursor-pointer list-none items-center justify-center rounded-lg text-muted-foreground outline-none hover:bg-background/70 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/30 md:top-1 md:size-9 [&::-webkit-details-marker]:hidden',
          !selected &&
            'md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 md:group-open/actions:opacity-100',
        )}
        aria-label={`${conversation.title} 대화 관리`}
        title="대화 관리"
      >
        <Ellipsis className="size-[18px]" aria-hidden="true" />
      </summary>
      <div className="mx-1 mb-1 space-y-0.5 rounded-xl border border-border/70 bg-background p-1.5 shadow-sm">
        <Button
          variant="ghost"
          className="h-11 w-full justify-start gap-2.5 rounded-lg px-2.5 text-xs font-normal md:h-9"
          onClick={(event) => act(event, onRename)}
        >
          <Pencil className="size-3.5" aria-hidden="true" />
          제목 변경
        </Button>
        <Button
          variant="ghost"
          className="h-11 w-full justify-start gap-2.5 rounded-lg px-2.5 text-xs font-normal md:h-9"
          onClick={(event) => act(event, onTogglePin)}
        >
          {conversation.is_pinned ? (
            <PinOff className="size-3.5" aria-hidden="true" />
          ) : (
            <Pin className="size-3.5" aria-hidden="true" />
          )}
          {conversation.is_pinned ? '고정 해제' : '고정'}
        </Button>
        <Button
          variant="ghost"
          disabled={generating}
          title={
            generating
              ? '답변이 끝나면 보관하거나 복원할 수 있습니다.'
              : undefined
          }
          className="h-11 w-full justify-start gap-2.5 rounded-lg px-2.5 text-xs font-normal md:h-9"
          onClick={(event) => act(event, onToggleArchive)}
        >
          {conversation.status === 'archived' ? (
            <ArchiveRestore className="size-3.5" aria-hidden="true" />
          ) : (
            <Archive className="size-3.5" aria-hidden="true" />
          )}
          {conversation.status === 'archived' ? '복원' : '보관'}
        </Button>
        <Button
          variant="ghost"
          disabled={generating}
          title={generating ? '답변이 끝나면 삭제할 수 있습니다.' : undefined}
          className="h-11 w-full justify-start gap-2.5 rounded-lg px-2.5 text-xs font-normal text-destructive hover:bg-destructive/10 hover:text-destructive md:h-9"
          onClick={(event) => act(event, onDelete)}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          삭제
        </Button>
      </div>
    </details>
  );
}
