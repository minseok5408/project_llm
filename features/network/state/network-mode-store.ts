import { responseError } from '../../../lib/http.ts';
import type { RequestFn } from '../../../lib/http.ts';
import type { SearchMetadata } from '../types.ts';

export type GenerationNetworkPolicy = {
  network_mode: 'auto' | 'local';
  web_search: 'auto' | 'on' | 'off';
};
export type NetworkMode = {
  local_only: boolean;
  revision: number;
  mode: 'local' | 'online';
  reason: 'forced_local' | 'provider_unconfigured' | 'offline' | 'available';
  search_configured: boolean;
  checked_at: string | null;
};
export type NetworkModeState = {
  value: NetworkMode | null;
  loading: boolean;
  saving: boolean;
  checking: boolean;
  error: string | null;
};

const INITIAL_STATE: NetworkModeState = {
  value: null,
  loading: true,
  saving: false,
  checking: false,
  error: null,
};

function parseMode(input: unknown): NetworkMode {
  const data = input as Partial<NetworkMode> | null;
  if (
    !data ||
    typeof data.local_only !== 'boolean' ||
    !Number.isSafeInteger(data.revision) ||
    !['local', 'online'].includes(data.mode ?? '') ||
    !['forced_local', 'provider_unconfigured', 'offline', 'available'].includes(
      data.reason ?? '',
    ) ||
    typeof data.search_configured !== 'boolean' ||
    (data.checked_at !== null &&
      (typeof data.checked_at !== 'string' ||
        !Number.isFinite(Date.parse(data.checked_at))))
  )
    throw new Error('연결 설정을 확인할 수 없습니다. 로컬 답변만 사용합니다.');
  return data as NetworkMode;
}

// 인터넷 상태는 브라우저의 onLine 값이 아닌 서버의 검색 서비스 확인 결과를 쓴다.
export class NetworkModeStore {
  private state: NetworkModeState = INITIAL_STATE;
  private listeners = new Set<() => void>();
  private controller = new AbortController();
  private version = 0;
  private request: RequestFn;

  constructor(request: RequestFn) {
    this.request = request;
  }
  getSnapshot = () => this.state;
  getServerSnapshot = () => INITIAL_STATE;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private publish(patch: Partial<NetworkModeState>) {
    if (this.controller.signal.aborted) return;
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
  private begin() {
    this.controller.abort();
    this.controller = new AbortController();
    return { version: ++this.version, signal: this.controller.signal };
  }
  private async read(path: string, signal: AbortSignal, localOnly?: boolean) {
    const method =
      localOnly === undefined
        ? path.endsWith('/check')
          ? 'POST'
          : 'GET'
        : 'PATCH';
    const response = await this.request(path, {
      method,
      signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
      ...(method === 'GET'
        ? {}
        : {
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(
              localOnly === undefined ? {} : { local_only: localOnly },
            ),
          }),
    });
    if (!response.ok)
      throw new Error(
        await responseError(response, '연결 설정 요청에 실패했습니다.'),
      );
    return parseMode(await response.json());
  }
  private fail(error: unknown) {
    this.publish({
      error:
        error instanceof Error ? error.message : '서버 연결을 확인해 주세요.',
    });
  }

  initialize = async () => {
    const operation = this.begin();
    this.publish({ ...INITIAL_STATE, loading: true });
    try {
      const value = await this.read('/api/v1/network-mode', operation.signal);
      if (operation.version !== this.version) return;
      this.publish({ value, loading: false });
    } catch (error) {
      if (operation.version === this.version) this.fail(error);
    } finally {
      if (operation.version === this.version) this.publish({ loading: false });
    }
  };

  check = async () => {
    if (
      this.state.loading ||
      this.state.saving ||
      this.state.checking ||
      this.controller.signal.aborted
    )
      return;
    const operation = this.begin();
    this.publish({ checking: true, error: null });
    try {
      // 데이터 사용을 껐거나 설정을 모르면 외부 검사 없이 저장된 정책만 조회한다.
      const path =
        !this.state.value || this.state.value.local_only
          ? '/api/v1/network-mode'
          : '/api/v1/network-mode/check';
      const value = await this.read(path, operation.signal);
      if (operation.version === this.version) this.publish({ value });
    } catch (error) {
      if (operation.version === this.version) this.fail(error);
    } finally {
      if (operation.version === this.version) this.publish({ checking: false });
    }
  };

  setDataUsage = async (enabled: boolean) => {
    // 설정 쓰기는 동시에 보내지 않아 늦은 응답이 사용자의 최신 선택을 덮지 않는다.
    if (
      this.state.saving ||
      this.state.loading ||
      this.controller.signal.aborted
    )
      return;
    const operation = this.begin();
    this.publish({ saving: true, checking: false, error: null });
    try {
      const value = await this.read(
        '/api/v1/network-mode',
        operation.signal,
        !enabled,
      );
      if (operation.version === this.version) this.publish({ value });
    } catch (error) {
      if (operation.version === this.version) this.fail(error);
    } finally {
      if (operation.version === this.version) this.publish({ saving: false });
    }
  };

  observeSearch = (search?: SearchMetadata | null) => {
    const value = this.state.value;
    if (
      !value ||
      value.local_only ||
      this.state.loading ||
      this.state.saving ||
      this.state.checking ||
      this.state.error ||
      !search
    )
      return;
    // 실제 생성에서 받은 검색 결과만 반영하며 사용자가 저장한 설정과 버전은 보존한다.
    if (['completed', 'no_results', 'omitted'].includes(search.status)) {
      this.publish({
        value: {
          ...value,
          mode: 'online',
          reason: 'available',
          search_configured: true,
        },
      });
    } else if (
      search.status === 'failed' ||
      (search.status === 'unavailable' && search.reason === 'offline')
    ) {
      this.publish({ value: { ...value, mode: 'local', reason: 'offline' } });
    } else if (
      search.status === 'unavailable' &&
      search.reason === 'provider_unconfigured'
    ) {
      this.publish({
        value: {
          ...value,
          mode: 'local',
          reason: 'provider_unconfigured',
          search_configured: false,
        },
      });
    }
  };

  generationPolicy = (): GenerationNetworkPolicy => {
    // 설정이 확인되지 않았거나 변경 중이면 외부로 질문이 전달되지 않게 한다.
    const enabled = Boolean(
      this.state.value &&
      !this.state.value.local_only &&
      !this.state.loading &&
      !this.state.saving &&
      !this.state.checking &&
      !this.state.error &&
      !this.controller.signal.aborted,
    );
    // 데이터 사용 ON은 웹검색을 허용하고, 질문에 검색이 필요한지는 서버에서 판단한다.
    return enabled
      ? { network_mode: 'auto', web_search: 'auto' }
      : { network_mode: 'local', web_search: 'off' };
  };

  dispose = () => {
    ++this.version;
    this.controller.abort();
  };
}
