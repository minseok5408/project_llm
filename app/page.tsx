'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

type Role = 'user' | 'assistant';

type ChatMessage = {
  id: string;
  role: Role;
  content: string;
};

type RuntimeStatus = {
  gateway: 'checking' | 'online' | 'offline';
  provider: 'ready' | 'starting' | 'offline';
  backend: 'mlx' | 'mock' | 'unknown';
  model: string;
  detail: string;
};

type StreamEvent = {
  event: string;
  data: Record<string, unknown>;
};

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

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000';
const MODEL_ID = 'mlx-community/Qwen3.8-27B-4bit';

const suggestions = [
  '확장 가능한 RAG 구조를 설계해줘',
  '이 프로젝트의 다음 개발 단계를 정리해줘',
  'Python 비동기 코드를 쉽게 설명해줘',
];

const gatewayLabels: Record<RuntimeStatus['gateway'], string> = {
  checking: '확인 중',
  online: '연결됨',
  offline: '연결 끊김',
};

const providerLabels: Record<RuntimeStatus['provider'], string> = {
  ready: '준비됨',
  starting: '시작 중',
  offline: '연결 끊김',
};

const backendLabels: Record<RuntimeStatus['backend'], string> = {
  mlx: 'MLX',
  mock: '테스트용',
  unknown: '알 수 없음',
};

function makeId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

function parseSseBlock(block: string): StreamEvent | null {
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }

  if (!dataLines.length) return null;

  try {
    return { event, data: JSON.parse(dataLines.join('\n')) };
  } catch {
    return null;
  }
}

async function consumeSse(
  response: Response,
  onEvent: (event: StreamEvent) => void,
) {
  if (!response.body) throw new Error('스트림을 열 수 없습니다.');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replaceAll('\r\n', '\n');

      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const parsed = parseSseBlock(buffer.slice(0, boundary));
        if (parsed) onEvent(parsed);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf('\n\n');
      }

      if (done) break;
    }

    const trailing = parseSseBlock(buffer.trim());
    if (trailing) onEvent(trailing);
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
}

