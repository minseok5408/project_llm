import { validContextStatus } from './context-status.ts';
import type { ContextStatus } from './context-status.ts';
import type { TokenBalance } from '../../usage/types.ts';
import { responseError } from '../../auth/state/auth-session.ts';
import {
  consumeGenerationEvents,
  requestId,
  terminalGeneration,
} from '../stream/chat-stream.ts';
import type { GenerationEvent } from '../stream/chat-stream.ts';
import type { GenerationNetworkPolicy } from '../../network/state/network-mode-store.ts';
import type { SearchMetadata } from '../../network/types.ts';

export type Workspace = { id: string; name: string; role: string };
export type Conversation = {
  id: string;
  workspace_id: string;
  title: string;
  status: 'active' | 'archived';
  is_pinned: boolean;
  model: string;
  last_message_at: string;
  created_at: string;
  active_generation_id?: string | null;
};
export type ChatMessage = {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  status: string;
  sequence: number;
  token_count: number | null;
  created_at: string;
  generation_id?: string | null;
  generation_status?: string | null;
  is_current?: boolean;
  can_regenerate?: boolean;
  finish_reason?: 'stop' | 'length' | null;
  search?: SearchMetadata | null;
};
export type Generation = {
  context?: ContextStatus | null;
  id: string;
  status: string;
  assistant_message_id: string;
  cancel_requested?: boolean;
  stage?: 'compacting' | 'generating' | 'searching';
  context_compacted?: boolean;
  search?: SearchMetadata | null;
  network_mode?: 'auto' | 'local';
  web_search_mode?: 'auto' | 'on' | 'off';
};
type MessagePage = {
  context?: ContextStatus | null;
  items: ChatMessage[];
  next_cursor: number | null;
  active_generation_id?: string | null;
};
type ConversationPage = { items: Conversation[]; next_cursor: string | null };
type AcceptedGeneration = {
  generation_id: string;
  user_message_id: string;
  assistant_message_id: string;
  events_url: string;
};
export type ConversationState = {
  context: ContextStatus | null;
  lastResult: { id: string; status: string } | null;
  workspaces: Workspace[];
  workspaceId: string;
  conversations: Conversation[];
  conversationCursor: string | null;
  filter: 'active' | 'archived';
  selected: Conversation | null;
  messages: ChatMessage[];
  messageCursor: number | null;
  generation: Generation | null;
  lengthLimitedMessageIds: string[];
  usage: TokenBalance | null;
  loading: boolean;
  listLoading: boolean;
  sending: boolean;
  cancelling: boolean;
  stream: 'idle' | 'connecting' | 'live' | 'reconnecting' | 'paused';
  draft: string;
  error: string | null;
};
export type RequestFn = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export const INITIAL_STATE: ConversationState = {
  context: null,
  lastResult: null,
  workspaces: [],
  workspaceId: '',
  conversations: [],
  conversationCursor: null,
  filter: 'active',
  selected: null,
  messages: [],
  messageCursor: null,
  generation: null,
  lengthLimitedMessageIds: [],
  usage: null,
  loading: true,
  listLoading: false,
  sending: false,
  cancelling: false,
  stream: 'idle',
  draft: '',
  error: null,
};

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function mergeMessages(older: ChatMessage[], current: ChatMessage[]) {
  return [
    ...new Map([...older, ...current].map((item) => [item.id, item])).values(),
  ].sort((left, right) => left.sequence - right.sequence);
}

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, ms);
    signal.addEventListener('abort', abort, { once: true });
  });
}

function validSearchMetadata(value: unknown): value is SearchMetadata {
  if (!value || typeof value !== 'object') return false;
  const data = value as Partial<SearchMetadata>;
  return (
    [
      'pending',
      'searching',
      'completed',
      'disabled',
      'unavailable',
      'failed',
      'no_results',
      'cancelled',
      'omitted',
    ].includes(data.status ?? '') &&
    (data.reason === null || typeof data.reason === 'string') &&
    typeof data.provider === 'string' &&
    Array.isArray(data.sources) &&
    data.sources.every(
      (source) =>
        source &&
        Number.isSafeInteger(source.number) &&
        source.number > 0 &&
        typeof source.title === 'string' &&
        typeof source.url === 'string' &&
        typeof source.snippet === 'string' &&
        typeof source.retrieved_at === 'string',
    )
  );
}

