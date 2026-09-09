import type { AuthSessionStore } from '../../auth/state/auth-session.ts';
import type { Conversation } from './chat-store.ts';

type SearchState = {
  query: string;
  items: Conversation[];
  nextCursor: string | null;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
};

const INITIAL_STATE: SearchState = {
  query: '',
  items: [],
  nextCursor: null,
  loading: false,
  loadingMore: false,
  error: null,
};

export class ConversationSearchStore {
  private state = INITIAL_STATE;
  private listeners = new Set<() => void>();
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private revision = 0;
  private active = false;
  private request: AuthSessionStore['request'];
  private workspaceId: string;

  constructor(request: AuthSessionStore['request'], workspaceId: string) {
    this.request = request;
    this.workspaceId = workspaceId;
  }

  getSnapshot = () => this.state;
  getServerSnapshot = () => INITIAL_STATE;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private publish(patch: Partial<SearchState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  private cancel() {
    this.revision += 1;
    this.controller?.abort();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  initialize = async () => {
    this.cancel();
    this.active = true;
    this.publish(INITIAL_STATE);
    await this.search();
  };

  setQuery = (query: string) => {
    if (!this.active) return;
    this.cancel();
    this.publish({
      query: query.slice(0, 200),
      items: [],
      nextCursor: null,
      loading: true,
      loadingMore: false,
      error: null,
    });
    // 한글 조합 중 발생하는 연속 입력을 묶고 이전 응답은 검색어 변경 즉시 무효화한다.
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.search();
    }, 250);
  };

  search = async (more = false) => {
    if (!this.active) return;
    if (!this.workspaceId) {
      this.publish({
        loading: false,
        loadingMore: false,
        error: '대화 목록을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      });
      return;
    }
    if (
      more &&
      (!this.state.nextCursor || this.state.loadingMore || this.state.loading)
    )
      return;
    this.cancel();
    const revision = this.revision;
    const controller = new AbortController();
    this.controller = controller;
    const params = new URLSearchParams({
      workspace_id: this.workspaceId,
      status: 'all',
      limit: '30',
    });
    const query = this.state.query.trim();
    if (query) params.set('q', query);
    if (more && this.state.nextCursor)
      params.set('cursor', this.state.nextCursor);
    this.publish({ loading: !more, loadingMore: more, error: null });
    try {
      const response = await this.request(`/api/v1/conversations?${params}`, {
        signal: controller.signal,
        cache: 'no-store',
      });
      if (!response.ok)
        throw new Error('검색 결과를 불러오지 못했습니다. 다시 시도해 주세요.');
      const result = (await response.json()) as {
        items: Conversation[];
        next_cursor: string | null;
      };
      if (!this.active || revision !== this.revision) return;
      const items = more
        ? [...this.state.items, ...result.items]
        : result.items;
      this.publish({
        items: [...new Map(items.map((item) => [item.id, item])).values()],
        nextCursor: result.next_cursor,
      });
    } catch {
      if (
        this.active &&
        revision === this.revision &&
        !controller.signal.aborted
      )
        this.publish({
          error: '검색 결과를 불러오지 못했습니다. 다시 시도해 주세요.',
        });
    } finally {
      if (this.active && revision === this.revision)
        this.publish({ loading: false, loadingMore: false });
    }
  };

  dispose = () => {
    this.active = false;
    this.cancel();
  };
}