function ConnectionDot({ status }: { status: RuntimeStatus['provider'] }) {
  return (
    <span className="relative flex size-2" aria-hidden="true">
      {status === 'ready' && (
        <span className="absolute inline-flex size-full animate-ping bg-primary opacity-40" />
      )}
      <span
        className={cn(
          'relative inline-flex size-2 border',
          status === 'ready' && 'border-primary bg-primary',
          status === 'starting' && 'border-amber-300 bg-amber-300',
          status === 'offline' && 'border-rose-400 bg-rose-400',
        )}
      />
    </span>
  );
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [runtime, setRuntime] = useState<RuntimeStatus>({
    gateway: 'checking',
    provider: 'starting',
    backend: 'unknown',
    model: MODEL_ID,
    detail: '연결 확인 중',
  });
  const controllerRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const sendMessageRef = useRef<(text: string) => Promise<boolean>>(
    async () => false,
  );

  const refreshStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/status`, {
        cache: 'no-store',
      });
      if (!response.ok) throw new Error('status request failed');
      const data = (await response.json()) as {
        provider?: {
          ready?: boolean;
          backend?: string;
          model?: string;
          detail?: string;
        };
      };
      setRuntime({
        gateway: 'online',
        provider: data.provider?.ready ? 'ready' : 'starting',
        backend: data.provider?.backend === 'mock' ? 'mock' : 'mlx',
        model: data.provider?.model ?? MODEL_ID,
        detail: data.provider?.detail ?? '추론 서버 시작 대기 중',
      });
    } catch {
      setRuntime({
        gateway: 'offline',
        provider: 'offline',
        backend: 'unknown',
        model: MODEL_ID,
        detail: 'Python API가 실행되지 않았습니다',
      });
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(refreshStatus, 0);
    const timer = window.setInterval(refreshStatus, 10_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refreshStatus]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = async (text = input) => {
    const content = text.trim();
    if (!content || isGenerating || controllerRef.current) return false;

    const userMessage: ChatMessage = {
      id: makeId(),
      role: 'user',
      content,
    };
    const assistantId = makeId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
    };
    const history = [...messages, userMessage];

    setMessages([...history, assistantMessage]);
    setInput('');
    setError(null);
    setIsGenerating(true);

    const controller = new AbortController();
    controllerRef.current = controller;

    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: history.map(({ role, content: messageContent }) => ({
            role,
            content: messageContent,
          })),
          options: {
            thinking,
            max_tokens: maxTokens,
          },
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const detail = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(detail?.detail ?? `요청 실패 (${response.status})`);
      }

      await consumeSse(response, ({ event, data }) => {
        if (event === 'delta' && typeof data.text === 'string') {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, content: message.content + data.text }
                : message,
            ),
          );
        }

        if (event === 'error') {
          throw new Error(
            typeof data.message === 'string'
              ? data.message
              : '생성 중 오류가 발생했습니다.',
          );
        }
      });
    } catch (requestError) {
      if (
        requestError instanceof DOMException &&
        requestError.name === 'AbortError'
      ) {
        return false;
      }

      const message =
        requestError instanceof Error
          ? requestError.message
          : '알 수 없는 오류가 발생했습니다.';
      setError(message);
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId && !item.content
            ? {
                ...item,
                content:
                  '응답을 생성하지 못했습니다. 연결 상태를 확인해주세요.',
              }
            : item,
        ),
      );
      void refreshStatus();
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setIsGenerating(false);
      }
    }

    return true;
  };

  useEffect(() => {
    sendMessageRef.current = sendMessage;
  });

  useEffect(() => {
    const context = (document as Document & { modelContext?: WebMcpContext })
      .modelContext;
    if (!context?.registerTool) return;

    const lifecycle = new AbortController();
    try {
      const registration = context.registerTool(
        {
          name: 'send_chat_message',
          title: '채팅 메시지 보내기',
          description:
            '화면에 열린 Project LLM 대화로 메시지 하나를 보내고 실시간 응답을 기다립니다.',
          inputSchema: {
            type: 'object',
            properties: {
              message: {
                type: 'string',
                minLength: 1,
                maxLength: 100000,
                description: '보낼 사용자 메시지입니다.',
              },
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
            if (typeof message !== 'string' || !message.trim()) {
              throw new Error('메시지에는 한 글자 이상 입력해야 합니다.');
            }
            if (message.length > 100000) {
              throw new Error('메시지는 100,000자 이하여야 합니다.');
            }

            const accepted = await sendMessageRef.current(message);
            if (!accepted)
              throw new Error('응답 생성 중이거나 메시지가 비어 있습니다.');
            return { status: 'sent' };
          },
        },
        { signal: lifecycle.signal },
      );
      void Promise.resolve(registration).catch(() => undefined);
    } catch {
      // WebMCP is optional; the visible chat remains fully functional.
    }
    return () => lifecycle.abort();
  }, []);

  const stopGeneration = () => {
    controllerRef.current?.abort();
  };

  const clearConversation = () => {
    if (isGenerating) stopGeneration();
    setMessages([]);
    setError(null);
  };

  return (
    <main className="grid h-[100dvh] w-full grid-cols-1 overflow-hidden bg-background lg:grid-cols-[minmax(0,1fr)_21rem]">
      <section className="flex min-w-0 flex-col overflow-hidden border-border lg:border-r">
        <header className="shrink-0 border-b border-border bg-card">
          <div className="flex h-11 items-center border-b border-border/70 px-3 sm:px-4">
            <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
              <span className="text-primary">$</span> Project LLM
              <span className="text-muted-foreground">
                {' '}
                — zsh — ~/project_llm
              </span>
            </h1>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={clearConversation}
              disabled={!messages.length}
              className="h-7 rounded-none border border-transparent px-2 text-xs text-muted-foreground hover:border-border hover:bg-muted hover:text-primary"
            >
              대화 지우기
            </Button>
          </div>
          <div
            className="flex h-8 items-center gap-4 overflow-x-auto whitespace-nowrap px-3 text-xs text-muted-foreground sm:px-4"
            aria-live="polite"
          >
            <span className="flex items-center gap-2">
              <ConnectionDot status={runtime.provider} />
              추론 엔진: {backendLabels[runtime.backend]}
            </span>
            <span>
              API:{' '}
              <strong
                className={cn(
                  'font-normal',
                  runtime.gateway === 'online'
                    ? 'text-primary'
                    : runtime.gateway === 'checking'
                      ? 'text-amber-300'
                      : 'text-rose-300',
                )}
              >
                {gatewayLabels[runtime.gateway]}
              </strong>
            </span>
            <span>문맥: 32,768 토큰</span>
            <span>작업: 1/1</span>
            <span className="ml-auto hidden text-border sm:inline">
              tty:qwen.0
            </span>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col">
            {messages.length === 0 ? (
              <section
                className="px-4 py-8 sm:px-8 sm:py-12"
                aria-label="터미널 시작 화면"
              >
                <div className="max-w-3xl">
                  <div className="mb-7 text-sm leading-7 text-muted-foreground">
                    <p>
                      <span className="text-primary">Project LLM</span>{' '}
                      <span className="text-foreground">0.1.0</span>
                    </p>
                    <p>로컬 추론 터미널 / Apple Silicon arm64</p>
                    <p className="mt-4">
                      <span className="text-primary">[정상]</span> FastAPI 연결
                      완료
                    </p>
                    <p>
                      <span
                        className={cn(
                          runtime.provider === 'ready'
                            ? 'text-primary'
                            : runtime.provider === 'offline'
                              ? 'text-rose-300'
                              : 'text-amber-300',
                        )}
                      >
                        {runtime.provider === 'ready'
                          ? '[정상]'
                          : runtime.provider === 'offline'
                            ? '[실패]'
                            : '[대기]'}
                      </span>{' '}
                      {runtime.detail}
                    </p>
                    <p>
                      <span className="text-primary">[정상]</span> 모델 문맥
                      할당: 32,768 토큰
                    </p>
                  </div>

                  <div className="border-l-2 border-primary pl-4 sm:pl-5">
                    <p className="text-base leading-7 text-foreground">
                      <span className="text-primary">you@local</span>
                      <span className="text-muted-foreground">:</span>
                      <span className="text-sky-300">~/chat</span>
                      <span className="text-muted-foreground">$ </span>
                      대화를 시작하세요.
                    </p>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      아래 예제를 실행하거나 하단 프롬프트에 직접 입력할 수
                      있습니다.
                    </p>
                  </div>

                  <div className="mt-7 border-y border-border">
                    <div className="border-b border-border bg-muted/30 px-3 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
                      ./examples --interactive
                    </div>
                    {suggestions.map((suggestion, index) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => void sendMessage(suggestion)}
                        className="group flex w-full items-start gap-3 border-b border-border/70 px-3 py-3 text-left text-sm leading-6 text-foreground transition last:border-b-0 hover:bg-primary/[0.055] focus-visible:bg-primary/[0.055] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary"
                      >
                        <span className="text-muted-foreground">
                          {String(index + 1).padStart(2, '0')}
                        </span>
                        <span className="text-primary">$</span>
                        <span className="group-hover:text-primary">
                          {suggestion}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              </section>
            ) : (
              <div>
                {messages.map((message, index) => {
                  const streaming =
                    isGenerating &&
                    message.role === 'assistant' &&
                    index === messages.length - 1;

                  return (
                    <article
                      key={message.id}
                      className={cn(
                        'border-b border-border px-4 py-5 sm:px-8',
                        message.role === 'user'
                          ? 'bg-sky-300/[0.025]'
                          : 'bg-transparent',
                      )}
                    >
                      <div className="mb-3 flex items-center gap-2 text-xs">
                        <span className="text-muted-foreground">
                          {String(index + 1).padStart(3, '0')}
                        </span>
                        <span
                          className={cn(
                            message.role === 'user'
                              ? 'text-sky-300'
                              : 'text-primary',
                          )}
                        >
                          {message.role === 'user' ? 'you@local' : 'qwen@mlx'}
                        </span>
                        <span className="text-muted-foreground">
                          {message.role === 'user'
                            ? ':~/chat$'
                            : ':~/response>'}
                        </span>
                        {streaming && (
                          <span className="ml-auto text-amber-300">
                            [생성 중]
                          </span>
                        )}
                      </div>
                      <p
                        className={cn(
                          'whitespace-pre-wrap break-words border-l pl-4 text-base leading-7',
                          message.role === 'user'
                            ? 'border-sky-300/50 text-slate-100'
                            : 'border-primary/50 text-foreground',
                          streaming && 'streaming-caret',
                        )}
                      >
                        {message.content ||
                          (streaming ? '프로세스를 시작하는 중...' : '')}
                      </p>
                    </article>
                  );
                })}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>
        </div>

        <footer className="shrink-0 border-t border-border bg-card">
          <div className="mx-auto max-w-5xl px-3 py-3 sm:px-6 sm:py-4">
            {error && (
              <div
                role="alert"
                className="mb-3 border border-rose-400/40 bg-rose-400/[0.06] px-3 py-2 text-sm leading-6 text-rose-200"
              >
                <span className="mr-2 text-rose-400">오류:</span>
                {error}
              </div>
            )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void sendMessage();
              }}
              className="border border-border bg-background focus-within:border-primary/70 focus-within:ring-1 focus-within:ring-primary/20"
            >
              <div className="border-b border-border/70 px-3 py-2 text-xs text-muted-foreground">
                <span className="text-primary">you@local</span>:
                <span className="text-sky-300">~/chat</span>
                <span>$ compose --stdin</span>
              </div>
              <div className="flex items-end">
                <span
                  className="shrink-0 py-3 pl-3 text-base text-primary"
                  aria-hidden="true"
                >
                  &gt;_
                </span>
                <Textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                  rows={2}
                  placeholder="명령 또는 질문 입력..."
                  aria-label="채팅 메시지"
                  disabled={isGenerating}
                  className="max-h-40 min-h-[3.5rem] resize-none rounded-none border-0 bg-transparent px-3 py-3 text-base leading-6 shadow-none placeholder:text-muted-foreground/60 focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
                />
                {isGenerating ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={stopGeneration}
                    aria-label="응답 생성 중지"
                    className="m-2 h-9 rounded-none border-rose-400/50 bg-rose-400/[0.06] px-3 text-xs text-rose-200 hover:bg-rose-400/10 hover:text-rose-100"
                  >
                    생성 중지 ^C
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    disabled={!input.trim()}
                    aria-label="메시지 전송"
                    className="m-2 h-9 rounded-none border border-primary bg-primary px-4 text-xs font-semibold text-primary-foreground hover:bg-primary/85"
                  >
                    전송 ↵
                  </Button>
                )}
              </div>
            </form>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                Enter: 전송 · Shift+Enter: 줄바꿈 · 응답은 로컬에서 생성됨
              </span>
              <span>중요한 출력은 직접 확인하세요</span>
            </div>
          </div>
        </footer>
      </section>

      <aside
        className="hidden min-h-0 flex-col overflow-y-auto bg-card lg:flex"
        aria-label="런타임 설정"
      >
        <div className="flex h-11 shrink-0 items-center border-b border-border px-4 text-sm">
          <span className="text-primary">$</span>
          <span className="ml-2 text-foreground">실행 상태</span>
          <span className="ml-auto text-xs text-muted-foreground">10초</span>
        </div>

        <section className="border-b border-border">
          <h2 className="border-b border-border/70 bg-muted/30 px-4 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
            01 / 실행 상태
          </h2>
          <dl className="space-y-2 px-4 py-4 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">API</dt>
              <dd
                className={cn(
                  runtime.gateway === 'online'
                    ? 'text-primary'
                    : runtime.gateway === 'checking'
                      ? 'text-amber-300'
                      : 'text-rose-300',
                )}
              >
                {gatewayLabels[runtime.gateway]}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">추론 엔진</dt>
              <dd
                className={cn(
                  runtime.provider === 'ready'
                    ? 'text-primary'
                    : runtime.provider === 'starting'
                      ? 'text-amber-300'
                      : 'text-rose-300',
                )}
              >
                {providerLabels[runtime.provider]}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">백엔드</dt>
              <dd className="text-foreground">
                {backendLabels[runtime.backend]}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">생성 슬롯</dt>
              <dd className="text-foreground">1</dd>
            </div>
          </dl>
          <p className="border-t border-border/70 bg-background px-4 py-3 text-xs leading-5 text-muted-foreground">
            <span className="text-primary">&gt;</span> {runtime.detail}
          </p>
        </section>

        <section className="border-b border-border">
          <h2 className="border-b border-border/70 bg-muted/30 px-4 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
            02 / 모델 정보
          </h2>
          <dl className="space-y-3 px-4 py-4 text-sm">
            <div>
              <dt className="mb-1 text-muted-foreground">모델 ID</dt>
              <dd className="break-all leading-5 text-foreground">
                {runtime.model}
              </dd>
            </div>
            <div className="grid grid-cols-2 gap-3 border-t border-border/70 pt-3">
              <div>
                <dt className="text-muted-foreground">양자화</dt>
                <dd className="mt-1 text-primary">MLX / 4비트</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">문맥 길이</dt>
                <dd className="mt-1 text-primary">32,768</dd>
              </div>
            </div>
          </dl>
        </section>

        <section className="border-b border-border">
          <h2 className="border-b border-border/70 bg-muted/30 px-4 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
            03 / 생성 설정
          </h2>
          <div className="px-4 py-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm text-foreground">깊이 생각하기</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  복잡한 문제를 더 깊게 추론
                </p>
              </div>
              <Switch
                checked={thinking}
                onCheckedChange={setThinking}
                aria-label="깊이 생각하기"
                className="rounded-[2px] border border-border data-checked:border-primary"
              />
            </div>
            <div className="mt-5 border-t border-border/70 pt-4">
              <div className="mb-3 flex items-center justify-between text-sm">
                <span className="text-foreground">최대 토큰 수</span>
                <span className="text-primary">
                  {maxTokens.toLocaleString()}
                </span>
              </div>
              <div className="grid grid-cols-3 border border-border">
                {[512, 1024, 2048].map((value) => (
                  <Button
                    key={value}
                    type="button"
                    variant="ghost"
                    onClick={() => setMaxTokens(value)}
                    className={cn(
                      'h-9 rounded-none border-r border-border px-1 text-xs last:border-r-0 hover:bg-muted hover:text-primary',
                      maxTokens === value &&
                        'bg-primary/[0.09] text-primary shadow-[inset_0_-2px_0_var(--primary)]',
                    )}
                  >
                    {value === 2048 ? '2K' : value === 1024 ? '1K' : value}
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="mt-auto border-t border-border bg-background">
          <h2 className="border-b border-border/70 px-4 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
            세션 기록
          </h2>
          <div className="space-y-1 px-4 py-4 text-xs leading-5 text-muted-foreground">
            <p>
              <span className="text-primary">[정상]</span> API 경계 분리됨
            </p>
            <p>
              <span className="text-primary">[정상]</span> 로컬 추론 활성화됨
            </p>
            <p>
              <span className="text-amber-300">[!]</span> 대화 기록: 메모리에만
              보관
            </p>
          </div>
        </section>
      </aside>
    </main>
  );
}
