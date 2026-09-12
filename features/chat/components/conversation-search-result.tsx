'use client';

import { Archive, MessageCircle } from 'lucide-react';
import type { Conversation } from '../state/chat-store.ts';

export function HighlightedText({
  text,
  query,
}: {
  text: string;
  query: string;
}) {
  const term = query.trim();
  if (!term) return text;
  // 검색어를 정규식 문법이나 HTML로 해석하지 않고 원문 텍스트만 강조한다.
  const pattern = new RegExp(
    term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
    'giu',
  );
  const parts = [];
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    parts.push(text.slice(offset, match.index));
    parts.push(
      <mark
        key={match.index}
        className="rounded bg-amber-200/60 text-inherit dark:bg-amber-500/30"
      >
        {match[0]}
      </mark>,
    );
    offset = match.index + match[0].length;
  }
  parts.push(text.slice(offset));
  return <>{parts}</>;
}

export function ConversationSearchResult({
  conversation,
  query,
  onOpen,
}: {
  conversation: Conversation;
  query: string;
  onOpen: (id: string, messageId?: string) => void;
}) {
  const match = conversation.search_match;
  return (
    <button
      type="button"
      className="flex w-full items-start gap-3 rounded-xl px-3 py-3.5 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
      onClick={() => onOpen(conversation.id, match?.message_id)}
    >
      <MessageCircle
        className="mt-0.5 size-[18px] shrink-0 text-muted-foreground"
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">
          <HighlightedText text={conversation.title} query={query} />
        </span>
        {match && (
          <span className="mt-1 block line-clamp-2 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
            <span className="sr-only">일치한 메시지: </span>
            <HighlightedText text={match.snippet} query={query} />
          </span>
        )}
        {conversation.status === 'archived' && (
          <span className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground">
            <Archive className="size-3" aria-hidden="true" />
            보관된 대화
          </span>
        )}
      </span>
      <time
        className="shrink-0 text-[11px] tabular-nums text-muted-foreground"
        dateTime={conversation.last_message_at}
      >
        {new Date(conversation.last_message_at).toLocaleDateString('ko-KR', {
          month: 'short',
          day: 'numeric',
        })}
      </time>
    </button>
  );
}
