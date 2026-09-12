import type { RequestFn } from '../../../lib/http.ts';
import { ConversationSession, INITIAL_STATE } from './conversation-session.ts';
import type {
  ConversationState,
  Conversation,
  Generation,
} from './conversation-session.ts';
import type { GenerationNetworkPolicy } from '../../network/state/network-mode-store.ts';

export type {
  Workspace,
  Conversation,
  ChatMessage,
} from './conversation-session.ts';
export { mergeMessages } from './conversation-session.ts';
export type ChatTask = {
  conversationId: string;
  title: string;
  generation: Generation | null;
  sending: boolean;
  cancelling: boolean;
  stream: ConversationState['stream'];
  error: string | null;
};
export type ChatNotification = {
  id: string;
  conversationId: string;
  title: string;
  status: string;
};
export type ChatState = ConversationState & {
  tasks: ChatTask[];
  notifications: ChatNotification[];
};
const INITIAL_CHAT: ChatState = {
  ...INITIAL_STATE,
  tasks: [],
  notifications: [],
};

// 계정 세션 하나가 여러 대화의 연결을 소유한다. 화면 이동은 연결을 종료하지 않는다.
export class ChatStore {
  private state = INITIAL_CHAT;
  private listeners = new Set<() => void>();
  private sessions = new Set<ConversationSession>();
  private conversations = new Map<string, ConversationSession>();
  private drafts = new Map<string, ConversationSession>();
  private completed = new Set<string>();
  private notifications: ChatNotification[] = [];
  private lifetime = new AbortController();
  private request: RequestFn;
  private navigate: (id: string | null) => void;
  private networkPolicy?: () => GenerationNetworkPolicy;
  private current: ConversationSession;
  private usage = INITIAL_STATE.usage;
  private usageVersion = 0;
  private discovering: Promise<void> | null = null;

  constructor(
    request: RequestFn,
    navigate: (id: string | null) => void = () => {},
    networkPolicy?: () => GenerationNetworkPolicy,
  ) {
    this.request = request;
    this.navigate = navigate;
    this.networkPolicy = networkPolicy;
    this.current = this.createSession();
  }

  getSnapshot = () => this.state;
  getServerSnapshot = () => INITIAL_CHAT;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private createSession(seed: Partial<ConversationState> = {}) {
    const lifetime = this.lifetime;
    const session = new ConversationSession(
      async (url, init = {}) => {
        const usageVersion =
          url === '/api/v1/usage' ? ++this.usageVersion : null;
        const response = await this.request(url, {
          ...init,
          signal: AbortSignal.any([
            lifetime.signal,
            ...(init.signal ? [init.signal] : []),
          ]),
        });
        // 요청 구현이 취소를 무시해도 이전 계정의 응답은 새 상태에 들어오지 않는다.
        lifetime.signal.throwIfAborted();
        if (usageVersion !== null && response.ok) {
          const usage = await response.clone().json();
          if (!lifetime.signal.aborted && usageVersion === this.usageVersion) {
            this.usage = usage;
            this.publish();
          }
        }
        return response;
      },
      (id) => {
        if (this.current === session) this.navigate(id);
      },
      this.networkPolicy,
    );
    session.seed(seed);
    this.sessions.add(session);
    session.subscribe(() => {
      if (lifetime.signal.aborted || !this.sessions.has(session)) return;
      const next = session.getSnapshot();
      if (next.selected) {
        this.conversations.set(next.selected.id, session);
        for (const [key, draft] of this.drafts)
          if (draft === session) this.drafts.delete(key);
      }
      if (next.lastResult && !this.completed.has(next.lastResult.id)) {
        this.completed.add(next.lastResult.id);
        // 현재 보고 있는 대화의 종료는 읽은 상태로 처리한다.
        if (next.selected && this.current !== session) {
          this.notifications = [
            {
              ...next.lastResult,
              conversationId: next.selected.id,
              title: next.selected.title,
            },
            ...this.notifications,
          ].slice(0, 10);
        }
      }
      this.publish();
    });
    return session;
  }

  private seed(): Partial<ConversationState> {
    const {
      workspaces,
      workspaceId,
      conversations,
      conversationCursor,
      filter,
    } = this.state;
    return {
      workspaces,
      workspaceId,
      conversations,
      conversationCursor,
      filter,
      usage: this.usage,
      loading: false,
    };
  }

