'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { AuthGate } from '../../auth/components/auth-gate';
import type { AuthenticatedProps } from '../../auth/components/auth-gate';
import { ChatStore } from '../state/chat-store';
import { ChatScrollFollow, preserveScrollAnchor } from '../scroll/chat-scroll';
import { ChatMessageView } from './chat-message-view';
import { ChatComposer } from './chat-composer';
import { ConversationSidebar } from './conversation-sidebar';
import { TokenUsagePanel } from '../../usage/components/token-usage-panel';
import { NetworkModeStore } from '../../network/state/network-mode-store.ts';
import { NetworkModeSwitch } from '../../network/components/network-mode-switch.tsx';

const MODEL_ID = 'mlx-community/Qwen3.8-27B-4bit';
const suggestions = [
  '냉장고에 달걀과 두부가 있어. 간단한 저녁 메뉴를 추천해줘',
  '하루 30분씩 꾸준히 공부할 수 있는 일주일 계획을 짜줘',
  'Python 비동기 코드를 쉽게 설명해줘',
];
const pathId = () =>
  window.location.pathname.match(/^\/chat\/([0-9a-f-]{36})\/?$/i)?.[1] ?? null;
const number = (value: number | null | undefined) =>
  value == null ? '—' : value.toLocaleString();
type WebMcpContext = {
  registerTool: (
    tool: {
      name: string;
      title: string;
      description: string;
      inputSchema: Record<string, unknown>;
      annotations: { readOnlyHint: boolean; untrustedContentHint: boolean };
      execute: (input: unknown) => Promise<Record<string, unknown>>;
    },
    options?: { signal?: AbortSignal },
  ) => void | Promise<void>;
};

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
  const [networkStore] = useState(() => new NetworkModeStore(request));
  const networkState = useSyncExternalStore(
    networkStore.subscribe,
    networkStore.getSnapshot,
    networkStore.getServerSnapshot,
  );
  const [store] = useState(
    () =>
      new ChatStore(
        request,
        (id) => {
          const target = id ? `/chat/${id}` : '/';
          if (window.location.pathname !== target)
            window.history.pushState(window.history.state, '', target);
        },
        networkStore.generationPolicy,
      ),
  );
  const state = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getServerSnapshot,
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [runtime, setRuntime] = useState({
    ready: false,
    online: false,
    backend: '확인 중',
    model: MODEL_ID,
    detail: '실행 상태 확인 중',
  });
  const scrollViewport = useRef<HTMLDivElement | null>(null);
  const scrollContent = useRef<HTMLDivElement | null>(null);
  const [scrollFollow] = useState(() => new ChatScrollFollow());
  const [showLatest, setShowLatest] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const messageElements = useRef(new Map<string, HTMLElement>());
  const scrollConversation = useRef<string | null>(null);
  const touchY = useRef<number | null>(null);
  const historyAnchor = useRef<{
    conversationId: string;
    messageId: string;
    offset: number;
  } | null>(null);
  const sendRef = useRef<(text: string) => Promise<boolean>>(async () => false);
  const generation = state.generation;
  const isGenerating = Boolean(generation);
  const cancelling = state.cancelling || generation?.cancel_requested;
  const compacting = generation?.stage === 'compacting';
  const searching = generation?.stage === 'searching';
  const noBalance =
    state.usage &&
    !state.usage.unlimited &&
    (state.usage.remaining_tokens ?? 0) <= 0;
  const canSend =
    !state.loading &&
    !state.sending &&
    !isGenerating &&
    !logoutPending &&
    !noBalance &&
    Boolean(state.workspaceId) &&
    state.selected?.status !== 'archived';

  useEffect(() => {
    void networkStore.initialize();
    return () => networkStore.dispose();
  }, [networkStore]);

  useEffect(() => {
    networkStore.observeSearch(generation?.search);
  }, [generation?.search, networkStore]);

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);

  useEffect(() => {
    void store.initialize(pathId());
    const navigateBack = () => {
      const id = pathId();
      if (id) void store.openConversation(id, false);
      else store.newDraft();
      setSidebarOpen(false);
    };
    const refresh = () => void store.refresh();
    const visible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    window.addEventListener('popstate', navigateBack);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', visible);
    return () => {
      window.removeEventListener('popstate', navigateBack);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', visible);
      store.dispose();
    };
  }, [store]);

  useEffect(() => {
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const response = await request('/api/status', {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Status unavailable');
        const data = (await response.json()) as {
          provider?: {
            ready?: boolean;
            backend?: string;
            model?: string;
            detail?: string;
          };
        };
        if (!controller.signal.aborted)
          setRuntime({
            ready: Boolean(data.provider?.ready),
            online: true,
            backend: data.provider?.backend?.toUpperCase() ?? '확인 중',
            model: data.provider?.model ?? MODEL_ID,
            detail: data.provider?.detail ?? '추론 서버 준비 중',
          });
      } catch {
        if (!controller.signal.aborted)
          setRuntime((previous) => ({
            ...previous,
            ready: false,
            online: false,
            detail: 'API 연결을 확인해 주세요.',
          }));
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 10_000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [request]);
  const updateScroll = useCallback(() => {
    const viewport = scrollViewport.current;
    if (!viewport) return;
    const target = scrollFollow.targetAfterResize(viewport);
    if (target !== null) {
      viewport.scrollTop = target;
      scrollFollow.recordPosition(viewport.scrollTop);
    }
    setShowLatest(scrollFollow.hasNewerContent(viewport));
  }, [scrollFollow]);
  const followLatest = () => {
    scrollFollow.resume();
    updateScroll();
  };
  const pauseFollowing = () => {
    scrollFollow.pause();
    updateScroll();
  };

  useLayoutEffect(() => {
    const viewport = scrollViewport.current;
    if (!viewport) return;
    const conversationId = state.selected?.id ?? null;
    if (
      scrollConversation.current !== conversationId ||
      state.messages.length === 0
    ) {
      scrollConversation.current = conversationId;
      historyAnchor.current = null;
      scrollFollow.resume();
    }
    const anchor = historyAnchor.current;
    if (
      anchor &&
      anchor.conversationId === conversationId &&
      state.messages[0]?.id !== anchor.messageId
    ) {
      const element = messageElements.current.get(anchor.messageId);
      if (element) {
        const offset =
          element.getBoundingClientRect().top -
          viewport.getBoundingClientRect().top;
        viewport.scrollTop = preserveScrollAnchor(
          viewport.scrollTop,
          anchor.offset,
          offset,
        );
        scrollFollow.recordPosition(viewport.scrollTop);
      }
      historyAnchor.current = null;
    } else if (!loadingOlder) historyAnchor.current = null;
    updateScroll();
  }, [
    state.messages,
    state.selected?.id,
    loadingOlder,
    scrollFollow,
    updateScroll,
  ]);

  useEffect(() => {
    const viewport = scrollViewport.current;
    const content = scrollContent.current;
    if (!viewport || !content) return;
    // 글자 단위 표시와 화면 크기 변화도 사용자가 선택한 따라가기 상태를 지킨다.
    const observer = new ResizeObserver(updateScroll);
    observer.observe(content);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [updateScroll]);

  const loadOlder = async () => {
    if (loadingOlder || !state.selected) return;
    const viewport = scrollViewport.current;
    const firstId = state.messages[0]?.id;
    if (!firstId) return;
    const element = messageElements.current.get(firstId);
    if (viewport && element) {
      pauseFollowing();
      historyAnchor.current = {
        conversationId: state.selected.id,
        messageId: firstId,
        offset:
          element.getBoundingClientRect().top -
          viewport.getBoundingClientRect().top,
      };
    }
    setLoadingOlder(true);
    await store.olderMessages();
    setLoadingOlder(false);
  };

  const send = async (text = state.draft) => {
    if (!canSend || !text.trim()) return false;
    followLatest();
    return store.send(text, { thinking, max_tokens: maxTokens });
  };
  useEffect(() => {
    sendRef.current = send;
  });
  useEffect(() => {
    const context = (document as Document & { modelContext?: WebMcpContext })
      .modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    try {
      const result = context.registerTool(
        {
          name: 'send_chat_message',
          title: '채팅 메시지 보내기',
          description:
            '현재 로그인한 계정의 대화에 메시지를 저장하고 응답 생성을 요청합니다.',
          inputSchema: {
            type: 'object',
            properties: {
              message: { type: 'string', minLength: 1, maxLength: 100000 },
            },
            required: ['message'],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: true },
          async execute(input) {
            const message =
              typeof input === 'object' && input !== null && 'message' in input
                ? (input as { message?: unknown }).message
                : undefined;
            if (
              typeof message !== 'string' ||
              !message.trim() ||
              message.length > 100000
            )
              throw new Error('메시지는 1~100,000자로 입력해야 합니다.');
            if (!(await sendRef.current(message)))
              throw new Error(
                '현재 메시지를 전송할 수 없습니다. 화면의 상태를 확인해 주세요.',
              );
            return { status: 'accepted' };
          },
        },
        { signal: lifecycle.signal },
      );
      void Promise.resolve(result).catch(() => undefined);
    } catch {
      /* WebMCP 지원 여부와 관계없이 화면의 채팅은 계속 사용할 수 있다. */
    }
    return () => lifecycle.abort();
  }, []);
  const open = (id: string) => {
    setTitleDraft(null);
    setConfirmDelete(false);
    setSidebarOpen(false);
    void store.openConversation(id);
  };
  const newChat = () => {
    setTitleDraft(null);
    setConfirmDelete(false);
    setSidebarOpen(false);
    store.newDraft();
  };

  return (
    <main className="relative flex h-[100dvh] w-full overflow-hidden bg-background">
      {sidebarOpen && (
        <button
          type="button"
          aria-label="대화 목록 닫기"
          onClick={() => setSidebarOpen(false)}
          className="absolute inset-0 z-20 bg-black/65 md:hidden"
        />
      )}
      <ConversationSidebar
        state={state}
        sidebarOpen={sidebarOpen}
        isGenerating={isGenerating}
        onClose={() => setSidebarOpen(false)}
        onNewChat={newChat}
        onWorkspaceChange={(id) => void store.setWorkspace(id)}
        onFilterChange={(filter) => void store.setFilter(filter)}
        onOpenConversation={open}
        onRename={(conversation) => {
          setTitleDraft(conversation.title);
          setConfirmDelete(false);
          setSidebarOpen(false);
        }}
        onTogglePin={(conversation) =>
          void store.updateConversation({ is_pinned: !conversation.is_pinned })
        }
        onToggleArchive={(conversation) =>
          void store.updateConversation({
            status: conversation.status === 'archived' ? 'active' : 'archived',
          })
        }
        onDelete={() => {
          setConfirmDelete(true);
          setSidebarOpen(false);
        }}
        onLoadMore={() => void store.loadConversations(true)}
      >
        <TokenUsagePanel
          usage={state.usage}
          email={session.user.email}
          logoutPending={logoutPending}
          onRefresh={() => void store.refreshUsage()}
          onLogoutAll={() => void logout(true)}
        />
      </ConversationSidebar>
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="shrink-0 border-b border-border bg-card">
          <div className="flex min-h-14 items-center gap-2 px-3 sm:px-5">
            <Button
              variant="ghost"
              size="sm"
              className="rounded-none md:hidden"
              aria-label="대화 목록 열기"
              aria-expanded={sidebarOpen}
              onClick={() => setSidebarOpen(true)}
            >
              ☰
            </Button>
            <h1 className="min-w-0 flex-1 truncate text-sm">
              {state.selected?.title ?? '새 채팅'}
            </h1>
            <span className="hidden max-w-28 truncate text-xs text-muted-foreground sm:block">
              {userName}
            </span>
            {session.user.platform_role === 'system' && (
              <span className="border border-primary/40 px-1.5 py-0.5 text-[10px] text-primary">
                system
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              disabled={logoutPending}
              onClick={() => void logout()}
              className="h-8 rounded-none px-2 text-xs"
            >
              로그아웃
            </Button>
          </div>
          <div className="flex min-h-8 items-center gap-4 border-t border-border/70 px-4 text-[11px] text-muted-foreground">
            <span className={runtime.ready ? 'text-primary' : 'text-amber-300'}>
              ● {runtime.backend}
            </span>
            <span>API: {runtime.online ? '연결됨' : '확인 중'}</span>
            <span className="ml-auto">
              {state.usage?.unlimited
                ? '토큰 한도 없음'
                : `남은 토큰 ${number(state.usage?.remaining_tokens)}`}
            </span>
          </div>
          <NetworkModeSwitch
            state={networkState}
            onLocalOnly={(value) => void networkStore.setLocalOnly(value)}
            onCheck={() => void networkStore.check()}
            onWebSearch={networkStore.setWebSearch}
          />
        </header>
        {(authError || state.error) && (
          <div
            role="alert"
            className="flex shrink-0 items-start gap-3 border-b border-rose-400/30 bg-rose-400/5 px-4 py-3 text-sm leading-6 text-rose-200"
          >
            <span className="flex-1">{authError || state.error}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                store.clearError();
                void store.refresh();
              }}
              className="h-7 shrink-0 rounded-none text-xs"
            >
              다시 확인
            </Button>
          </div>
        )}
        {titleDraft !== null && (
          <form
            className="flex shrink-0 gap-2 border-b border-border p-3"
            onSubmit={(event) => {
              event.preventDefault();
              void store
                .updateConversation({ title: titleDraft.trim() })
                .then((updated) => {
                  if (updated) setTitleDraft(null);
                });
            }}
          >
            <Input
              aria-label="대화 제목"
              value={titleDraft}
              maxLength={300}
              onChange={(event) => setTitleDraft(event.target.value)}
              className="h-9 rounded-none"
              required
            />
            <Button
              type="submit"
              disabled={!titleDraft.trim()}
              className="h-9 rounded-none"
            >
              저장
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="h-9 rounded-none"
              onClick={() => setTitleDraft(null)}
            >
              취소
            </Button>
          </form>
        )}
        {confirmDelete && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-rose-400/30 bg-rose-400/5 p-3 text-sm">
            <span className="flex-1">이 대화를 목록에서 삭제할까요?</span>
            <Button
              variant="outline"
              className="rounded-none text-rose-200"
              onClick={() => {
                void store.deleteConversation();
                setConfirmDelete(false);
              }}
            >
              삭제
            </Button>
            <Button
              variant="ghost"
              className="rounded-none"
              onClick={() => setConfirmDelete(false)}
            >
              취소
            </Button>
          </div>
        )}
        <div className="relative min-h-0 flex-1">
          {/* 키보드로 긴 대화를 탐색할 수 있도록 스크롤 영역에 초점을 허용한다. */}
          {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
          <section
            ref={scrollViewport}
            /* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex */
            tabIndex={0}
            aria-label="대화 메시지"
            className="h-full overflow-y-auto overscroll-contain"
            style={{ overflowAnchor: 'none' }}
            onScroll={(event) => {
              const viewport = event.currentTarget;
              scrollFollow.onScroll(viewport);
              const anchor = historyAnchor.current;
              const element = anchor
                ? messageElements.current.get(anchor.messageId)
                : null;
              if (anchor && element)
                anchor.offset =
                  element.getBoundingClientRect().top -
                  viewport.getBoundingClientRect().top;
              setShowLatest(scrollFollow.hasNewerContent(viewport));
            }}
            onWheel={(event) => {
              if (event.deltaY < 0) pauseFollowing();
            }}
            onTouchStart={(event) => {
              touchY.current = event.touches[0]?.clientY ?? null;
            }}
            onTouchMove={(event) => {
              const currentY = event.touches[0]?.clientY;
              if (
                currentY != null &&
                touchY.current != null &&
                currentY > touchY.current
              )
                pauseFollowing();
              touchY.current = currentY ?? null;
            }}
            onTouchEnd={() => {
              touchY.current = null;
            }}
            onKeyDown={(event) => {
              if (
                ['ArrowUp', 'PageUp', 'Home'].includes(event.key) ||
                (event.key === ' ' && event.shiftKey)
              )
                pauseFollowing();
            }}
          >
            <div ref={scrollContent} className="mx-auto w-full max-w-5xl">
              {state.loading ? (
                <p
                  className="px-6 py-12 text-sm text-muted-foreground"
                  aria-live="polite"
                >
                  저장된 대화를 불러오는 중...
                </p>
              ) : state.messages.length === 0 ? (
                <section
                  className="px-5 py-12 sm:px-10 sm:py-16"
                  aria-label="대화 시작 화면"
                >
                  <p className="text-sm text-primary">
                    Project LLM{' '}
                    <span className="text-muted-foreground">
                      / local workspace
                    </span>
                  </p>
                  <p className="mt-4 text-sm leading-7 text-muted-foreground">
                    Apple Silicon에서 실행되는 로컬 LLM 채팅
                    <br />
                    {runtime.detail}
                  </p>
                  <div className="mt-10 border-l-2 border-primary pl-5">
                    <h2 className="break-words text-lg">
                      <span className="text-primary">{userName}</span>
                      <span className="text-muted-foreground">:~/chat$ </span>
                      대화를 시작하세요.
                    </h2>
                    <p className="mt-3 text-sm leading-6 text-muted-foreground">
                      대화는 계정에 저장되어 다른 기기에서도 이어갈 수 있습니다.
                    </p>
                  </div>
                  <div className="mt-8 border-y border-border">
                    {suggestions.map((suggestion, index) => (
                      <button
                        type="button"
                        key={suggestion}
                        disabled={!canSend}
                        onClick={() => {
                          store.setDraft(suggestion);
                          void send(suggestion);
                        }}
                        className="flex w-full gap-3 border-b border-border/60 px-3 py-4 text-left text-sm leading-6 hover:bg-primary/5 disabled:opacity-50 last:border-b-0"
                      >
                        <span className="text-muted-foreground">
                          0{index + 1}
                        </span>
                        <span className="text-primary">$</span>
                        <span>{suggestion}</span>
                      </button>
                    ))}
                  </div>
                </section>
              ) : (
                <>
                  {state.messageCursor && (
                    <div className="border-b border-border p-3 text-center">
                      <Button
                        variant="ghost"
                        className="rounded-none text-xs"
                        disabled={loadingOlder}
                        onClick={() => void loadOlder()}
                      >
                        {loadingOlder
                          ? '이전 메시지 불러오는 중...'
                          : '이전 메시지 불러오기'}
                      </Button>
                    </div>
                  )}
                  {state.messages.map((message) => {
                    const streaming =
                      generation?.assistant_message_id === message.id;
                    return (
                      <article
                        key={message.id}
                        ref={(element) => {
                          if (element)
                            messageElements.current.set(message.id, element);
                          else messageElements.current.delete(message.id);
                        }}
                        className={cn(
                          'border-b border-border px-5 py-6 sm:px-10',
                          message.role === 'user' && 'bg-sky-300/[0.025]',
                        )}
                      >
                        <ChatMessageView
                          message={message}
                          userName={userName}
                          streaming={streaming}
                          cancelling={Boolean(cancelling)}
                          compacting={compacting}
                          searching={searching}
                          search={streaming ? generation?.search : undefined}
                          reducedMotion={reducedMotion}
                          lengthLimited={
                            message.finish_reason === 'length' ||
                            state.lengthLimitedMessageIds.includes(message.id)
                          }
                          canSend={canSend}
                          onRegenerate={() => {
                            if (!canSend) return;
                            followLatest();
                            void store.regenerate(message.id, {
                              thinking,
                              max_tokens: maxTokens,
                            });
                          }}
                        />
                      </article>
                    );
                  })}
                </>
              )}
            </div>
          </section>
          {showLatest && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="absolute bottom-4 right-4 h-9 rounded-none border border-border shadow-lg"
              onClick={followLatest}
            >
              최신 답변으로 ↓
            </Button>
          )}
        </div>
        <ChatComposer
          state={state}
          userName={userName}
          model={runtime.model}
          isGenerating={isGenerating}
          cancelling={Boolean(cancelling)}
          compacting={compacting}
          searching={searching}
          noBalance={Boolean(noBalance)}
          logoutPending={logoutPending}
          canSend={canSend}
          thinking={thinking}
          maxTokens={maxTokens}
          onReconnect={store.reconnect}
          onCancel={() => void store.cancel()}
          onSend={() => void send()}
          onDraftChange={(value) => store.setDraft(value)}
          onThinkingChange={setThinking}
          onMaxTokensChange={setMaxTokens}
        />
      </section>
    </main>
  );
}
