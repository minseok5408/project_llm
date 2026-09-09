export type AuthSession = {
  user: {
    id: string;
    email: string;
    display_name: string;
    platform_role: 'system' | 'member';
  };
  expires_at: string;
  csrf_token: string;
};

export type AuthState =
  | { status: 'checking' }
  | { status: 'anonymous'; message?: string }
  | { status: 'unavailable'; message: string }
  | {
      status: 'authenticated';
      session: AuthSession;
      busy: boolean;
      message?: string;
    };

const INITIAL_STATE: AuthState = { status: 'checking' };
const UNAVAILABLE_MESSAGE =
  '로그인 상태를 확인할 수 없습니다. 서버 연결을 확인한 뒤 다시 시도해 주세요.';

export async function responseError(response: Response, fallback: string) {
  const data: unknown = await response.json().catch(() => null);
  if (
    data &&
    typeof data === 'object' &&
    'detail' in data &&
    typeof data.detail === 'string'
  ) {
    return data.detail;
  }
  return fallback;
}

function parseSession(value: unknown): AuthSession {
  const session = value as Partial<AuthSession> | null;
  if (
    !session?.user ||
    typeof session.user.id !== 'string' ||
    typeof session.user.email !== 'string' ||
    typeof session.user.display_name !== 'string' ||
    !['system', 'member'].includes(session.user.platform_role) ||
    typeof session.csrf_token !== 'string' ||
    !session.csrf_token ||
    typeof session.expires_at !== 'string' ||
    !Number.isFinite(Date.parse(session.expires_at))
  ) {
    throw new Error(UNAVAILABLE_MESSAGE);
  }
  return session as AuthSession;
}

// 세션과 CSRF 토큰은 메모리에만 두고 브라우저 저장소에는 기록하지 않는다.
export class AuthSessionStore {
  private state: AuthState = INITIAL_STATE;
  private listeners = new Set<() => void>();
  private sessionController = new AbortController();
  private operationController = new AbortController();
  private version = 0;
  private authenticating = false;
  private expiryTimer: ReturnType<typeof setTimeout> | undefined;

  getSnapshot = () => this.state;
  getServerSnapshot = () => INITIAL_STATE;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private publish(state: AuthState) {
    this.state = state;
    for (const listener of this.listeners) listener();
  }

  private abortSession() {
    this.sessionController.abort();
    this.sessionController = new AbortController();
    clearTimeout(this.expiryTimer);
    this.expiryTimer = undefined;
  }

  private nextOperation() {
    this.operationController.abort();
    this.operationController = new AbortController();
    return ++this.version;
  }

  private reset(state: AuthState) {
    this.nextOperation();
    this.abortSession();
    this.publish(state);
  }

  private accept(session: AuthSession) {
    const remaining = Date.parse(session.expires_at) - Date.now();
    if (remaining <= 0) {
      this.reset({ status: 'anonymous', message: '로그인이 만료되었습니다.' });
      return;
    }
    if (
      this.state.status !== 'authenticated' ||
      this.state.session.user.id !== session.user.id ||
      this.state.session.csrf_token !== session.csrf_token ||
      this.state.session.user.platform_role !== session.user.platform_role
    ) {
      this.abortSession();
    }
    clearTimeout(this.expiryTimer);
    // 서버가 정한 절대 만료 시각을 사용하며 활동할 때 유효 시간을 늘리지 않는다.
    this.expiryTimer = setTimeout(
      () => {
        this.reset({
          status: 'anonymous',
          message:
            '로그인 후 24시간이 지나 로그아웃되었습니다. 다시 로그인해 주세요.',
        });
      },
      Math.min(remaining, 2_147_483_647),
    );
    this.publish({ status: 'authenticated', session, busy: false });
  }

  verify = async () => {
    if (this.authenticating) return;
    const version = this.nextOperation();
    if (this.state.status === 'unavailable') this.publish(INITIAL_STATE);
    try {
      const response = await fetch('/api/v1/auth/me', {
        cache: 'no-store',
        credentials: 'same-origin',
        signal: AbortSignal.any([
          this.operationController.signal,
          AbortSignal.timeout(15_000),
        ]),
      });
      if (version !== this.version) return;
      if (response.status === 401) {
        this.reset({ status: 'anonymous' });
        return;
      }
      if (!response.ok) throw new Error(UNAVAILABLE_MESSAGE);
      const session = parseSession(await response.json());
      if (version === this.version) this.accept(session);
    } catch {
      if (version === this.version) {
        this.reset({ status: 'unavailable', message: UNAVAILABLE_MESSAGE });
      }
    }
  };

  authenticate = async (
    mode: 'login' | 'signup',
    payload: { email: string; password: string; display_name?: string },
  ) => {
    const version = this.nextOperation();
    this.authenticating = true;
    try {
      const response = await fetch(`/api/v1/auth/${mode}`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: AbortSignal.any([
          this.operationController.signal,
          AbortSignal.timeout(15_000),
        ]),
      });
      if (version !== this.version) return;
      if (!response.ok) {
        throw new Error(
          await responseError(
            response,
            '로그인 요청에 실패했습니다. 다시 시도해 주세요.',
          ),
        );
      }
      const session = parseSession(await response.json());
      if (version === this.version) this.accept(session);
    } finally {
      this.authenticating = false;
    }
  };

  request = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    if (this.state.status !== 'authenticated') {
      throw new DOMException('Authentication required', 'AbortError');
    }
    if (Date.parse(this.state.session.expires_at) <= Date.now()) {
      this.reset({ status: 'anonymous', message: '로그인이 만료되었습니다.' });
      throw new DOMException('Session expired', 'AbortError');
    }
    const sessionController = this.sessionController;
    const headers = new Headers(init.headers);
    if ((init.method ?? 'GET').toUpperCase() !== 'GET') {
      headers.set('X-CSRF-Token', this.state.session.csrf_token);
    }
    const response = await fetch(input, {
      ...init,
      headers,
      cache: 'no-store',
      credentials: 'same-origin',
      signal: AbortSignal.any([
        this.sessionController.signal,
        ...(init.signal ? [init.signal] : []),
      ]),
    });
    if (
      response.status === 401 &&
      sessionController === this.sessionController
    ) {
      this.reset({ status: 'anonymous', message: '다시 로그인해 주세요.' });
    }
    return response;
  };

  logout = async (all = false) => {
    if (this.state.status !== 'authenticated' || this.state.busy) return;
    this.publish({ ...this.state, busy: true, message: undefined });
    const session = this.state.session;
    const sessionController = this.sessionController;
    try {
      const response = await this.request(
        `/api/v1/auth/${all ? 'logout-all' : 'logout'}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
          signal: AbortSignal.timeout(15_000),
        },
      );
      if (response.status === 401) return;
      if (!response.ok)
        throw new Error('로그아웃에 실패했습니다. 다시 시도해 주세요.');
      if (sessionController === this.sessionController) {
        this.reset({ status: 'anonymous', message: '로그아웃되었습니다.' });
      }
    } catch {
      if (
        this.state.status === 'authenticated' &&
        this.state.session === session
      ) {
        this.publish({
          ...this.state,
          busy: false,
          message:
            '로그아웃에 실패했습니다. 서버 연결을 확인한 뒤 다시 시도해 주세요.',
        });
      }
    }
  };

  dispose = () => {
    this.nextOperation();
    this.abortSession();
  };
}