  private publish() {
    if (this.lifetime.signal.aborted || !this.current) return;
    const tasks: ChatTask[] = [];
    for (const session of this.sessions) {
      const state = session.getSnapshot();
      if (state.generation || state.sending)
        tasks.push({
          conversationId: state.selected?.id ?? '',
          title: state.selected?.title ?? '새 대화',
          generation: state.generation,
          sending: state.sending,
          cancelling: state.cancelling,
          stream: state.stream,
          error: state.error,
        });
    }
    const current = this.current.getSnapshot();
    const conversations = current.conversations.map((conversation) => {
      const tracked = this.conversations.get(conversation.id)?.getSnapshot();
      if (tracked?.generation)
        return { ...conversation, active_generation_id: tracked.generation.id };
      if (tracked?.lastResult)
        return { ...conversation, active_generation_id: null };
      return conversation;
    });
    this.state = {
      ...current,
      conversations,
      usage: this.usage ?? current.usage,
      tasks,
      notifications: this.notifications,
    };
    for (const listener of this.listeners) listener();
  }

  initialize = async (id?: string | null) => {
    if (this.lifetime.signal.aborted) {
      this.lifetime = new AbortController();
      this.current = this.createSession();
    }
    const current = this.current;
    const lifetime = this.lifetime;
    await current.initialize(id);
    if (lifetime.signal.aborted) return;
    if (!id && !current.getSnapshot().selected)
      this.drafts.set(current.getSnapshot().workspaceId, current);
    await this.discoverActive();
  };

  private discoverActive = async () => {
    if (this.discovering || this.lifetime.signal.aborted)
      return this.discovering;
    const lifetime = this.lifetime;
    this.discovering = (async () => {
      try {
        const response = await this.request('/api/v1/generations/active', {
          signal: lifetime.signal,
        });
        if (!response.ok) return;
        const page = (await response.json()) as {
          items: { conversation_id: string }[];
        };
        if (lifetime.signal.aborted) return;
        for (const item of page.items) {
          if (typeof item.conversation_id !== 'string') continue;
          const existing = this.conversations.get(item.conversation_id);
          if (existing) {
            if (!existing.getSnapshot().loading) await existing.refresh();
          } else {
            const session = this.createSession(this.seed());
            this.conversations.set(item.conversation_id, session);
            await session.openConversation(item.conversation_id, false);
          }
          if (lifetime.signal.aborted) return;
        }
      } catch {
        // 복원 실패는 현재 대화를 지우지 않으며 서버가 최종 동시성·권한을 검사한다.
      }
    })();
    try {
      await this.discovering;
    } finally {
      if (lifetime === this.lifetime) this.discovering = null;
    }
  };

  newDraft = () => {
    const workspaceId = this.state.workspaceId;
    let session = this.drafts.get(workspaceId);
    if (!session || session.getSnapshot().selected) {
      session = this.createSession(this.seed());
      this.drafts.set(workspaceId, session);
    }
    this.current = session;
    this.publish();
    this.navigate(null);
  };

  openConversation = async (id: string, navigate = true) => {
    let session = this.conversations.get(id);
    if (!session) {
      session = this.createSession(this.seed());
      this.conversations.set(id, session);
    }
    this.current = session;
    // 메시지 재조회가 끝나기 전, 대화를 선택한 즉시 해당 대화의 점을 지운다.
    this.notifications = this.notifications.filter(
      (item) => item.conversationId !== id,
    );
    this.publish();
    if (navigate) this.navigate(id);
    const state = session.getSnapshot();
    if (state.loading) {
      // 같은 대화의 첫 조회가 진행 중이면 검색 위치 이동도 그 완료를 기다린다.
      const waiting = session;
      const lifetime = this.lifetime.signal;
      await new Promise<void>((resolve) => {
        const finish = () => {
          unsubscribe();
          lifetime.removeEventListener('abort', finish);
          resolve();
        };
        const unsubscribe = waiting.subscribe(() => {
          if (!waiting.getSnapshot().loading) finish();
        });
        lifetime.addEventListener('abort', finish, { once: true });
        if (lifetime.aborted || !waiting.getSnapshot().loading) finish();
      });
      return;
    }
    if (state.generation || state.sending) return;
    await session.openConversation(id, false, true);
  };

  setWorkspace = async (workspaceId: string) => {
    if (workspaceId === this.state.workspaceId) return;
    const session =
      this.drafts.get(workspaceId) ??
      this.createSession({
        ...this.seed(),
        workspaceId,
        conversations: [],
        conversationCursor: null,
      });
    this.drafts.set(workspaceId, session);
    this.current = session;
    this.publish();
    this.navigate(null);
    await session.loadConversations();
  };

