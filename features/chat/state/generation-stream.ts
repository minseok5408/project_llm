import { ApiError, responseError } from '../../../lib/http.ts';
import type { JsonRequest, RequestFn } from '../../../lib/http.ts';
import {
  consumeGenerationEvents,
  terminalGeneration,
} from '../stream/chat-stream.ts';
import { validContextStatus } from './context-status.ts';
import { reduceGenerationEvent } from './generation-events.ts';
import type {
  GenerationCursor,
  GenerationEventState,
} from './generation-events.ts';
import type { ConversationState, Generation } from './conversation-types.ts';

type StreamState = GenerationEventState & Pick<ConversationState, 'stream'>;
type StreamHost = {
  request: RequestFn;
  json: JsonRequest;
  getState: () => StreamState;
  getSignal: () => AbortSignal;
  publish: (patch: Partial<StreamState>) => void;
  onFinished: (status: string, signal: AbortSignal) => Promise<void>;
  onError: (error: unknown) => void;
};

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

// 화면을 옮기거나 계정이 끝나면 연결을 취소하며 같은 생성의 재연결은 커서를 유지한다.
export class GenerationStream {
  private host: StreamHost;
  private controller: AbortController | null = null;
  private cursor: GenerationCursor = { lastEventId: 0, replayText: '' };

  constructor(host: StreamHost) {
    this.host = host;
  }

  stop = () => {
    this.controller?.abort();
    this.controller = null;
  };

  reset = () => {
    this.stop();
    this.cursor = { lastEventId: 0, replayText: '' };
  };
  async attach(id: string, signal: AbortSignal) {
    const generation = await this.host.json<Generation>(
      `/api/v1/generations/${id}`,
      { signal },
    );
    if (signal.aborted) return;
    this.host.publish({
      context: validContextStatus(generation.context)
        ? generation.context
        : this.host.getState().context,
    });
    if (terminalGeneration(generation.status)) {
      this.host.publish({ generation });
      await this.host.onFinished(generation.status, signal);
      return;
    }
    this.cursor = { lastEventId: 0, replayText: '' };
    this.host.publish({
      generation,
      stream: 'connecting',
      messages: this.host
        .getState()
        .messages.map((message) =>
          message.id === generation.assistant_message_id &&
          !generation.cancel_requested
            ? { ...message, content: '' }
            : message,
        ),
    });
    void this.watch();
  }
  async watch() {
    const generation = this.host.getState().generation;
    if (!generation) return;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const signal = AbortSignal.any([controller.signal, this.host.getSignal()]);
    let failures = 0;
    while (
      !signal.aborted &&
      this.host.getState().generation?.id === generation.id
    ) {
      try {
        this.host.publish({ stream: failures ? 'reconnecting' : 'connecting' });
        const response = await this.host.request(
          `/api/v1/generations/${generation.id}/events?after=${this.cursor.lastEventId}`,
          {
            headers: {
              Accept: 'text/event-stream',
              'Last-Event-ID': String(this.cursor.lastEventId),
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
        this.host.publish({ stream: 'live' });
        await consumeGenerationEvents(response, (event) => {
          if (signal.aborted) return;
          const result = reduceGenerationEvent(
            this.host.getState(),
            this.cursor,
            event,
          );
          if (result) {
            this.cursor = result.cursor;
            if (Object.keys(result.patch).length)
              this.host.publish(result.patch);
          }
        });
        if (signal.aborted) return;
        const current = await this.host.json<Generation>(
          `/api/v1/generations/${generation.id}`,
          { signal },
        );
        if (terminalGeneration(current.status)) {
          if (validContextStatus(current.context))
            this.host.publish({ context: current.context });
          await this.host.onFinished(current.status, signal);
          return;
        }
        this.host.publish({
          generation: { ...this.host.getState().generation, ...current },
        });
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
          this.host.onError(error);
          this.host.publish({ stream: 'paused' });
          return;
        }
        if (failures >= 5) {
          this.host.publish({
            stream: 'paused',
            error:
              '연결이 끊겼습니다. 생성은 서버에서 계속될 수 있으니 다시 연결해 주세요.',
          });
          return;
        }
      }
      this.host.publish({ stream: 'reconnecting' });
      try {
        await pause(Math.min(1000 * 2 ** failures, 10_000), signal);
      } catch {
        return;
      }
    }
  }
}
