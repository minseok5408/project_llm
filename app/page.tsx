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
          title: 'Send chat message',
          description:
            'Send one text message through the visible Qwen Workbench conversation and wait for the streamed response.',
          inputSchema: {
            type: 'object',
            properties: {
              message: {
                type: 'string',
                minLength: 1,
                maxLength: 100000,
                description: 'The user message to send.',
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
              throw new Error('message must be a non-empty string');
            }
            if (message.length > 100000) {
              throw new Error('message must be 100000 characters or fewer');
            }

            const accepted = await sendMessageRef.current(message);
            if (!accepted)
              throw new Error('the chat is busy or the message is empty');
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
            <div className="mr-3 flex items-center gap-1.5" aria-hidden="true">
              <span className="size-2.5 border border-rose-400/70 bg-rose-400/30" />
              <span className="size-2.5 border border-amber-300/70 bg-amber-300/30" />
              <span className="size-2.5 border border-primary/70 bg-primary/30" />
            </div>
            <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
              <span className="text-primary">$</span> qwen-workbench
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
              :clear
            </Button>
          </div>
          <div
            className="flex h-8 items-center gap-4 overflow-x-auto whitespace-nowrap px-3 text-xs text-muted-foreground sm:px-4"
            aria-live="polite"
          >
            <span className="flex items-center gap-2">
              <ConnectionDot status={runtime.provider} />
              provider://{runtime.backend}
            </span>
            <span>
              gateway=
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
                {runtime.gateway}
              </strong>
            </span>
            <span>context=32768</span>
            <span>slot=1/1</span>
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
                      <span className="text-primary">qwen-workbench</span>{' '}
                      <span className="text-foreground">0.1.0</span>
                    </p>
                    <p>local inference terminal / Apple Silicon arm64</p>
                    <p className="mt-4">
                      <span className="text-primary">[ ok ]</span> FastAPI
                      gateway handshake
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
                          ? '[ ok ]'
                          : runtime.provider === 'offline'
                            ? '[fail]'
                            : '[wait]'}
                      </span>{' '}
                      {runtime.detail}
                    </p>
                    <p>
                      <span className="text-primary">[ ok ]</span> model context
                      allocated: 32768 tokens
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
                            [streaming]
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
                <span className="mr-2 text-rose-400">stderr:</span>
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
                    SIGINT ^C
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    disabled={!input.trim()}
                    aria-label="메시지 전송"
                    className="m-2 h-9 rounded-none border border-primary bg-primary px-4 text-xs font-semibold text-primary-foreground hover:bg-primary/85"
                  >
                    EXEC ↵
                  </Button>
                )}
              </div>
            </form>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                Enter: run · Shift+Enter: newline · 응답은 로컬에서 생성됨
              </span>
              <span>verify critical output manually</span>
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
          <span className="ml-2 text-foreground">watch runtime</span>
          <span className="ml-auto text-xs text-muted-foreground">10s</span>
        </div>

        <section className="border-b border-border">
          <h2 className="border-b border-border/70 bg-muted/30 px-4 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
            01 / runtime.status
          </h2>
          <dl className="space-y-2 px-4 py-4 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">gateway</dt>
              <dd
                className={cn(
                  runtime.gateway === 'online'
                    ? 'text-primary'
                    : runtime.gateway === 'checking'
                      ? 'text-amber-300'
                      : 'text-rose-300',
                )}
              >
                {runtime.gateway}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">provider</dt>
              <dd
                className={cn(
                  runtime.provider === 'ready'
                    ? 'text-primary'
                    : runtime.provider === 'starting'
                      ? 'text-amber-300'
                      : 'text-rose-300',
                )}
              >
                {runtime.provider}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">backend</dt>
              <dd className="text-foreground">{runtime.backend}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">generation.slot</dt>
              <dd className="text-foreground">1</dd>
            </div>
          </dl>
          <p className="border-t border-border/70 bg-background px-4 py-3 text-xs leading-5 text-muted-foreground">
            <span className="text-primary">&gt;</span> {runtime.detail}
          </p>
        </section>

        <section className="border-b border-border">
          <h2 className="border-b border-border/70 bg-muted/30 px-4 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
            02 / model.info
          </h2>
          <dl className="space-y-3 px-4 py-4 text-sm">
            <div>
              <dt className="mb-1 text-muted-foreground">model_id</dt>
              <dd className="break-all leading-5 text-foreground">
                {runtime.model}
              </dd>
            </div>
            <div className="grid grid-cols-2 gap-3 border-t border-border/70 pt-3">
              <div>
                <dt className="text-muted-foreground">quant</dt>
                <dd className="mt-1 text-primary">mlx / 4-bit</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">context</dt>
                <dd className="mt-1 text-primary">32,768</dd>
              </div>
            </div>
          </dl>
        </section>

        <section className="border-b border-border">
          <h2 className="border-b border-border/70 bg-muted/30 px-4 py-2 text-xs uppercase tracking-[0.12em] text-muted-foreground">
            03 / generation.conf
          </h2>
          <div className="px-4 py-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm text-foreground">thinking</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  extended reasoning
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
                <span className="text-foreground">max_tokens</span>
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
            session.log
          </h2>
          <div className="space-y-1 px-4 py-4 text-xs leading-5 text-muted-foreground">
            <p>
              <span className="text-primary">[ok]</span> api boundary isolated
            </p>
            <p>
              <span className="text-primary">[ok]</span> local inference enabled
            </p>
            <p>
              <span className="text-amber-300">[!]</span> history: memory only
            </p>
          </div>
        </section>
      </aside>
    </main>
  );
}
