import { validFileSources } from '../../files/types.ts';
import { validContextStatus } from './context-status.ts';
import { validProgress } from './interaction-types.ts';
import type { GenerationEvent } from '../stream/chat-stream.ts';
import type { SearchMetadata } from '../../network/types.ts';
import type { ConversationState } from './conversation-types.ts';

export type GenerationEventState = Pick<
  ConversationState,
  | 'generation'
  | 'messages'
  | 'context'
  | 'cancelling'
  | 'lengthLimitedMessageIds'
  | 'error'
>;
export type GenerationCursor = { lastEventId: number; replayText: string };

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

// 원본 상태를 바꾸지 않고 현재 생성에 속한 이벤트만 화면 상태 변경으로 계산한다.
export function reduceGenerationEvent(
  state: GenerationEventState,
  cursor: GenerationCursor,
  event: GenerationEvent,
): { patch: Partial<GenerationEventState>; cursor: GenerationCursor } | null {
  const active = state.generation;
  if (!active || event.id <= cursor.lastEventId) return null;
  let generation = active;
  if (
    typeof event.data.generation_id === 'string' &&
    event.data.generation_id !== generation.id
  )
    return null;

  const patch: Partial<GenerationEventState> = {};
  let messages = state.messages;
  let replayText = cursor.replayText;
  const updateAssistant = (change: Partial<(typeof messages)[number]>) => {
    const assistantId = generation.assistant_message_id;
    messages = messages.map((message) =>
      message.id === assistantId ? { ...message, ...change } : message,
    );
    patch.messages = messages;
  };
  const progress = validProgress(event.data.progress)
    ? event.data.progress
    : undefined;
  if (progress) {
    generation = { ...generation, progress };
    patch.generation = generation;
    updateAssistant({ progress });
  }
  if (event.event === 'meta') {
    if (validFileSources(event.data.file_sources)) {
      const file_sources = event.data.file_sources;
      generation = { ...generation, file_sources };
      updateAssistant({ file_sources });
    }
    if (
      validContextStatus(event.data.context) &&
      event.data.context.generation_id === generation.id
    )
      patch.context = event.data.context;
    const search = validSearchMetadata(event.data.search)
      ? event.data.search
      : undefined;
    if (search) updateAssistant({ search });
    generation = {
      ...generation,
      progress: progress ?? generation.progress,
      status:
        typeof event.data.status === 'string'
          ? event.data.status
          : generation.status,
      cancel_requested:
        generation.cancel_requested || event.data.cancel_requested === true,
      assistant_message_id:
        typeof event.data.assistant_message_id === 'string'
          ? event.data.assistant_message_id
          : generation.assistant_message_id,
      stage:
        event.data.stage === 'compacting' ||
        event.data.stage === 'generating' ||
        event.data.stage === 'searching'
          ? event.data.stage
          : generation.stage,
      context_compacted:
        generation.context_compacted || event.data.context_compacted === true,
      search: search ?? generation.search,
    };
    patch.generation = generation;
  }
  if (
    event.event === 'delta' &&
    typeof event.data.text === 'string' &&
    !state.cancelling &&
    !generation.cancel_requested
  ) {
    replayText += event.data.text;
    updateAssistant({ content: replayText });
  }
  if (event.event === 'error')
    patch.error =
      typeof event.data.message === 'string'
        ? event.data.message
        : '생성에 실패했습니다.';
  if (event.event === 'done' && event.data.finish_reason === 'length') {
    patch.lengthLimitedMessageIds = [
      ...new Set([
        ...state.lengthLimitedMessageIds,
        generation.assistant_message_id,
      ]),
    ];
  }
  return { patch, cursor: { lastEventId: event.id, replayText } };
}
