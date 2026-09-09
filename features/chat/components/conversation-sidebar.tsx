'use client';

import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ChatState, Conversation } from '../state/chat-store.ts';

export function ConversationSidebar({
  state,
  sidebarOpen,
  isGenerating,
  onClose,
  onNewChat,
  onWorkspaceChange,
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
    | 'workspaceId'
    | 'workspaces'
    | 'filter'
    | 'selected'
    | 'listLoading'
    | 'conversations'
    | 'conversationCursor'
  >;
  sidebarOpen: boolean;
  isGenerating: boolean;
  onClose: () => void;
  onNewChat: () => void;
  onWorkspaceChange: (id: string) => void;
  onFilterChange: (filter: 'active' | 'archived') => void;
  onOpenConversation: (id: string) => void;
  onRename: (conversation: Conversation) => void;
  onTogglePin: (conversation: Conversation) => void;
  onToggleArchive: (conversation: Conversation) => void;
  onDelete: () => void;
  onLoadMore: () => void;
  children: ReactNode;
}) {
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

  return (
    <aside
      className={cn(
        'absolute inset-y-0 left-0 z-30 w-72 shrink-0 flex-col border-r border-border bg-card md:static md:w-64',
        sidebarOpen ? 'flex' : 'hidden md:flex',
      )}
      aria-label="저장된 대화"
    >
      <div className="flex h-14 shrink-0 items-center border-b border-border px-4">
        <span className="flex-1 text-sm">
          <span className="text-primary">$</span> Project LLM
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClose}
          className="rounded-none md:hidden"
          aria-label="목록 닫기"
        >
          닫기
        </Button>
      </div>
      <div className="space-y-3 border-b border-border p-3">
        <Button onClick={onNewChat} className="h-10 w-full rounded-none">
          + 새 채팅
        </Button>
        <label
          className="block text-xs text-muted-foreground"
          htmlFor="workspace"
        >
          작업 공간
        </label>
        <select
          id="workspace"
          value={state.workspaceId}
          onChange={(event) => onWorkspaceChange(event.target.value)}
          className="h-9 w-full border border-border bg-background px-2 text-xs"
        >
          {!state.workspaces.length && (
            <option value="">작업 공간 확인 중</option>
          )}
          {state.workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
        <div className="grid grid-cols-2 gap-1">
          {(['active', 'archived'] as const).map((filter) => (
            <Button
              key={filter}
              variant={state.filter === filter ? 'secondary' : 'ghost'}
              onClick={() => onFilterChange(filter)}
              aria-pressed={state.filter === filter}
              className="h-8 rounded-none text-xs"
            >
              {filter === 'active' ? '대화' : '보관함'}
            </Button>
          ))}
        </div>
      </div>
      <nav
        className="min-h-0 flex-1 overflow-y-auto p-2"
        aria-label="대화 목록"
      >
        {sorted.map((conversation) => (
          <div
            key={conversation.id}
            className={cn(
              'mb-1 border border-transparent',
              state.selected?.id === conversation.id &&
                'border-border bg-primary/5',
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
              className="block px-3 py-3 text-sm hover:bg-muted"
              aria-current={
                state.selected?.id === conversation.id ? 'page' : undefined
              }
            >
              <span className="block truncate">
                {conversation.is_pinned && (
                  <span className="mr-1 text-primary" aria-label="고정됨">
                    ◆
                  </span>
                )}
                {conversation.title}
              </span>
              <span className="mt-1 block text-[10px] text-muted-foreground">
                {conversation.active_generation_id
                  ? '응답 생성 중'
                  : new Date(conversation.last_message_at).toLocaleDateString(
                      'ko-KR',
                    )}
              </span>
            </a>
            {state.selected?.id === conversation.id && (
              <div className="flex flex-wrap gap-1 border-t border-border/60 px-2 py-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 rounded-none px-2 text-[11px]"
                  onClick={() => onRename(conversation)}
                >
                  제목
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 rounded-none px-2 text-[11px]"
                  onClick={() => onTogglePin(conversation)}
                >
                  {conversation.is_pinned ? '고정 해제' : '고정'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={isGenerating}
                  className="h-7 rounded-none px-2 text-[11px]"
                  onClick={() => onToggleArchive(conversation)}
                >
                  {conversation.status === 'archived' ? '복원' : '보관'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={isGenerating}
                  className="h-7 rounded-none px-2 text-[11px] text-rose-300"
                  onClick={onDelete}
                >
                  삭제
                </Button>
              </div>
            )}
          </div>
        ))}
        {!state.listLoading && !state.conversations.length && (
          <p className="px-3 py-6 text-xs leading-6 text-muted-foreground">
            {state.filter === 'archived'
              ? '보관된 대화가 없습니다.'
              : '첫 대화를 시작해 보세요.'}
          </p>
        )}
        {state.listLoading && (
          <p className="p-3 text-xs text-muted-foreground" aria-live="polite">
            대화 목록 불러오는 중...
          </p>
        )}
        {state.conversationCursor && (
          <Button
            variant="ghost"
            disabled={state.listLoading}
            onClick={onLoadMore}
            className="mt-1 w-full rounded-none text-xs"
          >
            대화 더 보기
          </Button>
        )}
      </nav>
      {children}
    </aside>
  );
}
