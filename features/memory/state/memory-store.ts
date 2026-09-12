import { responseError } from '../../../lib/http.ts';
import type { RequestFn } from '../../../lib/http.ts';

export type Memory = {
  id: string;
  key: string;
  content: string;
  updated_at: string;
};
export type MemorySnapshot = {
  revision: number;
  limit: number;
  items: Memory[];
};
export type MemoryState = {
  value: MemorySnapshot | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
};
export type MemoryRequest = RequestFn;
const initial = (): MemoryState => ({
  value: null,
  loading: true,
  saving: false,
  error: null,
});
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

export function parseMemories(input: unknown): MemorySnapshot {
  const data = input as MemorySnapshot | null;
  if (
    !data ||
    !Number.isSafeInteger(data.revision) ||
    data.revision < 0 ||
    data.limit !== 20 ||
    !Array.isArray(data.items) ||
    data.items.length > data.limit ||
    data.items.some(
      (item) =>
        !item ||
        typeof item.id !== 'string' ||
        !uuid.test(item.id) ||
        typeof item.key !== 'string' ||
        !item.key.trim() ||
        item.key.length > 80 ||
        typeof item.content !== 'string' ||
        !item.content.trim() ||
        item.content.length > 500 ||
        typeof item.updated_at !== 'string' ||
        !Number.isFinite(Date.parse(item.updated_at)),
    ) ||
    new Set(data.items.map((item) => item.id)).size !== data.items.length
  )
    throw new Error('저장된 기억을 확인할 수 없습니다. 다시 불러와 주세요.');
  return data;
}

// 서버 응답만 보관하며 계정·화면 전환 뒤 도착한 응답은 버린다.
export class MemoryStore {
  private state = initial();
  private listeners = new Set<() => void>();
  private version = 0;
  private controller = new AbortController();
  private request: MemoryRequest;
  constructor(request: MemoryRequest) {
    this.request = request;
  }
  getSnapshot = () => this.state;
  getServerSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<MemoryState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
  dispose = () => {
    this.version++;
    this.controller.abort();
    this.state = initial();
  };
  load = async () => {
    if (this.state.saving) return;
    this.controller.abort();
    this.controller = new AbortController();
    const version = ++this.version;
    this.publish({ loading: true, error: null });
    try {
      const response = await this.request('/api/v1/memories', {
        signal: this.controller.signal,
      });
      if (!response.ok)
        throw new Error(
          await responseError(response, '기억 요청을 완료하지 못했습니다.'),
        );
      const value = parseMemories(await response.json());
      if (version === this.version) this.publish({ value, loading: false });
    } catch (error) {
      if (version === this.version)
        this.publish({
          value: null,
          loading: false,
          error:
            error instanceof Error
              ? error.message
              : '기억을 불러오지 못했습니다.',
        });
    }
  };
  save = (key: string, content: string, id?: string) =>
    this.change(id ? 'PATCH' : 'POST', id, {
      key: key.trim(),
      content: content.trim(),
    });
  remove = (id: string) => this.change('DELETE', id, {});
  private async change(
    method: string,
    id: string | undefined,
    fields: Record<string, string>,
  ): Promise<boolean> {
    if (
      !this.state.value ||
      this.state.loading ||
      this.state.saving ||
      (id && !uuid.test(id))
    )
      return false;
    const version = ++this.version;
    const revision = this.state.value.revision;
    this.publish({ saving: true, error: null });
    try {
      const response = await this.request(
        `/api/v1/memories${id ? `/${id}` : ''}`,
        {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...fields, revision }),
          signal: this.controller.signal,
        },
      );
      if (!response.ok)
        throw new Error(
          response.status === 409
            ? '다른 변경이 있거나 같은 이름의 기억이 있습니다. 새로고침 후 확인해 주세요.'
            : await responseError(response, '기억 요청을 완료하지 못했습니다.'),
        );
      const value = parseMemories(await response.json());
      if (version !== this.version) return false;
      this.publish({ value, saving: false });
      return true;
    } catch (error) {
      if (version === this.version)
        this.publish({
          saving: false,
          error:
            error instanceof Error
              ? error.message
              : '기억을 저장하지 못했습니다.',
        });
      return false;
    }
  }
}
