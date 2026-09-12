'use client';

import { Fragment, useState, type ReactNode, type RefObject } from 'react';
import {
  Archive,
  ChevronDown,
  LoaderCircle,
  MessageCircle,
  PanelLeft,
  PanelLeftClose,
  Pin,
  Search,
  SquarePen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SheetClose } from '@/components/ui/sheet';
import { useIsMobile } from '@/hooks/use-mobile';
import { cn } from '@/lib/utils';
import type { ChatState, Conversation } from '../state/chat-store.ts';
import { taskLabel } from './generation-activity';
import { ConversationRowActions } from './conversation-row-actions';
import { ConversationSidebarSurface } from './conversation-sidebar-surface';

export function ConversationSidebar({
  state,
  sidebarOpen,
  desktopCollapsed = false,
  isGenerating,
  onClose,
  onMobileClose = onClose,
  mobileReturnFocus,
  onExpand,
  onNewChat,
  onSearch,
  onFilterChange,
  onOpenConversation,
  onRename,
  onTogglePin,
  onToggleArchive,
  onDelete,
  onLoadMore,
  children,
}: {
  state: Pick<
    ChatState,
    | 'filter'
    | 'selected'
    | 'listLoading'
    | 'conversations'
    | 'conversationCursor'
  > &
    Partial<Pick<ChatState, 'tasks' | 'notifications'>>;
  sidebarOpen: boolean;
  desktopCollapsed?: boolean;
  isGenerating: boolean;
  onClose: () => void;
  onMobileClose?: () => void;
  mobileReturnFocus?: RefObject<HTMLElement | null>;
  onExpand: () => void;
  onNewChat: () => void;
  onSearch: () => void;
  onFilterChange: (filter: 'active' | 'archived') => void;
  onOpenConversation: (id: string) => void;
  onRename: (conversation: Conversation) => void;
  onTogglePin: (conversation: Conversation) => void;
  onToggleArchive: (conversation: Conversation) => void;
  onDelete: (conversation: Conversation) => void;
  onLoadMore: () => void;
  children: ReactNode;
}) {
  const isMobile = useIsMobile();
  const [recentOpen, setRecentOpen] = useState(true);
  const listDetailsOpen = state.filter === 'archived' || recentOpen;
  const listed = [...state.conversations];
  if (
    state.selected &&
    state.selected.status === state.filter &&
    !listed.some((item) => item.id === state.selected?.id)
  )
    listed.unshift(state.selected);
  const sorted = listed.sort(
    (left, right) => Number(right.is_pinned) - Number(left.is_pinned),
  );
  const recentHeading = (
    <h2 className="py-1">
      <Button
        variant="ghost"
        onClick={() => setRecentOpen((open) => !open)}
        aria-expanded={recentOpen}
        className="h-8 w-full justify-start gap-2 rounded-lg px-3 text-[11px] font-medium text-muted-foreground/80 hover:bg-transparent aria-expanded:bg-transparent aria-expanded:text-muted-foreground/80 dark:hover:bg-transparent"
      >
        최근 채팅
        <ChevronDown
          className={cn(
            'size-3 transition-transform',
            !recentOpen && '-rotate-90',
          )}
          aria-hidden="true"
        />
      </Button>
    </h2>
  );

  return (
    <ConversationSidebarSurface
      mobile={isMobile}
      open={sidebarOpen}
      collapsed={desktopCollapsed}
      onMobileClose={onMobileClose}
      returnFocus={mobileReturnFocus}
    >
      <div
        className={cn(
          'flex h-[72px] shrink-0 items-center pl-5 pr-3',
          desktopCollapsed && 'md:justify-center md:px-2',
        )}
      >
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-lg font-semibold tracking-[-0.025em]',
            desktopCollapsed && 'md:hidden',
          )}
        >
          Project LLM
        </span>
        {desktopCollapsed && (
          <Button
            variant="ghost"
            size="icon"
            onClick={onExpand}
            className="hidden size-10 rounded-lg p-0 text-muted-foreground hover:bg-sidebar-accent hover:text-foreground md:inline-flex"
            aria-label="사이드바 펼치기"
            aria-expanded={false}
            title="사이드바 펼치기"
          >
            <PanelLeft className="size-5" aria-hidden="true" />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          onClick={onSearch}
          className={cn(
            'size-8 rounded-lg p-0 text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
            desktopCollapsed && 'md:hidden',
          )}
          aria-label="채팅 검색"
          title="채팅 검색"
        >
          <Search className="size-[18px]" aria-hidden="true" />
        </Button>
        {isMobile ? (
          <SheetClose
            render={
              <Button
                variant="ghost"
                size="icon"
                className="size-11 rounded-lg text-muted-foreground hover:bg-sidebar-accent"
              />
            }
            aria-label="대화 목록 닫기"
          >
            <PanelLeftClose className="size-[18px]" aria-hidden="true" />
          </SheetClose>
        ) : (
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            className={cn(
              'size-8 rounded-lg p-0 text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
              desktopCollapsed && 'md:hidden',
            )}
            aria-label="사이드바 접기"
            title="사이드바 접기"
          >
            <PanelLeftClose className="size-[18px]" aria-hidden="true" />
          </Button>
        )}
      </div>
      <div
        className={cn(
          'shrink-0 space-y-0.5 px-3 pb-4',
          desktopCollapsed &&
            'md:flex md:flex-col md:gap-0.5 md:space-y-0 md:px-2 md:pb-0',
        )}
      >
        <Button
          variant="ghost"
          onClick={onNewChat}
          className={cn(
            'h-11 w-full justify-start gap-3 rounded-lg px-3 text-[13px] font-normal hover:bg-sidebar-accent',
            desktopCollapsed &&
              'md:size-10 md:justify-center md:px-0 md:text-muted-foreground md:hover:text-foreground',
          )}
          aria-label="새 채팅"
          title="새 채팅"
        >
          <SquarePen className="size-[18px]" aria-hidden="true" />
          <span className={cn(desktopCollapsed && 'md:sr-only')}>새 채팅</span>
        </Button>
        {desktopCollapsed && (
          <Button
            variant="ghost"
            size="icon"
            onClick={onSearch}
            className="hidden size-10 rounded-lg p-0 text-muted-foreground hover:bg-sidebar-accent hover:text-foreground md:inline-flex"
            aria-label="채팅 검색"
            title="채팅 검색"
          >
            <Search className="size-[18px]" aria-hidden="true" />
          </Button>
        )}
        <div
          className={cn(
            'grid grid-cols-2 gap-1 pb-1 pt-4',
            desktopCollapsed && 'md:hidden',
          )}
        >
          {(['active', 'archived'] as const).map((filter) => (
            <Button
              key={filter}
              variant="ghost"
              onClick={() => onFilterChange(filter)}
              aria-pressed={state.filter === filter}
              className={cn(
                'h-9 gap-1.5 rounded-lg text-xs font-normal text-muted-foreground',
                state.filter === filter &&
                  'bg-sidebar-accent text-foreground hover:bg-sidebar-accent',
              )}
            >
              {filter === 'active' ? (
                <MessageCircle className="size-3.5" aria-hidden="true" />
              ) : (
                <Archive className="size-3.5" aria-hidden="true" />
              )}
              {filter === 'active' ? '대화' : '보관함'}
            </Button>
          ))}
        </div>
      </div>
      <nav
        className={cn(
          'min-h-0 flex-1 overscroll-contain overflow-y-auto px-3 pb-4',
          desktopCollapsed && 'md:hidden',
        )}
        aria-label="대화 목록"
      >
        {state.filter === 'archived' || sorted[0]?.is_pinned ? (
          <h2 className="px-3 pb-2 pt-2 text-[11px] font-medium text-muted-foreground/80">
            {state.filter === 'archived' ? '보관된 채팅' : '고정된 채팅'}
          </h2>
        ) : (
          recentHeading
        )}
        {sorted.map((conversation, index) => (
          <Fragment key={conversation.id}>
            {state.filter === 'active' &&
              index > 0 &&
              sorted[index - 1].is_pinned &&
              !conversation.is_pinned && (
                <div className="pt-3">{recentHeading}</div>
              )}
            <div
              hidden={
                state.filter === 'active' &&
                !conversation.is_pinned &&
                !recentOpen
              }
              className={cn(
                'group relative mb-0.5 rounded-lg transition-colors hover:bg-sidebar-accent/70',
                state.selected?.id === conversation.id &&
                  'bg-sidebar-accent text-foreground',
              )}
            >
              <a
                href={`/chat/${conversation.id}`}
                onClick={(event) => {
                  if (
                    event.button !== 0 ||
                    event.metaKey ||
                    event.ctrlKey ||
                    event.shiftKey ||
                    event.altKey
                  )
                    return;
                  event.preventDefault();
                  onOpenConversation(conversation.id);
                }}
                className={cn(
                  'flex min-h-11 items-center gap-2 rounded-lg py-3 pl-3 pr-12 text-[13px] leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring/30',
                  state.selected?.id === conversation.id && 'font-medium',
                )}
                title={`${conversation.title} · ${new Date(conversation.last_message_at).toLocaleDateString('ko-KR')}`}
                aria-current={
                  state.selected?.id === conversation.id ? 'page' : undefined
                }
              >
                {conversation.is_pinned && (
                  <Pin
                    className="size-3 shrink-0 text-muted-foreground"
                    aria-label="고정됨"
                  />
                )}
                <span className="min-w-0 flex-1 truncate">
                  {conversation.title}
                </span>
                {conversation.active_generation_id && (
                  <LoaderCircle
                    className="size-3.5 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
                    aria-label={
                      state.tasks?.find(
                        (task) => task.conversationId === conversation.id,
                      )
                        ? taskLabel(
                            state.tasks.find(
                              (task) => task.conversationId === conversation.id,
                            )!,
                          )
                        : '응답 생성 중'
                    }
                  />
                )}
                {state.notifications?.some(
                  (item) => item.conversationId === conversation.id,
                ) && (
                  <span
                    className="size-1.5 shrink-0 rounded-full bg-primary"
                    aria-label="새 작업 알림"
                  />
                )}
              </a>
              <ConversationRowActions
                conversation={conversation}
                selected={state.selected?.id === conversation.id}
                generating={Boolean(
                  conversation.active_generation_id ||
                  state.tasks?.some(
                    (task) => task.conversationId === conversation.id,
                  ) ||
                  (state.selected?.id === conversation.id && isGenerating),
                )}
                onRename={onRename}
                onTogglePin={onTogglePin}
                onToggleArchive={onToggleArchive}
                onDelete={onDelete}
              />
            </div>
          </Fragment>
        ))}
        {listDetailsOpen && !state.listLoading && !sorted.length && (
          <div className="px-3 py-7 text-center" aria-live="polite">
            <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-2xl bg-sidebar-accent/65 text-muted-foreground">
              {state.filter === 'archived' ? (
                <Archive className="size-[18px]" aria-hidden="true" />
              ) : (
                <MessageCircle className="size-[18px]" aria-hidden="true" />
              )}
            </div>
            <p className="text-xs font-medium text-foreground/80">
              {state.filter === 'archived'
                ? '보관된 대화가 없어요'
                : '여기서 대화를 이어가세요'}
            </p>
            <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
              {state.filter === 'archived'
                ? '보관한 대화는 이곳에 모아둘게요.'
                : '새 채팅을 시작하면 이곳에 저장돼요.'}
            </p>
          </div>
        )}
        {listDetailsOpen && state.listLoading && (
          <p
            className="flex items-center gap-2 px-3 py-4 text-xs text-muted-foreground"
            aria-live="polite"
          >
            <LoaderCircle
              className="size-3.5 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
            대화 목록 불러오는 중...
          </p>
        )}
        {listDetailsOpen && state.conversationCursor && (
          <Button
            variant="ghost"
            disabled={state.listLoading}
            onClick={onLoadMore}
            className="mt-2 h-10 w-full rounded-xl text-xs font-normal text-muted-foreground hover:bg-sidebar-accent"
          >
            대화 더 보기
          </Button>
        )}
      </nav>
      <div
        className={cn('hidden', desktopCollapsed && 'md:block md:flex-1')}
        aria-hidden="true"
      />
      {children}
    </ConversationSidebarSurface>
  );
}