  private canSubmit() {
    const other = [...this.sessions].some(
      (session) =>
        session !== this.current &&
        (session.getSnapshot().generation || session.getSnapshot().sending),
    );
    if (other)
      this.current.seed({
        error:
          '다른 대화의 응답이 진행 중입니다. 완료하거나 중단한 뒤 보내 주세요.',
      });
    return !other;
  }
  send: ConversationSession['send'] = (...args) =>
    this.canSubmit() ? this.current.send(...args) : Promise.resolve(false);
  regenerate: ConversationSession['regenerate'] = (...args) =>
    this.canSubmit()
      ? this.current.regenerate(...args)
      : Promise.resolve(false);
  continueAnswer: ConversationSession['continueAnswer'] = (...args) =>
    this.canSubmit()
      ? this.current.continueAnswer(...args)
      : Promise.resolve(false);
  setDraft = (draft: string) => this.current.setDraft(draft);
  setQuestionAnswer: ConversationSession['setQuestionAnswer'] = (...args) =>
    this.current.setQuestionAnswer(...args);
  respondToQuestions: ConversationSession['respondToQuestions'] = (...args) =>
    this.canSubmit()
      ? this.current.respondToQuestions(...args)
      : Promise.resolve(false);
  uploadFile: ConversationSession['uploadFile'] = (...args) =>
    this.current.uploadFile(...args);
  changeFile: ConversationSession['changeFile'] = (...args) =>
    this.current.changeFile(...args);
  refreshFiles = () => this.current.refreshFiles();
  clearError = () => this.current.clearError();
  refreshUsage = () => this.current.refreshUsage();
  loadConversations = (more = false) => this.current.loadConversations(more);
  setFilter = (filter: 'active' | 'archived') => this.current.setFilter(filter);
  olderMessages = () => this.current.olderMessages();
  newerMessages = () => this.current.newerMessages();
  latestMessages = () => this.current.latestMessages();
  revealMessage = (id: string) => this.current.revealMessage(id);
  updateConversation = (
    patch: Partial<Pick<Conversation, 'title' | 'is_pinned' | 'status'>>,
    id = this.state.selected?.id,
  ) => this.updateConversationById(patch, id);
  private async updateConversationById(
    patch: Partial<Pick<Conversation, 'title' | 'is_pinned' | 'status'>>,
    id: string | undefined,
  ) {
    if (!id) return false;
    if (!(await this.current.updateConversation(patch, id))) return false;
    for (const session of this.sessions) {
      const state = session.getSnapshot();
      session.seed({
        ...(state.selected?.id === id
          ? { selected: { ...state.selected, ...patch } }
          : {}),
        conversations: state.conversations
          .map((item) => (item.id === id ? { ...item, ...patch } : item))
          .filter((item) => item.status === state.filter),
      });
    }
    return true;
  }
  cancel = () => this.current.cancel();
  reconnect = () => this.current.reconnect();
  cancelTask = (id: string) => this.conversations.get(id)?.cancel();
  reconnectTask = (id: string) => this.conversations.get(id)?.reconnect();
  dismissNotification = (id: string) => {
    this.notifications = this.notifications.filter((item) => item.id !== id);
    this.publish();
  };
  deleteConversation = async (id = this.state.selected?.id) => {
    if (!id) return false;
    if (!(await this.current.deleteConversation(id))) return false;
    const session = this.conversations.get(id);
    if (
      session &&
      (session.getSnapshot().selected?.id === id ||
        session.getSnapshot().loading)
    )
      session.newDraft();
    if (id) {
      this.conversations.delete(id);
      this.notifications = this.notifications.filter(
        (item) => item.conversationId !== id,
      );
      if (session && this.current === session)
        this.drafts.set(session.getSnapshot().workspaceId, session);
      else if (session) {
        this.sessions.delete(session);
        session.dispose();
      }
      for (const remaining of this.sessions) {
        const state = remaining.getSnapshot();
        remaining.seed({
          conversations: state.conversations.filter((item) => item.id !== id),
        });
      }
      this.publish();
    }
    return true;
  };
  refresh = async () => {
    await this.current.refresh();
    await this.discoverActive();
  };
  dispose = () => {
    this.lifetime.abort();
    for (const session of this.sessions) session.dispose();
    this.sessions.clear();
    this.conversations.clear();
    this.drafts.clear();
    this.completed.clear();
    this.notifications = [];
    this.usage = null;
    this.discovering = null;
    this.state = INITIAL_CHAT;
  };
}
