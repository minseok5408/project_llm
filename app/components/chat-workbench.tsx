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
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { AuthGate } from './auth-gate';
import type { AuthenticatedProps } from './auth-gate';
import { ChatStore } from './chat-store';
import { ChatScrollFollow, preserveScrollAnchor } from './chat-scroll';
import { StreamingText } from './streaming-text';

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
      {(auth) => <Workbench key={auth.session.user.id} {...auth} />}
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
  const [store] = useState(
    () =>
      new ChatStore(request, (id) => {
        const target = id ? `/chat/${id}` : '/';
        if (window.location.pathname !== target)
          window.history.pushState(window.history.state, '', target);
      }),
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
    <main className="relative flex h-[100dvh] w-full overflow-hidden bg-background">
      {sidebarOpen && (
        <button
          type="button"
          aria-label="대화 목록 닫기"
          onClick={() => setSidebarOpen(false)}
          className="absolute inset-0 z-20 bg-black/65 md:hidden"
        />
      )}
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
            onClick={() => setSidebarOpen(false)}
            className="rounded-none md:hidden"
            aria-label="목록 닫기"
          >
            닫기
          </Button>
        </div>
        <div className="space-y-3 border-b border-border p-3">
          <Button onClick={newChat} className="h-10 w-full rounded-none">
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
            onChange={(event) => void store.setWorkspace(event.target.value)}
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
                onClick={() => void store.setFilter(filter)}
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
                  open(conversation.id);
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
                    onClick={() => {
                      setTitleDraft(conversation.title);
                      setConfirmDelete(false);
                      setSidebarOpen(false);
                    }}
                  >
                    제목
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 rounded-none px-2 text-[11px]"
                    onClick={() =>
                      void store.updateConversation({
                        is_pinned: !conversation.is_pinned,
                      })
                    }
                  >
                    {conversation.is_pinned ? '고정 해제' : '고정'}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={isGenerating}
                    className="h-7 rounded-none px-2 text-[11px]"
                    onClick={() =>
                      void store.updateConversation({
                        status:
                          conversation.status === 'archived'
                            ? 'active'
                            : 'archived',
                      })
                    }
                  >
                    {conversation.status === 'archived' ? '복원' : '보관'}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={isGenerating}
                    className="h-7 rounded-none px-2 text-[11px] text-rose-300"
                    onClick={() => {
                      setConfirmDelete(true);
                      setSidebarOpen(false);
                    }}
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
              onClick={() => void store.loadConversations(true)}
              className="mt-1 w-full rounded-none text-xs"
            >
              대화 더 보기
            </Button>
          )}
        </nav>
        <section
          className="shrink-0 border-t border-border px-4 py-4"
          aria-label="토큰 사용량"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-xs text-muted-foreground">토큰 사용량</h2>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 rounded-none px-1 text-[10px]"
              onClick={() => void store.refreshUsage()}
            >
              새로고침
            </Button>
          </div>
          <p className="mt-2 text-lg text-primary">
            {state.usage?.unlimited
              ? '한도 없음'
              : `${number(state.usage?.remaining_tokens)} 남음`}
          </p>
          {state.usage?.budget_source === 'free_monthly' && (
            <p className="mt-2 text-xs text-muted-foreground">
              무료 · 매월 {number(state.usage.token_limit)}토큰
            </p>
          )}
          {state.usage?.budget_source === 'plan' && state.usage.plan_name && (
            <p className="mt-2 text-xs text-muted-foreground">
              {state.usage.plan_name}
            </p>
          )}
          <dl className="mt-3 space-y-1 text-[11px] text-muted-foreground">
            <div className="flex justify-between">
              <dt>실사용</dt>
              <dd>{number(state.usage?.used_tokens)}</dd>
            </div>
            {!state.usage?.unlimited && (
              <div className="flex justify-between">
                <dt>
                  {state.usage?.budget_source === 'free_monthly'
                    ? '월 한도'
                    : '기간 한도'}
                </dt>
                <dd>{number(state.usage?.token_limit)}</dd>
              </div>
            )}
          </dl>
          {state.usage?.ends_at && (
            <p className="mt-2 text-[10px] text-muted-foreground">
              {state.usage.budget_source === 'free_monthly'
                ? `다음 갱신 ${new Date(state.usage.ends_at).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric' })} (한국시간)`
                : `${new Date(state.usage.ends_at).toLocaleDateString('ko-KR')}까지`}
            </p>
          )}
          {state.usage?.budget_source === 'free_monthly' && (
            <p className="mt-2 text-[10px] leading-5 text-muted-foreground">
              매월 1일 갱신 · 남은 토큰은 이월되지 않습니다.
            </p>
          )}
          <p
            className="mt-3 truncate text-[11px] text-muted-foreground"
            title={session.user.email}
          >
            {session.user.email}
          </p>
          <Button
            variant="ghost"
            disabled={logoutPending}
            onClick={() => void logout(true)}
            className="mt-2 h-7 rounded-none px-0 text-[10px] text-muted-foreground"
          >
            모든 기기에서 로그아웃
          </Button>
        </section>
      </aside>
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
                        <div className="mb-3 flex items-center gap-2 text-xs">
                          <span className="text-muted-foreground">
                            {String(message.sequence).padStart(3, '0')}
                          </span>
                          <span
                            className={cn(
                              'max-w-48 truncate',
                              message.role === 'user'
                                ? 'text-sky-300'
                                : 'text-primary',
                            )}
                            title={
                              message.role === 'user' ? userName : undefined
                            }
                          >
                            {message.role === 'user'
                              ? userName
                              : message.role === 'system'
                                ? 'system'
                                : 'qwen@mlx'}
                          </span>
                          <span className="text-muted-foreground">
                            {message.role === 'user'
                              ? ':~/chat$'
                              : ':~/response>'}
                          </span>
                          {streaming && (
                            <span className="ml-auto text-amber-300">
                              {cancelling
                                ? '[중단 중]'
                                : compacting
                                  ? '[정리 중]'
                                  : '[생성 중]'}
                            </span>
                          )}
                          {!streaming &&
                            ['failed', 'cancelled', 'pending'].includes(
                              message.status,
                            ) && (
                              <span className="ml-auto text-xs text-muted-foreground">
                                {message.status === 'cancelled'
                                  ? '중단됨'
                                  : message.status === 'failed'
                                    ? '생성 실패'
                                    : '확인 대기'}
                              </span>
                            )}
                        </div>
                        <p
                          className={cn(
                            'whitespace-pre-wrap break-words border-l pl-4 text-base leading-7',
                            message.role === 'user'
                              ? 'border-sky-300/50 text-slate-100'
                              : 'border-primary/50 text-foreground',
                            streaming &&
                              !cancelling &&
                              !reducedMotion &&
                              'streaming-caret',
                          )}
                        >
                          <StreamingText
                            content={message.content}
                            active={streaming}
                            frozen={Boolean(streaming && cancelling)}
                            reducedMotion={reducedMotion}
                            fallback={
                              streaming
                                ? cancelling
                                  ? '응답 생성을 중단하고 있습니다.'
                                  : compacting
                                    ? '이전 대화를 정리 중…'
                                    : '응답을 준비하는 중...'
                                : '저장된 응답이 없습니다.'
                            }
                          />
                        </p>
                        {message.token_count != null &&
                          message.role === 'assistant' && (
                            <p className="mt-3 pl-4 text-[10px] text-muted-foreground">
                              출력 {number(message.token_count)} 토큰
                            </p>
                          )}
                        {!streaming &&
                          message.role === 'assistant' &&
                          state.lengthLimitedMessageIds.includes(
                            message.id,
                          ) && (
                            <p className="mt-3 pl-4 text-xs text-amber-200">
                              길이 제한에 도달했습니다. “이어서 말해”라고
                              입력하면 이어서 답합니다.
                            </p>
                          )}
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
        <footer className="shrink-0 border-t border-border bg-card">
          <div className="mx-auto max-w-5xl px-3 py-3 sm:px-6 sm:py-4">
            {isGenerating && (
              <div
                className="mb-3 flex flex-wrap items-center gap-2 text-xs text-amber-200"
                aria-live="polite"
              >
                <span className="flex-1">
                  {cancelling
                    ? '중단 확인 중 · 다음 질문을 작성할 수 있습니다.'
                    : state.stream === 'paused'
                      ? '생성 상태 연결이 끊겼습니다.'
                      : state.stream === 'reconnecting'
                        ? '생성 상태에 다시 연결하는 중...'
                        : generation?.status === 'queued'
                          ? '생성 대기 중...'
                          : compacting
                            ? '이전 대화를 정리 중…'
                            : '응답 생성 중...'}
                </span>
                {state.stream === 'paused' && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={store.reconnect}
                    className="h-7 rounded-none text-xs"
                  >
                    다시 연결
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={Boolean(cancelling)}
                  onClick={() => void store.cancel()}
                  className="h-7 rounded-none border-rose-400/40 text-xs text-rose-200"
                >
                  {cancelling ? '중단 중...' : '답변 중단'}
                </Button>
              </div>
            )}
            {noBalance && (
              <p className="mb-3 text-xs leading-5 text-amber-200">
                사용할 수 있는 토큰을 모두 사용했습니다.
              </p>
            )}
            {state.selected?.status === 'archived' && (
              <p className="mb-3 text-xs text-muted-foreground">
                보관된 대화입니다. 대화 목록에서 복원하면 이어갈 수 있습니다.
              </p>
            )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void send();
              }}
              className="border border-border bg-background focus-within:border-primary/70"
            >
              <div className="flex items-center justify-between gap-3 border-b border-border/60 px-3 py-2 text-[11px] text-muted-foreground">
                <span className="min-w-0 truncate">
                  <span className="text-primary">{userName}</span>:~/chat$
                  compose --stdin
                </span>
                <details className="relative shrink-0">
                  <summary className="cursor-pointer text-primary">
                    생성 설정
                  </summary>
                  <div className="absolute bottom-7 right-0 z-10 w-72 border border-border bg-card p-4 shadow-xl">
                    <p className="break-all text-xs leading-5">
                      {state.selected?.model ?? runtime.model}
                    </p>
                    <p className="mt-2 text-[10px]">
                      MLX / 4비트 · 문맥 32,768 토큰
                    </p>
                    <div className="mt-4 flex items-center justify-between">
                      <label htmlFor="thinking">깊이 생각하기</label>
                      <Switch
                        id="thinking"
                        checked={thinking}
                        onCheckedChange={setThinking}
                        disabled={isGenerating || state.sending}
                      />
                    </div>
                    <p className="mt-4">최대 출력 토큰</p>
                    <div className="mt-2 grid grid-cols-3 gap-1">
                      {[512, 1024, 2048].map((value) => (
                        <Button
                          type="button"
                          key={value}
                          size="sm"
                          variant={value === maxTokens ? 'secondary' : 'ghost'}
                          disabled={isGenerating || state.sending}
                          onClick={() => setMaxTokens(value)}
                          className="h-8 rounded-none text-xs"
                        >
                          {value}
                        </Button>
                      ))}
                    </div>
                  </div>
                </details>
              </div>
              <div className="flex items-end">
                <span className="py-3 pl-3 text-primary" aria-hidden="true">
                  &gt;_
                </span>
                <Textarea
                  value={state.draft}
                  onChange={(event) => store.setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === 'Enter' &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                  rows={2}
                  maxLength={100000}
                  disabled={
                    (isGenerating && !cancelling) ||
                    state.sending ||
                    logoutPending
                  }
                  placeholder="명령 또는 질문 입력..."
                  aria-label="채팅 메시지"
                  className="max-h-40 min-h-[3.5rem] resize-none rounded-none border-0 bg-transparent px-3 py-3 text-base leading-6 shadow-none focus-visible:ring-0 dark:bg-transparent"
                />
                <Button
                  type="submit"
                  disabled={!state.draft.trim() || !canSend}
                  className="m-2 h-9 rounded-none px-4 text-xs"
                >
                  {state.sending ? '접수 중...' : '전송 ↵'}
                </Button>
              </div>
            </form>
            <div className="mt-2 flex flex-wrap justify-between gap-2 text-[10px] text-muted-foreground">
              <span>Enter: 전송 · Shift+Enter: 줄바꿈</span>
              <span>답변이 끝나거나 중단된 뒤 확인된 사용량만 차감합니다.</span>
            </div>
          </div>
        </footer>
      </section>
    </main>
  );
}
