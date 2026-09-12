'use client';

import { useRef, useState } from 'react';
import {
  ArrowDown,
  BookOpen,
  CodeXml,
  Compass,
  PanelLeft,
  PencilLine,
  SquarePen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { AuthGate } from '../../auth/components/auth-gate';
import type { AuthenticatedProps } from '../../auth/components/auth-gate';
import { ChatMessageView } from './chat-message-view';
import { MessageVersionGroup } from './message-version-group.tsx';
import { groupMessageVersions } from '../state/message-versions.ts';
import {
  ConversationManagementDialog,
  type ConversationManagementTarget,
} from './conversation-management-dialog.tsx';
import { ChatComposer } from './chat-composer';
import { taskLabel } from './generation-activity';
import { ConversationSidebar } from './conversation-sidebar';
import { ModelInfoMenu } from './model-info-menu';
import { ConversationSearchDialog } from './conversation-search-dialog';
import { TokenUsagePanel } from '../../usage/components/token-usage-panel';
import { NetworkModeSwitch } from '../../network/components/network-mode-switch.tsx';
import { MemorySettings } from '../../memory/components/memory-settings.tsx';
import { ThemeSetting } from '../../preferences/components/theme-setting.tsx';

import { useChatScroll } from '../hooks/use-chat-scroll.ts';
import { useChatSession } from '../hooks/use-chat-session.ts';
import { useModelStatus } from '../hooks/use-model-status.ts';
import { useReducedMotion } from '../hooks/use-reduced-motion.ts';
import { useWebMcp } from '../hooks/use-web-mcp.ts';
import { useWorkbenchMenus } from '../hooks/use-workbench-menus.ts';

const suggestions = [
  {
    label: '글 다듬기',
    prompt:
      '내가 쓴 글을 자연스럽고 명확하게 다듬어줘. 글을 보내면 핵심 의미를 유지하면서 고쳐줘.',
    icon: PencilLine,
    color: 'text-amber-600 dark:text-amber-400',
  },
  {
    label: '공부 계획 세우기',
    prompt: '하루 30분씩 꾸준히 공부할 수 있는 일주일 계획을 짜줘',
    icon: BookOpen,
    color: 'text-emerald-600 dark:text-emerald-400',
  },
  {
    label: '코드 이해하기',
    prompt: 'Python 비동기 코드를 쉽게 설명해줘',
    icon: CodeXml,
    color: 'text-blue-600 dark:text-blue-400',
  },
  {
    label: '아이디어 찾기',
    prompt:
      '이번 주말에 해볼 만한 작은 프로젝트 아이디어를 함께 생각해줘. 먼저 내 관심사를 물어봐줘.',
    icon: Compass,
    color: 'text-violet-600 dark:text-violet-400',
  },
];
export function ChatApplication() {
  return (
    <AuthGate>
      {(auth) => (
        <Workbench
          key={`${auth.session.user.id}:${auth.session.expires_at}`}
          {...auth}
        />
      )}
    </AuthGate>
  );
}

function Workbench({
  session,
  request,
  logout,
  logoutPending,
  authError,
}: AuthenticatedProps) {
  const userName =
    session.user.display_name.trim() ||
    session.user.email.split('@')[0] ||
    '사용자';
  const {
    sidebarOpen,
    setSidebarOpen,
    closeSidebar,
    searchOpen,
    setSearchOpen,
    searchReturnFocus,
    openSearch,
  } = useWorkbenchMenus();
  const { store, state, networkStore, networkState } = useChatSession(
    request,
    closeSidebar,
  );
  const [desktopCollapsed, setDesktopCollapsed] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [managementTarget, setManagementTarget] =
    useState<ConversationManagementTarget | null>(null);
  const mobileSidebarTrigger = useRef<HTMLButtonElement | null>(null);
  const searchNavigation = useRef(0);
  const [findingMessage, setFindingMessage] = useState(false);
  const [loadingNewer, setLoadingNewer] = useState(false);
  const [reveal, setReveal] = useState<{
    conversationId: string;
    messageId: string;
    key: number;
  } | null>(null);
  const reducedMotion = useReducedMotion();
  const runtime = useModelStatus(request);
  const {
    scrollViewport,
    scrollContent,
    viewportHandlers,
    registerMessage,
    showLatest,
    loadingOlder,
    followLatest,
    pauseFollowing,
    loadOlder,
  } = useChatScroll({
    conversationId: state.selected?.id ?? null,
    messages: state.messages,
    loadOlderMessages: store.olderMessages,
    reveal,
  });
  const generation = state.generation;
  const isGenerating = Boolean(generation);
  const otherTask = state.tasks.find(
    (task) => task.conversationId !== state.selected?.id,
  );
  const cancelling = state.cancelling || generation?.cancel_requested;
  const compacting = generation?.stage === 'compacting';
  const searching = generation?.stage === 'searching';
  const noBalance =
    state.usage &&
    !state.usage.unlimited &&
    (state.usage.remaining_tokens ?? 0) <= 0;
  const canSend =
    !state.loading &&
    !findingMessage &&
    !state.sending &&
    !state.fileBusy &&
    !state.filesLoading &&
    (!state.filesEnabled ||
      state.files.every((file) => file.status === 'ready')) &&
    !isGenerating &&
    !otherTask &&
    !logoutPending &&
    !noBalance &&
    Boolean(state.workspaceId) &&
    state.selected?.status !== 'archived';
  const welcome = !state.loading && state.messages.length === 0;
  const networkBusy =
    networkState.loading || networkState.saving || networkState.checking;
  const networkOnline =
    !networkState.error &&
    !networkState.value?.local_only &&
    networkState.value?.mode === 'online';
  const searchLabel = networkBusy
    ? '연결 확인 중'
    : !networkOnline
      ? '로컬 모드'
      : '데이터 사용 ON';

  const send = async (text = state.draft) => {
    if (!canSend || !text.trim()) return false;
    setReveal(null);
    followLatest();
    return store.send(text, { thinking });
  };
  useWebMcp(send);
  const clearNavigation = () => {
    ++searchNavigation.current;
    setFindingMessage(false);
    setReveal(null);
    setManagementTarget(null);
    setSidebarOpen(false);
  };
  const open = (id: string) => {
    clearNavigation();
    void store.openConversation(id);
  };
  const newChat = () => {
    clearNavigation();
    store.newDraft();
  };
  const openSearchResult = async (id: string, messageId?: string) => {
    clearNavigation();
    const key = searchNavigation.current;
    if (!messageId) return store.openConversation(id);
    setFindingMessage(true);
    try {
      await store.openConversation(id);
      if (
        key !== searchNavigation.current ||
        store.getSnapshot().selected?.id !== id
      )
        return;
      const found = await store.revealMessage(messageId);
      if (
        found &&
        key === searchNavigation.current &&
        store.getSnapshot().selected?.id === id
      )
        setReveal({ conversationId: id, messageId, key });
    } finally {
      if (key === searchNavigation.current) setFindingMessage(false);
    }
  };
  const showLatestMessages = async () => {
    setReveal(null);
    const id = store.getSnapshot().selected?.id;
    if (
      store.getSnapshot().newerMessageCursor !== null &&
      !(await store.latestMessages())
    )
      return;
    if (id === store.getSnapshot().selected?.id) followLatest();
  };
  const showNewerMessages = async () => {
    if (loadingNewer) return;
    pauseFollowing();
    setLoadingNewer(true);
    try {
      await store.newerMessages();
    } finally {
      setLoadingNewer(false);
    }
  };

  return (
    <main className="chat-shell relative isolate flex h-[100dvh] w-full overflow-hidden bg-background">
      <ConversationSidebar
        state={state}
        sidebarOpen={sidebarOpen}
        desktopCollapsed={desktopCollapsed}
        isGenerating={isGenerating}
        onClose={() => {
          setSidebarOpen(false);
          setDesktopCollapsed(true);
        }}
        onMobileClose={() => setSidebarOpen(false)}
        mobileReturnFocus={mobileSidebarTrigger}
        onNewChat={newChat}
        onSearch={() => {
          setSidebarOpen(false);
          openSearch();
        }}
        onExpand={() => setDesktopCollapsed(false)}
        onFilterChange={(filter) => void store.setFilter(filter)}
        onOpenConversation={open}
        onRename={(conversation) => {
          store.clearError();
          setManagementTarget({ conversation, kind: 'rename' });
          setSidebarOpen(false);
        }}
        onTogglePin={(conversation) =>
          void store.updateConversation(
            { is_pinned: !conversation.is_pinned },
            conversation.id,
          )
        }
        onToggleArchive={(conversation) =>
          void store.updateConversation(
            {
              status:
                conversation.status === 'archived' ? 'active' : 'archived',
            },
            conversation.id,
          )
        }
        onDelete={(conversation) => {
          store.clearError();
          setManagementTarget({ conversation, kind: 'delete' });
          setSidebarOpen(false);
        }}
        onLoadMore={() => void store.loadConversations(true)}
      >
        <TokenUsagePanel
          usage={state.usage}
          email={session.user.email}
          userName={userName}
          collapsed={desktopCollapsed}
          onExpand={() => setDesktopCollapsed(false)}
          logoutPending={logoutPending}
          onRefresh={() => void store.refreshUsage()}
          onLogout={() => void logout()}
          generalSettings={<ThemeSetting />}
          memorySettings={<MemorySettings request={request} />}
          networkSettings={
            <NetworkModeSwitch
              state={networkState}
              onDataUsage={(value) => void networkStore.setDataUsage(value)}
              onCheck={() => void networkStore.check()}
            />
          }
        />
      </ConversationSidebar>
      <ConversationManagementDialog
        target={managementTarget}
        onClose={() => setManagementTarget(null)}
        onRename={(id, title) => store.updateConversation({ title }, id)}
        onDelete={(id) => store.deleteConversation(id)}
        error={state.error}
      />
      <ConversationSearchDialog
        key={state.workspaceId}
        open={searchOpen}
        onOpenChange={setSearchOpen}
        workspaceId={state.workspaceId}
        request={request}
        returnFocus={searchReturnFocus}
        onOpenConversation={(id, messageId) =>
          void openSearchResult(id, messageId)
        }
        onNewChat={newChat}
      />
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="relative z-10 flex h-16 shrink-0 items-center justify-between gap-2 bg-background/95 px-3 sm:h-[72px] sm:px-5">
          <div className="flex min-w-0 items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="size-10 rounded-xl md:hidden"
              ref={mobileSidebarTrigger}
              data-chat-sidebar-trigger
              aria-label="대화 목록 열기"
              aria-expanded={sidebarOpen}
              onClick={() => setSidebarOpen(true)}
            >
              <PanelLeft className="size-5" aria-hidden="true" />
            </Button>
            <h1 className="sr-only">{state.selected?.title ?? '새 채팅'}</h1>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="hidden items-center gap-1.5 rounded-full border border-border/60 px-3 py-1.5 text-[11px] text-muted-foreground sm:inline-flex">
              <span
                className={cn(
                  'size-1.5 rounded-full',
                  networkBusy ? 'bg-muted-foreground/40' : 'bg-foreground/50',
                )}
                aria-hidden="true"
              />
              {searchLabel}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-10 rounded-xl md:hidden"
              aria-label="새 채팅"
              title="새 채팅"
              onClick={newChat}
            >
              <SquarePen className="size-5" aria-hidden="true" />
            </Button>
          </div>
        </header>
        {otherTask && (
          <output className="mx-3 mb-2 flex shrink-0 flex-wrap items-center gap-2 rounded-xl bg-muted/60 px-3 py-2 text-xs sm:mx-5">
            <span className="min-w-0 flex-1 truncate">
              {otherTask.title} · {taskLabel(otherTask)}
            </span>
            {otherTask.conversationId && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={() => open(otherTask.conversationId)}
              >
                대화 보기
              </Button>
            )}
            {otherTask.generation && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                disabled={
                  otherTask.cancelling || otherTask.generation.cancel_requested
                }
                onClick={() => void store.cancelTask(otherTask.conversationId)}
              >
                중단
              </Button>
            )}
            <span className="w-full text-muted-foreground">
              작업이 끝날 때까지 다음 질문을 작성할 수 있습니다.
            </span>
          </output>
        )}
        {(authError || state.error) && (
          <div
            role="alert"
            className="mx-3 mb-2 flex shrink-0 items-start gap-3 rounded-2xl bg-destructive/5 px-4 py-3 text-sm leading-6 text-destructive sm:mx-5"
          >
            <span className="flex-1">{authError || state.error}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                store.clearError();
                void store.refresh();
              }}
              className="h-8 shrink-0 rounded-lg text-xs"
            >
              다시 확인
            </Button>
          </div>
        )}
        {findingMessage && (
          <output className="px-5 py-2 text-sm text-muted-foreground">
            검색한 메시지로 이동하는 중…
          </output>
        )}
        <div
          className={cn(
            'flex min-h-0 flex-1 flex-col',
            welcome && 'overflow-y-auto',
          )}
        >
          {welcome && (
            <div className="min-h-8 flex-[0.85]" aria-hidden="true" />
          )}
          <div
            className={cn('relative min-h-0', welcome ? 'shrink-0' : 'flex-1')}
          >
            {/* 키보드로 긴 대화를 탐색할 수 있도록 스크롤 영역에 초점을 허용한다. */}
            {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
            <section
              ref={scrollViewport}
              /* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex */
              tabIndex={0}
              aria-label="대화 메시지"
              className={cn(
                'overflow-y-auto overscroll-contain',
                !welcome && 'h-full',
              )}
              style={{ overflowAnchor: 'none' }}
              {...viewportHandlers}
            >
              <div ref={scrollContent} className="mx-auto w-full max-w-3xl">
                {state.loading ? (
                  <output className="block px-6 py-12">
                    <p className="mb-8 text-sm text-muted-foreground">
                      저장된 대화를 불러오는 중...
                    </p>
                    <div
                      className="space-y-3 motion-safe:animate-pulse"
                      aria-hidden="true"
                    >
                      <div className="ml-auto mb-10 h-12 w-2/3 rounded-3xl bg-muted" />
                      <div className="h-3 w-2/5 rounded-full bg-muted" />
                      <div className="h-3 w-full rounded-full bg-muted" />
                      <div className="h-3 w-4/5 rounded-full bg-muted" />
                    </div>
                  </output>
                ) : state.messages.length === 0 ? (
                  <section
                    className="chat-welcome px-5 pb-7 pt-6 text-center sm:pb-9"
                    aria-label="대화 시작 화면"
                  >
                    <p className="mb-4 text-[13px] font-medium tracking-wide text-muted-foreground">
                      나의 AI 작업 공간
                    </p>
                    <h2 className="text-[28px] font-semibold leading-[1.3] tracking-[-0.045em] sm:text-[38px]">
                      무엇을 도와드릴까요?
                    </h2>
                    <p className="mt-4 text-sm leading-6 text-muted-foreground sm:text-[15px]">
                      작은 질문부터 새로운 아이디어까지, 편하게 이야기하세요.
                    </p>
                  </section>
                ) : (
                  <>
                    {state.messageCursor && (
                      <div className="p-4 text-center">
                        <Button
                          variant="ghost"
                          className="rounded-full text-xs"
                          disabled={loadingOlder}
                          onClick={() => void loadOlder()}
                        >
                          {loadingOlder
                            ? '이전 메시지 불러오는 중...'
                            : '이전 메시지 불러오기'}
                        </Button>
                      </div>
                    )}
                    {groupMessageVersions(state.messages).map((group) => (
                      <MessageVersionGroup
                        key={group.id}
                        group={group}
                        activeMessageId={generation?.assistant_message_id}
                        reveal={
                          reveal?.conversationId === state.selected?.id
                            ? reveal
                            : null
                        }
                        registerMessage={registerMessage}
                        onVersionChange={pauseFollowing}
                      >
                        {(message) => {
                          const streaming =
                            generation?.assistant_message_id === message.id;
                          return (
                            <ChatMessageView
                              message={message}
                              revealKey={
                                reveal?.messageId === message.id
                                  ? reveal.key
                                  : undefined
                              }
                              request={request}
                              fileSources={
                                streaming ? generation?.file_sources : undefined
                              }
                              userName={userName}
                              streaming={streaming}
                              cancelling={Boolean(cancelling)}
                              compacting={compacting}
                              searching={searching}
                              search={
                                streaming ? generation?.search : undefined
                              }
                              questionAnswers={state.questionDrafts[message.id]}
                              onQuestionAnswer={(index, value) =>
                                store.setQuestionAnswer(
                                  message.id,
                                  index,
                                  value,
                                )
                              }
                              onQuestionSubmit={() => {
                                if (!canSend) return;
                                followLatest();
                                void store.respondToQuestions(message.id, {
                                  thinking,
                                });
                              }}
                              reducedMotion={reducedMotion}
                              lengthLimited={
                                message.finish_reason === 'length' ||
                                state.lengthLimitedMessageIds.includes(
                                  message.id,
                                )
                              }
                              canSend={canSend}
                              onContinue={() => {
                                if (!canSend) return;
                                followLatest();
                                void store.continueAnswer(message.id, {
                                  thinking,
                                });
                              }}
                              onRegenerate={() => {
                                if (!canSend) return;
                                followLatest();
                                void store.regenerate(message.id, {
                                  thinking,
                                });
                              }}
                            />
                          );
                        }}
                      </MessageVersionGroup>
                    ))}
                    {state.newerMessageCursor !== null && (
                      <div className="p-4 text-center">
                        <Button
                          variant="ghost"
                          className="rounded-full text-xs"
                          disabled={loadingNewer}
                          onClick={() => void showNewerMessages()}
                        >
                          {loadingNewer
                            ? '이후 메시지 불러오는 중…'
                            : '이후 메시지 불러오기'}
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </div>
            </section>
            {(showLatest || state.newerMessageCursor !== null) && (
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label="최신 답변으로 이동"
                title="최신 답변으로 이동"
                className="absolute bottom-3 left-1/2 size-10 -translate-x-1/2 rounded-full border-border/70 bg-background/95 shadow-md backdrop-blur-sm"
                onClick={() => void showLatestMessages()}
              >
                <ArrowDown className="size-4" aria-hidden="true" />
              </Button>
            )}
          </div>
          <ChatComposer
            state={state}
            userName={userName}
            isGenerating={isGenerating}
            cancelling={Boolean(cancelling)}
            compacting={compacting}
            searching={searching}
            noBalance={Boolean(noBalance)}
            logoutPending={logoutPending}
            canSend={canSend}
            thinking={thinking}
            onUpload={(file) => void store.uploadFile(file)}
            onDeleteFile={(id) => void store.changeFile(id, 'delete')}
            onRetryFile={(id) => void store.changeFile(id, 'retry')}
            onRefreshFiles={() => void store.refreshFiles()}
            onReconnect={store.reconnect}
            onCancel={() => void store.cancel()}
            onSend={() => void send()}
            onDraftChange={(value) => store.setDraft(value)}
            onThinkingChange={setThinking}
          />
          {welcome && (
            <>
              <section
                className="chat-welcome mx-auto flex w-full max-w-3xl shrink-0 flex-wrap justify-center gap-2 px-5 pt-3 sm:gap-2.5"
                aria-label="예시 질문"
              >
                {suggestions.map(({ label, prompt, icon: Icon, color }) => (
                  <button
                    type="button"
                    key={label}
                    disabled={!canSend}
                    onClick={() => {
                      store.setDraft(prompt);
                      document.getElementById('chat-message-input')?.focus();
                    }}
                    className="flex min-h-11 items-center gap-2 rounded-full border border-border/80 bg-background px-3.5 py-2.5 text-xs text-muted-foreground transition-colors hover:border-foreground/20 hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-40 sm:px-4"
                  >
                    <Icon className={cn('size-4', color)} aria-hidden="true" />
                    {label}
                  </button>
                ))}
              </section>
              <div className="min-h-12 flex-1" aria-hidden="true" />
            </>
          )}
        </div>
        <div className="relative z-10 flex shrink-0 justify-end px-3 pb-[max(0.5rem,env(safe-area-inset-bottom))] sm:px-5">
          <ModelInfoMenu runtime={runtime} />
        </div>
      </section>
    </main>
  );
}