// 대화 하나의 초안·메시지·생성 연결을 계정 세션의 메모리에 유지한다.
export class ConversationSession {
  private state: ConversationState = INITIAL_STATE;
  private listeners = new Set<() => void>();
  private request: RequestFn;
  private navigate: (id: string | null) => void;
  private networkPolicy: () => GenerationNetworkPolicy;
  private lifetime = new AbortController();
  private view = new AbortController();
  private streamController: AbortController | null = null;
  private listVersion = 0;
  private messageVersion = 0;
  private lastEventId = 0;
  private replayText = '';
  private pending: {
    conversationId: string;
    path: string;
    body: string;
    key: string;
  } | null = null;

  constructor(
    request: RequestFn,
    navigate: (id: string | null) => void = () => {},
    networkPolicy: () => GenerationNetworkPolicy = () => ({
      network_mode: 'local',
      web_search: 'auto',
    }),
  ) {
    this.request = request;
    this.navigate = navigate;
    this.networkPolicy = networkPolicy;
  }
  seed = (state: Partial<ConversationState>) => this.publish(state);
  getSnapshot = () => this.state;
  getServerSnapshot = () => INITIAL_STATE;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private publish(patch: Partial<ConversationState>) {
    if (this.lifetime.signal.aborted) return;
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
  private fail(error: unknown) {
    if (error instanceof Error && error.name === 'AbortError') return;
    this.publish({
      error:
        error instanceof ApiError
          ? error.message
          : '서버 연결을 확인한 뒤 다시 시도해 주세요.',
    });
  }
  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.request(path, {
      ...init,
      signal: AbortSignal.any([
        this.lifetime.signal,
        ...(init.signal ? [init.signal] : []),
      ]),
    });
    if (!response.ok) {
      throw new ApiError(
        await responseError(
          response,
          `요청에 실패했습니다 (${response.status}).`,
        ),
        response.status,
      );
    }
    return response.status === 204
      ? (undefined as T)
      : (response.json() as Promise<T>);
  }
  private write<T>(
    path: string,
    method: string,
    body: unknown,
    init: RequestInit = {},
  ) {
    return this.json<T>(path, {
      ...init,
      method,
      headers: {
        'Content-Type': 'application/json',
        ...Object.fromEntries(new Headers(init.headers)),
      },
      body: JSON.stringify(body),
    });
  }
  setDraft = (draft: string) => this.publish({ draft });
  clearError = () => this.publish({ error: null });

