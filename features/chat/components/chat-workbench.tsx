'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
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
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { AuthGate } from '../../auth/components/auth-gate';
import type { AuthenticatedProps } from '../../auth/components/auth-gate';
import { ChatStore } from '../state/chat-store';
import { ChatScrollFollow, preserveScrollAnchor } from '../scroll/chat-scroll';
import { ChatMessageView } from './chat-message-view';
import { ChatComposer } from './chat-composer';
import { ConversationSidebar } from './conversation-sidebar';
import { ModelInfoMenu } from './model-info-menu';
import { ConversationSearchDialog } from './conversation-search-dialog';
import { TokenUsagePanel } from '../../usage/components/token-usage-panel';
import { NetworkModeStore } from '../../network/state/network-mode-store.ts';
import { NetworkModeSwitch } from '../../network/components/network-mode-switch.tsx';
import { ThemeSetting } from '../../preferences/components/theme-setting.tsx';

const MODEL_ID = 'mlx-community/Qwen3.8-27B-4bit';
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
const pathId = () =>
  window.location.pathname.match(/^\/chat\/([0-9a-f-]{36})\/?$/i)?.[1] ?? null;
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
  const [desktopCollapsed, setDesktopCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchReturnFocus = useRef<HTMLElement | null>(null);
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
      : networkState.webSearch === 'auto'
        ? '자동 검색'
        : networkState.webSearch === 'on'
          ? '웹검색 켜짐'
          : '웹검색 꺼짐';

  const openSearch = useCallback(() => {
    searchReturnFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setSearchOpen(true);
  }, []);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === 'k' &&
        !event.isComposing &&
        !document.querySelector('[data-slot="dialog-content"][data-open]')
      ) {
        event.preventDefault();
        openSearch();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [openSearch]);

  useEffect(() => {
    // 설정 메뉴만 외부 클릭과 키보드로 닫고 대화의 접힌 원문은 유지한다.
    const openMenus = () =>
      document.querySelectorAll<HTMLDetailsElement>(
        'details[data-chat-menu][open]',
      );
    const closeOutside = (event: Event) => {
      for (const menu of openMenus()) {
        if (event.target instanceof Node && !menu.contains(event.target))
          menu.open = false;
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      // 모달이 열린 동안에는 모달의 닫기와 초점 복귀가 Escape를 처리한다.
      if (
        event.key !== 'Escape' ||
        event.defaultPrevented ||
        document.querySelector('[data-slot="dialog-content"][data-open]')
      )
        return;
      const menus = [...openMenus()];
      if (menus.length) {
        const focusedMenu = menus.find((menu) =>
          menu.contains(document.activeElement),
        );
        for (const menu of menus) menu.open = false;
        focusedMenu?.querySelector('summary')?.focus();
      } else setSidebarOpen(false);
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('focusin', closeOutside);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('focusin', closeOutside);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, []);

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
    <main className="chat-shell relative isolate flex h-[100dvh] w-full overflow-hidden bg-background">
      {sidebarOpen && (
        <button
          type="button"
          aria-label="대화 목록 닫기"
          onClick={() => setSidebarOpen(false)}
          className="absolute inset-0 z-20 bg-black/30 backdrop-blur-[2px] md:hidden"
        />
      )}
      <ConversationSidebar
        state={state}
        sidebarOpen={sidebarOpen}
        desktopCollapsed={desktopCollapsed}
        isGenerating={isGenerating}
        onClose={() => {
          setSidebarOpen(false);
          setDesktopCollapsed(true);
        }}
        onNewChat={newChat}
        onSearch={openSearch}
        onExpand={() => setDesktopCollapsed(false)}
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
          userName={userName}
          collapsed={desktopCollapsed}
          onExpand={() => setDesktopCollapsed(false)}
          logoutPending={logoutPending}
          onRefresh={() => void store.refreshUsage()}
          onLogout={() => void logout()}
          generalSettings={<ThemeSetting />}
          networkSettings={
            <NetworkModeSwitch
              state={networkState}
              onLocalOnly={(value) => void networkStore.setLocalOnly(value)}
              onCheck={() => void networkStore.check()}
              onWebSearch={networkStore.setWebSearch}
            />
          }
        />
      </ConversationSidebar>
      <ConversationSearchDialog
        key={state.workspaceId}
        open={searchOpen}
        onOpenChange={setSearchOpen}
        workspaceId={state.workspaceId}
        request={request}
        returnFocus={searchReturnFocus}
        onOpenConversation={open}
        onNewChat={newChat}
      />
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="relative z-10 flex h-16 shrink-0 items-center justify-between gap-2 bg-background/95 px-3 sm:h-[72px] sm:px-5">
          <div className="flex min-w-0 items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="size-10 rounded-xl md:hidden"
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
        {titleDraft !== null && (
          <form
            className="mx-3 mb-2 flex shrink-0 gap-2 rounded-2xl bg-muted p-3 sm:mx-5"
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
              className="h-10 rounded-xl bg-background"
              required
            />
            <Button
              type="submit"
              disabled={!titleDraft.trim()}
              className="h-10 rounded-xl"
            >
              저장
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="h-10 rounded-xl"
              onClick={() => setTitleDraft(null)}
            >
              취소
            </Button>
          </form>
        )}
        {confirmDelete && (
          <div className="mx-3 mb-2 flex shrink-0 flex-wrap items-center gap-2 rounded-2xl bg-destructive/5 p-3 text-sm sm:mx-5">
            <span className="flex-1">이 대화를 목록에서 삭제할까요?</span>
            <Button
              variant="outline"
              className="rounded-xl text-destructive"
              onClick={() => {
                void store.deleteConversation();
                setConfirmDelete(false);
              }}
            >
              삭제
            </Button>
            <Button
              variant="ghost"
              className="rounded-xl"
              onClick={() => setConfirmDelete(false)}
            >
              취소
            </Button>
          </div>
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
                          className="px-5 py-5 first:pt-8 last:pb-8 sm:px-6 sm:py-7"
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
                variant="outline"
                size="icon"
                aria-label="최신 답변으로 이동"
                title="최신 답변으로 이동"
                className="absolute bottom-3 left-1/2 size-10 -translate-x-1/2 rounded-full border-border/70 bg-background/95 shadow-md backdrop-blur-sm"
                onClick={followLatest}
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
            maxTokens={maxTokens}
            onReconnect={store.reconnect}
            onCancel={() => void store.cancel()}
            onSend={() => void send()}
            onDraftChange={(value) => store.setDraft(value)}
            onThinkingChange={setThinking}
            onMaxTokensChange={setMaxTokens}
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
