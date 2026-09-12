import { ApiError } from '../../../lib/http.ts';
import type { JsonRequest } from '../../../lib/http.ts';
import type { ConversationFileState, FilePage } from '../types.ts';

type FileContext = {
  conversationId: string | null;
  busy: boolean;
  canUpload: boolean;
};
type FileHost = {
  json: JsonRequest;
  getContext: () => FileContext;
  getSignal: () => AbortSignal;
  publish: (patch: Partial<ConversationFileState>) => void;
  ensureConversation: (
    title: string,
    signal: AbortSignal,
  ) => Promise<string | null>;
  onUploaded: () => void;
  onDeleted: (signal: AbortSignal) => Promise<unknown>;
};

// 파일 처리의 갱신 순서·폴링만 소유하고 대화 생성과 화면 상태 반영은 호출자에게 맡긴다.
export class ConversationFiles {
  private host: FileHost;
  private version = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(host: FileHost) {
    this.host = host;
  }

  reset = () => {
    this.version += 1;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  };
  refresh = async () => {
    const id = this.host.getContext().conversationId;
    if (!id) return;
    const signal = this.host.getSignal();
    const version = ++this.version;
    if (this.timer) clearTimeout(this.timer);
    this.host.publish({ filesLoading: true });
    try {
      const result = await this.host.json<FilePage>(
        `/api/v1/conversations/${id}/files`,
        { signal },
      );
      if (signal.aborted || version !== this.version) return;
      this.host.publish({
        files: result.items,
        filesEnabled: result.enabled,
        fileError: null,
      });
      if (
        result.items.some((file) => !['ready', 'failed'].includes(file.status))
      )
        this.timer = setTimeout(() => void this.refresh(), 1500);
    } catch (error) {
      if (!signal.aborted && version === this.version)
        this.host.publish({
          fileError:
            error instanceof ApiError
              ? error.message
              : '첨부 파일 상태를 불러오지 못했습니다.',
        });
    } finally {
      if (!signal.aborted && version === this.version)
        this.host.publish({ filesLoading: false });
    }
  };
  upload = async (file: File) => {
    const context = this.host.getContext();
    if (context.busy || !context.canUpload) return false;
    if (
      !file.size ||
      file.size > 10 * 1024 * 1024 ||
      !/\.(pdf|txt|md|csv|json)$/i.test(file.name)
    ) {
      this.host.publish({
        fileError: '10MB 이하 PDF·TXT·Markdown·CSV·JSON 파일을 선택해 주세요.',
      });
      return false;
    }
    const signal = this.host.getSignal();
    this.host.publish({ fileBusy: true, fileError: null });
    try {
      const conversationId = await this.host.ensureConversation(
        file.name.slice(0, 60),
        signal,
      );
      if (!conversationId || signal.aborted) return false;
      await this.host.json(`/api/v1/conversations/${conversationId}/files`, {
        method: 'POST',
        body: file,
        signal,
        headers: {
          'Content-Type': file.type || 'application/octet-stream',
          'X-File-Name': encodeURIComponent(file.name),
        },
      });
      if (signal.aborted) return false;
      await this.refresh();
      this.host.onUploaded();
      return !signal.aborted;
    } catch (error) {
      if (!signal.aborted)
        this.host.publish({
          fileError:
            error instanceof ApiError
              ? error.message
              : '파일을 첨부하지 못했습니다. 다시 시도해 주세요.',
        });
      return false;
    } finally {
      if (!signal.aborted) this.host.publish({ fileBusy: false });
    }
  };
  change = async (id: string, action: 'delete' | 'retry') => {
    const { conversationId, busy } = this.host.getContext();
    if (!conversationId || busy) return false;
    const signal = this.host.getSignal();
    this.host.publish({ fileBusy: true, fileError: null });
    try {
      await this.host.json(
        `/api/v1/conversations/${conversationId}/files/${id}${action === 'retry' ? '/retry' : ''}`,
        {
          method: action === 'retry' ? 'POST' : 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
          signal,
        },
      );
      if (signal.aborted) return false;
      await this.refresh();
      if (action === 'delete') await this.host.onDeleted(signal);
      return !signal.aborted;
    } catch (error) {
      if (!signal.aborted)
        this.host.publish({
          fileError:
            error instanceof ApiError
              ? error.message
              : '파일 요청을 완료하지 못했습니다.',
        });
      return false;
    } finally {
      if (!signal.aborted) this.host.publish({ fileBusy: false });
    }
  };
}