  initialize = async (id?: string | null) => {
    if (this.lifetime.signal.aborted) {
      this.lifetime = new AbortController();
      this.publish(INITIAL_STATE);
    }
    try {
      const result = await this.json<{ items: Workspace[] }>(
        '/api/v1/workspaces',
      );
      this.publish({
        workspaces: result.items,
        workspaceId: result.items[0]?.id ?? '',
      });
      void this.refreshUsage();
      if (id) await this.openConversation(id, false);
      else {
        this.publish({ loading: false });
        if (this.state.workspaceId) await this.loadConversations();
      }
    } catch (error) {
      this.publish({ loading: false });
      this.fail(error);
    }
  };
  refreshUsage = async () => {
    try {
      this.publish({ usage: await this.json<TokenBalance>('/api/v1/usage') });
    } catch (error) {
      this.fail(error);
    }
  };
  loadConversations = async (more = false) => {
    if (!this.state.workspaceId) return;
    const version = ++this.listVersion;
    const query = new URLSearchParams({
      workspace_id: this.state.workspaceId,
      status: this.state.filter,
      limit: '30',
    });
    if (more && this.state.conversationCursor)
      query.set('cursor', this.state.conversationCursor);
    this.publish({ listLoading: true });
    try {
      const result = await this.json<ConversationPage>(
        `/api/v1/conversations?${query}`,
      );
      if (version !== this.listVersion) return;
      const items = more
        ? [...this.state.conversations, ...result.items]
        : result.items;
      this.publish({
        conversations: [
          ...new Map(items.map((item) => [item.id, item])).values(),
        ],
        conversationCursor: result.next_cursor,
      });
    } catch (error) {
      if (version === this.listVersion) this.fail(error);
    } finally {
      if (version === this.listVersion) this.publish({ listLoading: false });
    }
  };
  setFilter = async (filter: 'active' | 'archived') => {
    this.publish({ filter, conversations: [], conversationCursor: null });
    await this.loadConversations();
  };
  setWorkspace = async (workspaceId: string) => {
    this.newDraft();
    this.publish({ workspaceId, conversations: [], conversationCursor: null });
    await this.loadConversations();
  };
  private stopViewing() {
    this.messageVersion += 1;
    this.view.abort();
    this.view = new AbortController();
    this.streamController?.abort();
    this.streamController = null;
    this.lastEventId = 0;
    this.replayText = '';
  }
  newDraft = () => {
    this.stopViewing();
    this.pending = null;
    this.publish({
      selected: null,
      messages: [],
      messageCursor: null,
      generation: null,
      lengthLimitedMessageIds: [],
      context: null,
      lastResult: null,
      draft: '',
      error: null,
      loading: false,
      sending: false,
      cancelling: false,
      stream: 'idle',
    });
    this.navigate(null);
  };
  openConversation = async (
    id: string,
    navigate = true,
    preserveDraft = false,
  ) => {
    this.stopViewing();
    const view = this.view;
    if (!preserveDraft) this.pending = null;
    this.publish({
      selected: null,
      messages: [],
      messageCursor: null,
      generation: null,
      lengthLimitedMessageIds: [],
      context: null,
      lastResult: null,
      draft: preserveDraft ? this.state.draft : '',
      error: null,
      loading: true,
      sending: false,
      cancelling: false,
      stream: 'idle',
    });
    if (navigate) this.navigate(id);
    try {
      const [conversation, messages] = await Promise.all([
        this.json<Conversation>(`/api/v1/conversations/${id}`, {
          signal: view.signal,
        }),
        this.json<MessagePage>(
          `/api/v1/conversations/${id}/messages?limit=50`,
          { signal: view.signal },
        ),
      ]);
      if (view.signal.aborted) return;
      this.publish({
        selected: conversation,
        context: validContextStatus(messages.context) ? messages.context : null,
        workspaceId: conversation.workspace_id,
        messages: messages.items,
        messageCursor: messages.next_cursor,
        loading: false,
      });
      void this.loadConversations();
      const activeId =
        messages.active_generation_id ?? conversation.active_generation_id;
      if (activeId) await this.attachGeneration(activeId, view.signal);
    } catch (error) {
      if (!view.signal.aborted) {
        this.publish({ loading: false });
        this.fail(error);
      }
    }
  };
  olderMessages = async () => {
    const { selected, messageCursor } = this.state;
    if (!selected || !messageCursor) return;
    const view = this.view;
    try {
      const page = await this.json<MessagePage>(
        `/api/v1/conversations/${selected.id}/messages?limit=50&before=${messageCursor}`,
        { signal: view.signal },
      );
      if (!view.signal.aborted)
        this.publish({
          messages: mergeMessages(page.items, this.state.messages),
          messageCursor: page.next_cursor,
        });
    } catch (error) {
      if (!view.signal.aborted) this.fail(error);
    }
  };
  private async refreshMessages(signal = this.view.signal) {
    const id = this.state.selected?.id;
    if (!id) return;
    const version = ++this.messageVersion;
    const result = await this.json<MessagePage>(
      `/api/v1/conversations/${id}/messages?limit=50`,
      { signal },
    );
    if (version !== this.messageVersion) return;
    if (!signal.aborted && this.state.selected?.id === id) {
      this.publish({
        context: validContextStatus(result.context) ? result.context : null,
        messages: mergeMessages(this.state.messages, result.items),
        messageCursor:
          this.state.messages.length > 50
            ? this.state.messageCursor
            : result.next_cursor,
      });
    }
    return result;
  }
  updateConversation = async (patch: {
    title?: string;
    is_pinned?: boolean;
    status?: 'active' | 'archived';
  }) => {
    if (!this.state.selected) return false;
    const id = this.state.selected.id;
    try {
      const conversation = await this.write<Conversation>(
        `/api/v1/conversations/${id}`,
        'PATCH',
        patch,
      );
      if (this.state.selected?.id === id)
        this.publish({ selected: conversation, error: null });
      await this.loadConversations();
      return true;
    } catch (error) {
      this.fail(error);
      return false;
    }
  };
  deleteConversation = async () => {
    if (!this.state.selected) return;
    const id = this.state.selected.id;
    try {
      await this.write(`/api/v1/conversations/${id}`, 'DELETE', {});
      if (this.state.selected?.id === id) this.newDraft();
      await this.loadConversations();
    } catch (error) {
      this.fail(error);
    }
  };
  send = async (
    content: string,
    options: { thinking: boolean; max_tokens?: number },
    clearDraft = true,
  ) => {
    content = content.trim();
    if (
      !content ||
      this.state.sending ||
      this.state.generation ||
      this.state.loading
    )
      return false;
    if (!this.state.workspaceId || this.state.selected?.status === 'archived')
      return false;
    const view = this.view;
    this.publish({ sending: true, error: null });
    try {
      let conversation = this.state.selected;
      if (!conversation) {
        conversation = await this.write<Conversation>(
          '/api/v1/conversations',
          'POST',
          {
            workspace_id: this.state.workspaceId,
            title: content.slice(0, 60),
          },
          { signal: view.signal },
        );
        if (view.signal.aborted) return false;
        this.publish({ selected: conversation });
        this.navigate(conversation.id);
      }
      const body = JSON.stringify({
        content,
        options,
        ...this.networkPolicy(),
      });
      await this.submitGeneration(
        conversation.id,
        `/api/v1/conversations/${conversation.id}/messages`,
        body,
        view.signal,
        clearDraft,
      );
      return !view.signal.aborted;
    } catch (error) {
      if (!view.signal.aborted) {
        this.fail(error);
        if (this.state.generation) this.publish({ stream: 'paused' });
      }
      return false;
    } finally {
      if (!view.signal.aborted) this.publish({ sending: false });
    }
  };
  continueAnswer = async (
    messageId: string,
    options: { thinking: boolean; max_tokens?: number },
  ) => {
    const message = this.state.messages.find((item) => item.id === messageId);
    if (
      message?.role !== 'assistant' ||
      message.is_current === false ||
      !message.can_regenerate ||
      (message.finish_reason !== 'length' &&
        !this.state.lengthLimitedMessageIds.includes(messageId))
    )
      return false;
    return this.send(
      '직전 답변이 길이 제한으로 끊겼습니다. 앞의 내용을 반복하지 말고 중단된 부분부터 이어서 완성해 주세요.',
      options,
      false,
    );
  };
  regenerate = async (
    messageId: string,
    options: { thinking: boolean; max_tokens?: number },
  ) => {
    const { selected, sending, generation, loading } = this.state;
    const message = this.state.messages.find((item) => item.id === messageId);
    if (
      !selected ||
      selected.status === 'archived' ||
      sending ||
      generation ||
      loading ||
      message?.role !== 'assistant' ||
      !message.can_regenerate ||
      message.is_current === false ||
      !message.generation_id
    )
      return false;
    const view = this.view;
    this.publish({ sending: true, error: null });
    try {
      await this.submitGeneration(
        selected.id,
        `/api/v1/generations/${message.generation_id}/regenerate`,
        JSON.stringify({ options, ...this.networkPolicy() }),
        view.signal,
        false,
      );
      return !view.signal.aborted;
    } catch (error) {
      if (!view.signal.aborted) {
        this.fail(error);
        if (this.state.generation) this.publish({ stream: 'paused' });
      }
      return false;
    } finally {
      if (!view.signal.aborted) this.publish({ sending: false });
    }
  };
  private async submitGeneration(
    conversationId: string,
    path: string,
    body: string,
    signal: AbortSignal,
    clearDraft: boolean,
  ) {
    if (
      this.pending?.conversationId !== conversationId ||
      this.pending.path !== path ||
      this.pending.body !== body
    )
      this.pending = { conversationId, path, body, key: requestId() };
    const accepted = await this.json<AcceptedGeneration>(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': this.pending.key,
      },
      body,
      signal,
    });
    if (signal.aborted) return;
    this.pending = null;
    this.publish({
      ...(clearDraft ? { draft: '' } : {}),
      sending: false,
      generation: {
        id: accepted.generation_id,
        status: 'queued',
        assistant_message_id: accepted.assistant_message_id,
      },
      stream: 'connecting',
    });
    await this.refreshMessages(signal);
    if (!signal.aborted)
      await this.attachGeneration(accepted.generation_id, signal);
    void this.refreshUsage();
    void this.loadConversations();
  }
  private async attachGeneration(id: string, signal: AbortSignal) {
    const generation = await this.json<Generation>(
      `/api/v1/generations/${id}`,
      { signal },
    );
    if (signal.aborted) return;
    this.publish({
      context: validContextStatus(generation.context)
        ? generation.context
        : this.state.context,
    });
    if (terminalGeneration(generation.status)) {
      this.publish({ generation });
      await this.finishGeneration(generation.status, signal);
      return;
    }
    this.lastEventId = 0;
    this.replayText = '';
    this.publish({
      generation,
      stream: 'connecting',
      messages: this.state.messages.map((message) =>
        message.id === generation.assistant_message_id &&
        !generation.cancel_requested
          ? { ...message, content: '' }
          : message,
      ),
    });
    void this.watchGeneration();
  }
  private applyEvent(event: GenerationEvent) {
    if (!this.state.generation || event.id <= this.lastEventId) return;
    if (
      typeof event.data.generation_id === 'string' &&
      event.data.generation_id !== this.state.generation.id
    )
      return;
    this.lastEventId = event.id;
    if (event.event === 'meta')
      this.publish({
        ...(validContextStatus(event.data.context) &&
        event.data.context.generation_id === this.state.generation.id
          ? { context: event.data.context }
          : {}),
        ...(validSearchMetadata(event.data.search)
          ? {
              messages: this.state.messages.map((message) =>
                message.id === this.state.generation?.assistant_message_id
                  ? { ...message, search: event.data.search as SearchMetadata }
                  : message,
              ),
            }
          : {}),
        generation: {
          ...this.state.generation,
          status:
            typeof event.data.status === 'string'
              ? event.data.status
              : this.state.generation.status,
          cancel_requested:
            this.state.generation.cancel_requested ||
            event.data.cancel_requested === true,
          assistant_message_id:
            typeof event.data.assistant_message_id === 'string'
              ? event.data.assistant_message_id
              : this.state.generation.assistant_message_id,
          stage:
            event.data.stage === 'compacting' ||
            event.data.stage === 'generating' ||
            event.data.stage === 'searching'
              ? event.data.stage
              : this.state.generation.stage,
          context_compacted:
            this.state.generation.context_compacted ||
            event.data.context_compacted === true,
          search: validSearchMetadata(event.data.search)
            ? event.data.search
            : this.state.generation.search,
        },
      });
    if (
      event.event === 'delta' &&
      typeof event.data.text === 'string' &&
      !this.state.cancelling &&
      !this.state.generation.cancel_requested
    ) {
      this.replayText += event.data.text;
      const assistantId = this.state.generation.assistant_message_id;
      this.publish({
        messages: this.state.messages.map((message) =>
          message.id === assistantId
            ? { ...message, content: this.replayText }
            : message,
        ),
      });
    }
    if (event.event === 'error')
      this.publish({
        error:
          typeof event.data.message === 'string'
            ? event.data.message
            : '생성에 실패했습니다.',
      });
    if (event.event === 'done' && event.data.finish_reason === 'length') {
      const assistantId = this.state.generation.assistant_message_id;
      this.publish({
        lengthLimitedMessageIds: [
          ...new Set([...this.state.lengthLimitedMessageIds, assistantId]),
        ],
      });
    }
  }
  private async finishGeneration(status: string, signal: AbortSignal) {
    if (signal.aborted) return;
    this.publish({
      context: this.state.context
        ? {
            ...this.state.context,
            phase: 'finished',
            ...(['pending', 'running'].includes(
              this.state.context.compaction_status,
            )
              ? ({
                  compaction_status:
                    status === 'cancelled' ? 'cancelled' : 'failed',
                } as const)
              : {}),
          }
        : null,
      lastResult: this.state.generation
        ? { id: this.state.generation.id, status }
        : this.state.lastResult,
      generation: null,
      stream: 'idle',
      cancelling: false,
      ...(status === 'usage_pending'
        ? {
            error:
              '생성은 종료됐지만 사용량 확인이 필요합니다. 토큰 차감을 보류했으며, 확인 후 다시 질문할 수 있습니다.',
          }
        : {}),
    });
    try {
      await this.refreshMessages(signal);
    } catch (error) {
      if (!signal.aborted) this.fail(error);
    }
    if (signal.aborted) return;
    void this.refreshUsage();
    void this.loadConversations();
  }
  private async watchGeneration() {
    const generation = this.state.generation;
    if (!generation) return;
    this.streamController?.abort();
    const controller = new AbortController();
    this.streamController = controller;
    const signal = AbortSignal.any([
      controller.signal,
      this.view.signal,
      this.lifetime.signal,
    ]);
    let failures = 0;
    while (!signal.aborted && this.state.generation?.id === generation.id) {
      try {
        this.publish({ stream: failures ? 'reconnecting' : 'connecting' });
        const response = await this.request(
          `/api/v1/generations/${generation.id}/events?after=${this.lastEventId}`,
          {
            headers: {
              Accept: 'text/event-stream',
              'Last-Event-ID': String(this.lastEventId),
            },
            signal,
          },
        );
        if (!response.ok)
          throw new ApiError(
            await responseError(response, '생성 상태 연결에 실패했습니다.'),
            response.status,
          );
        if (signal.aborted) return;
        this.publish({ stream: 'live' });
        await consumeGenerationEvents(response, (event) => {
          if (!signal.aborted) this.applyEvent(event);
        });
        if (signal.aborted) return;
        const current = await this.json<Generation>(
          `/api/v1/generations/${generation.id}`,
          { signal },
        );
        if (terminalGeneration(current.status)) {
          if (validContextStatus(current.context))
            this.publish({ context: current.context });
          await this.finishGeneration(current.status, signal);
          return;
        }
        this.publish({ generation: { ...this.state.generation, ...current } });
      } catch (error) {
        if (
          signal.aborted ||
          (error instanceof Error && error.name === 'AbortError')
        )
          return;
        failures += 1;
        if (
          error instanceof ApiError &&
          [401, 403, 404].includes(error.status)
        ) {
          this.fail(error);
          this.publish({ stream: 'paused' });
          return;
        }
        if (failures >= 5) {
          this.publish({
            stream: 'paused',
            error:
              '연결이 끊겼습니다. 생성은 서버에서 계속될 수 있으니 다시 연결해 주세요.',
          });
          return;
        }
      }
      this.publish({ stream: 'reconnecting' });
      try {
        await pause(Math.min(1000 * 2 ** failures, 10_000), signal);
      } catch {
        return;
      }
    }
  }
  reconnect = async () => {
    const view = this.view;
    const generation = this.state.generation;
    if (!generation) return;
    this.publish({ error: null });
    if (
      !this.state.messages.some(
        (message) => message.id === generation.assistant_message_id,
      )
    ) {
      try {
        await this.refreshMessages(view.signal);
        if (view.signal.aborted) return;
        await this.attachGeneration(generation.id, view.signal);
      } catch (error) {
        if (!view.signal.aborted) this.fail(error);
      }
      return;
    }
    void this.watchGeneration();
  };
  cancel = async () => {
    if (!this.state.generation || this.state.cancelling) return;
    const id = this.state.generation.id;
    const view = this.view;
    this.publish({ cancelling: true, error: null });
    try {
      const result = await this.write<Generation>(
        `/api/v1/generations/${id}/cancel`,
        'POST',
        {},
        { signal: AbortSignal.any([view.signal, AbortSignal.timeout(8_000)]) },
      );
      if (view.signal.aborted || this.state.generation?.id !== id) return;
      this.publish({
        cancelling: false,
        generation: {
          ...this.state.generation,
          ...result,
          cancel_requested: true,
        },
      });
      if (terminalGeneration(result.status)) {
        this.streamController?.abort();
        await this.finishGeneration(result.status, view.signal);
        return;
      }
      if (this.state.stream === 'paused') void this.reconnect();
    } catch (error) {
      if (view.signal.aborted || this.state.generation?.id !== id) return;
      this.publish({ cancelling: false });
      this.fail(error);
    }
  };
  refresh = async () => {
    const view = this.view;
    const selectedId = this.state.selected?.id;
    void this.refreshUsage();
    void this.loadConversations();
    if (this.state.selected && !this.state.sending && !this.state.generation) {
      try {
        const page = await this.refreshMessages(view.signal);
        if (
          !view.signal.aborted &&
          this.state.selected?.id === selectedId &&
          page?.active_generation_id
        )
          await this.attachGeneration(page.active_generation_id, view.signal);
      } catch (error) {
        this.fail(error);
      }
    }
  };
  dispose = () => {
    this.lifetime.abort();
    this.stopViewing();
    this.pending = null;
    this.state = INITIAL_STATE;
  };
}
