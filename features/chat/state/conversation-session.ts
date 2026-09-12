import { ApiError, responseError } from '../../../lib/http.ts';
import type { RequestFn } from '../../../lib/http.ts';
import { ConversationFiles } from '../../files/state/conversation-files.ts';
import { GenerationStream } from './generation-stream.ts';
import { validContextStatus } from './context-status.ts';
import { requestId, terminalGeneration } from '../stream/chat-stream.ts';
import type { GenerationNetworkPolicy } from '../../network/state/network-mode-store.ts';
import type { TokenBalance } from '../../usage/types.ts';
import type {
  Workspace,
  Conversation,
  ChatMessage,
  Generation,
  ConversationState,
  ConversationPage,
  MessagePage,
  AcceptedGeneration,
} from './conversation-types.ts';

export type { RequestFn } from '../../../lib/http.ts';
export type {
  Workspace,
  Conversation,
  ChatMessage,
  Generation,
  ConversationState,
} from './conversation-types.ts';

export const INITIAL_STATE: ConversationState = {
  files: [],
  filesEnabled: true,
  filesLoading: false,
  fileBusy: false,
  fileError: null,
  questionDrafts: {},
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
  newerMessageCursor: null,
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

export function mergeMessages(older: ChatMessage[], current: ChatMessage[]) {
  return [
    ...new Map([...older, ...current].map((item) => [item.id, item])).values(),
  ].sort((left, right) => left.sequence - right.sequence);
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
  private files: ConversationFiles;
  private generationStream: GenerationStream;
  private listVersion = 0;
  private messageVersion = 0;
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
      web_search: 'off',
    }),
  ) {
    this.request = request;
    this.navigate = navigate;
    this.networkPolicy = networkPolicy;
    this.files = new ConversationFiles({
      json: (path, init) => this.json(path, init),
      getContext: () => ({
        conversationId: this.state.selected?.id ?? null,
        busy: this.state.fileBusy,
        canUpload:
          !this.state.sending &&
          !this.state.loading &&
          Boolean(this.state.workspaceId) &&
          this.state.selected?.status !== 'archived',
      }),
      getSignal: () => this.view.signal,
      publish: (patch) => this.publish(patch),
      ensureConversation: (title, signal) =>
        this.ensureConversation(title, signal),
      onUploaded: () => {
        void this.loadConversations();
      },
      onDeleted: (signal) => this.refreshMessages(signal),
    });
    this.generationStream = new GenerationStream({
      request: this.request,
      json: (path, init) => this.json(path, init),
      getState: () => this.state,
      getSignal: () =>
        AbortSignal.any([this.view.signal, this.lifetime.signal]),
      publish: (patch) => this.publish(patch),
      onFinished: (status, signal) => this.finishGeneration(status, signal),
      onError: (error) => this.fail(error),
    });
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
    this.files.reset();
    this.messageVersion += 1;
    this.view.abort();
    this.view = new AbortController();
    this.generationStream.reset();
  }
  newDraft = () => {
    this.stopViewing();
    this.pending = null;
    this.publish({
      selected: null,
      files: [],
      filesLoading: false,
      fileBusy: false,
      fileError: null,
      messages: [],
      messageCursor: null,
      newerMessageCursor: null,
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
      files: [],
      filesLoading: false,
      fileBusy: false,
      fileError: null,
      messages: [],
      messageCursor: null,
      newerMessageCursor: null,
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
        newerMessageCursor: messages.newer_cursor ?? null,
        loading: false,
      });
      void this.loadConversations();
      void this.refreshFiles();
      const activeId =
        messages.active_generation_id ?? conversation.active_generation_id;
      if (activeId) await this.generationStream.attach(activeId, view.signal);
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
    const version = this.messageVersion;
    try {
      const page = await this.json<MessagePage>(
        `/api/v1/conversations/${selected.id}/messages?limit=50&before=${messageCursor}`,
        { signal: view.signal },
      );
      if (!view.signal.aborted && version === this.messageVersion)
        this.publish({
          messages: mergeMessages(page.items, this.state.messages),
          messageCursor: page.next_cursor,
        });
    } catch (error) {
      if (!view.signal.aborted) this.fail(error);
    }
  };
  revealMessage = async (messageId: string) => {
    const version = ++this.messageVersion;
    if (this.state.messages.some((message) => message.id === messageId))
      return true;
    const id = this.state.selected?.id;
    if (!id) return false;
    const view = this.view;
    try {
      const query = new URLSearchParams({ limit: '50', around: messageId });
      const page = await this.json<MessagePage>(
        `/api/v1/conversations/${id}/messages?${query}`,
        { signal: view.signal },
      );
      if (view.signal.aborted || version !== this.messageVersion) return false;
      if (!page.items.some((message) => message.id === messageId))
        throw new ApiError(
          '검색한 메시지를 찾을 수 없습니다. 다시 검색해 주세요.',
          404,
        );
      this.publish({
        messages: page.items,
        messageCursor: page.next_cursor,
        newerMessageCursor: page.newer_cursor ?? null,
        error: null,
      });
      return true;
    } catch (error) {
      if (!view.signal.aborted && version === this.messageVersion)
        this.fail(error);
      return false;
    }
  };
  newerMessages = async () => {
    const { selected, newerMessageCursor } = this.state;
    if (!selected || newerMessageCursor === null) return;
    const view = this.view;
    const version = this.messageVersion;
    try {
      const page = await this.json<MessagePage>(
        `/api/v1/conversations/${selected.id}/messages?limit=50&after=${newerMessageCursor}`,
        { signal: view.signal },
      );
      if (!view.signal.aborted && version === this.messageVersion)
        this.publish({
          messages: mergeMessages(this.state.messages, page.items),
          newerMessageCursor: page.newer_cursor ?? null,
        });
    } catch (error) {
      if (!view.signal.aborted && version === this.messageVersion)
        this.fail(error);
    }
  };
  latestMessages = async () => {
    try {
      return Boolean(await this.refreshMessages(this.view.signal, true));
    } catch (error) {
      this.fail(error);
      return false;
    }
  };
  private async refreshMessages(signal = this.view.signal, latest = false) {
    const id = this.state.selected?.id;
    if (!id) return;
    // 검색으로 연 과거 구간을 최신 페이지와 합쳐 중간 메시지가 빠진 것처럼 보이지 않게 한다.
    if (!latest && this.state.newerMessageCursor !== null) return;
    const version = ++this.messageVersion;
    const result = await this.json<MessagePage>(
      `/api/v1/conversations/${id}/messages?limit=50`,
      { signal },
    );
    if (version !== this.messageVersion) return;
    if (!signal.aborted && this.state.selected?.id === id) {
      this.publish({
        context: validContextStatus(result.context) ? result.context : null,
        messages: latest
          ? result.items
          : mergeMessages(this.state.messages, result.items),
        messageCursor:
          !latest && this.state.messages.length > 50
            ? this.state.messageCursor
            : result.next_cursor,
        newerMessageCursor: result.newer_cursor ?? null,
      });
    }
    return result;
  }
  refreshFiles = () => this.files.refresh();
  uploadFile = (file: File) => this.files.upload(file);
  changeFile = (id: string, action: 'delete' | 'retry') =>
    this.files.change(id, action);

  private async ensureConversation(title: string, signal: AbortSignal) {
    const selected = this.state.selected;
    if (selected) return selected.id;
    const conversation = await this.write<Conversation>(
      '/api/v1/conversations',
      'POST',
      { workspace_id: this.state.workspaceId, title },
      { signal },
    );
    if (signal.aborted) return null;
    this.publish({ selected: conversation });
    this.navigate(conversation.id);
    return conversation.id;
  }
  updateConversation = async (
    patch: {
      title?: string;
      is_pinned?: boolean;
      status?: 'active' | 'archived';
    },
    id = this.state.selected?.id,
  ) => {
    if (!id) return false;
    try {
      const conversation = await this.write<Conversation>(
        `/api/v1/conversations/${id}`,
        'PATCH',
        patch,
      );
      this.publish({
        ...(this.state.selected?.id === id ? { selected: conversation } : {}),
        error: null,
      });
      await this.loadConversations();
      return true;
    } catch (error) {
      this.fail(error);
      return false;
    }
  };
  deleteConversation = async (id = this.state.selected?.id) => {
    if (!id) return false;
    try {
      await this.write(`/api/v1/conversations/${id}`, 'DELETE', {});
      this.publish({ error: null });
      if (this.state.selected?.id === id) this.newDraft();
      await this.loadConversations();
      return true;
    } catch (error) {
      this.fail(error);
      return false;
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
      this.state.fileBusy ||
      this.state.filesLoading ||
      (this.state.filesEnabled &&
        this.state.files.some((file) => file.status !== 'ready')) ||
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
      const conversationId = await this.ensureConversation(
        content.slice(0, 60),
        view.signal,
      );
      if (!conversationId || view.signal.aborted) return false;
      const body = JSON.stringify({
        content,
        options,
        ...this.networkPolicy(),
      });
      await this.submitGeneration(
        conversationId,
        `/api/v1/conversations/${conversationId}/messages`,
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
  setQuestionAnswer = (messageId: string, index: number, value: string) => {
    const message = this.state.messages.find((item) => item.id === messageId);
    if (!message?.can_respond || !message.question_card?.questions[index])
      return;
    const answers = [...(this.state.questionDrafts[messageId] ?? [])];
    answers[index] = value.slice(0, 2000);
    this.publish({
      questionDrafts: { ...this.state.questionDrafts, [messageId]: answers },
    });
  };
  respondToQuestions = async (
    messageId: string,
    options: { thinking: boolean; max_tokens?: number },
  ) => {
    const { selected, sending, generation, loading } = this.state;
    const message = this.state.messages.find((item) => item.id === messageId);
    const answers = (message?.question_card?.questions ?? []).map((_, index) =>
      (this.state.questionDrafts[messageId]?.[index] ?? '').trim(),
    );
    if (
      !selected ||
      selected.status === 'archived' ||
      sending ||
      generation ||
      loading ||
      !message?.can_respond ||
      !message.generation_id ||
      !message.question_card ||
      answers.length !== message.question_card.questions.length ||
      answers.some((answer) => !answer)
    )
      return false;
    const view = this.view;
    this.publish({ sending: true, error: null });
    try {
      await this.submitGeneration(
        selected.id,
        `/api/v1/generations/${message.generation_id}/respond`,
        JSON.stringify({ answers, options, ...this.networkPolicy() }),
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
      '직전 답변이 길이 제한으로 끊겼습니다. 직전 답변의 언어를 그대로 유지하고, 앞의 내용을 반복하지 말고 중단된 부분부터 이어서 완성해 주세요.',
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
    await this.refreshMessages(signal, this.state.newerMessageCursor !== null);
    if (!signal.aborted)
      await this.generationStream.attach(accepted.generation_id, signal);
    void this.refreshUsage();
    void this.loadConversations();
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
        await this.generationStream.attach(generation.id, view.signal);
      } catch (error) {
        if (!view.signal.aborted) this.fail(error);
      }
      return;
    }
    void this.generationStream.watch();
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
        this.generationStream.stop();
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
          await this.generationStream.attach(
            page.active_generation_id,
            view.signal,
          );
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
