'use client';

import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type RefObject,
} from 'react';
import {
  Archive,
  LoaderCircle,
  MessageCircle,
  Search,
  SquarePen,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import type { AuthSessionStore } from '../../auth/state/auth-session.ts';
import { ConversationSearchStore } from '../state/conversation-search-store.ts';

export function ConversationSearchDialog({
  open,
  onOpenChange,
  workspaceId,
  request,
  returnFocus,
  onOpenConversation,
  onNewChat,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: string;
  request: AuthSessionStore['request'];
  returnFocus: RefObject<HTMLElement | null>;
  onOpenConversation: (id: string) => void;
  onNewChat: () => void;
}) {
  const [store] = useState(
    () => new ConversationSearchStore(request, workspaceId),
  );
  const state = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getServerSnapshot,
  );
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) void store.initialize();
    else store.dispose();
    return () => store.dispose();
  }, [open, store]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        initialFocus={input}
        finalFocus={() =>
          returnFocus.current?.getClientRects().length
            ? returnFocus.current
            : document.getElementById('chat-message-input')
        }
        showCloseButton={false}
        className="flex h-[min(560px,calc(100dvh-2rem))] flex-col gap-0 overflow-hidden rounded-2xl p-0 sm:max-w-[640px]"
      >
        <DialogTitle className="sr-only">채팅 검색</DialogTitle>
        <DialogDescription className="sr-only">
          현재 작업 공간의 대화 제목과 메시지 내용을 검색합니다. 보관한 대화도
          포함합니다.
        </DialogDescription>
        <div className="flex shrink-0 items-center gap-3 border-b border-border/70 px-4 py-3 sm:px-5">
          <Search
            className="size-5 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <input
            ref={input}
            type="search"
            aria-label="채팅 검색어"
            placeholder="채팅 검색"
            maxLength={200}
            value={state.query}
            onChange={(event) => store.setQuery(event.target.value)}
            className="h-11 min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:appearance-none"
          />
          <DialogClose
            render={
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-9 rounded-full"
              />
            }
          >
            <X className="size-4" aria-hidden="true" />
            <span className="sr-only">채팅 검색 닫기</span>
          </DialogClose>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2">
          {!state.query && (
            <Button
              variant="ghost"
              className="mb-2 h-12 w-full justify-start gap-3 rounded-xl px-3 font-normal"
              onClick={() => {
                onOpenChange(false);
                onNewChat();
              }}
            >
              <SquarePen className="size-[18px]" aria-hidden="true" />
              새 채팅
            </Button>
          )}
          <p className="px-3 pb-3 pt-2 text-xs text-muted-foreground">
            {state.query.trim() ? '검색 결과' : '최근 대화'} · 현재 작업 공간
          </p>
          {state.loading ? (
            <output className="flex items-center justify-center gap-2 px-4 py-16 text-sm text-muted-foreground">
              <LoaderCircle
                className="size-4 animate-spin motion-reduce:animate-none"
                aria-hidden="true"
              />
              검색하는 중…
            </output>
          ) : (
            <>
              {!state.items.length && !state.error && (
                <div className="px-5 py-16 text-center">
                  <MessageCircle
                    className="mx-auto mb-4 size-7 text-muted-foreground/60"
                    strokeWidth={1.4}
                    aria-hidden="true"
                  />
                  <p className="text-sm font-medium">
                    {state.query.trim()
                      ? '검색 결과가 없습니다'
                      : '아직 저장된 대화가 없습니다'}
                  </p>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    {state.query.trim()
                      ? '다른 단어로 검색해 보세요. 제목과 메시지 내용에서 찾습니다.'
                      : '새 채팅을 시작하면 이곳에서 다시 찾을 수 있어요.'}
                  </p>
                </div>
              )}
              <ul aria-label="채팅 검색 결과" className="space-y-0.5">
                {state.items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-3 rounded-xl px-3 py-3.5 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                      onClick={() => {
                        onOpenChange(false);
                        onOpenConversation(item.id);
                      }}
                    >
                      <MessageCircle
                        className="size-[18px] shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm">
                          {item.title}
                        </span>
                        {item.status === 'archived' && (
                          <span className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground">
                            <Archive className="size-3" aria-hidden="true" />
                            보관된 대화
                          </span>
                        )}
                      </span>
                      <time
                        className="shrink-0 text-[11px] tabular-nums text-muted-foreground"
                        dateTime={item.last_message_at}
                      >
                        {new Date(item.last_message_at).toLocaleDateString(
                          'ko-KR',
                          { month: 'short', day: 'numeric' },
                        )}
                      </time>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          {state.error && (
            <div role="alert" className="px-3 py-6 text-center text-sm">
              <p className="text-destructive">{state.error}</p>
              <Button
                variant="ghost"
                className="mt-3 rounded-lg"
                onClick={() => void store.search(Boolean(state.items.length))}
              >
                다시 시도
              </Button>
            </div>
          )}
          {state.nextCursor && !state.error && (
            <div className="py-3 text-center">
              <Button
                variant="ghost"
                disabled={state.loadingMore}
                onClick={() => void store.search(true)}
                className="rounded-full text-xs"
              >
                {state.loadingMore ? '불러오는 중…' : '더 보기'}
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
