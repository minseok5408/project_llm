'use client';

import { ChevronDown, RotateCcw } from 'lucide-react';
import type { ChatMessage } from '../state/chat-store.ts';
import { CopyButton } from '../../../components/content/copy-button.tsx';
import { StreamingText } from './streaming-text';
import { SearchSources } from '../../network/components/search-sources.tsx';
import type { SearchMetadata } from '../../network/types.ts';

export function ChatMessageView({
  message,
  userName,
  streaming,
  cancelling,
  compacting,
  searching = false,
  search,
  reducedMotion,
  lengthLimited,
  canSend,
  onRegenerate,
}: {
  message: ChatMessage;
  userName: string;
  streaming: boolean;
  cancelling: boolean;
  compacting: boolean;
  searching?: boolean;
  search?: SearchMetadata | null;
  reducedMotion: boolean;
  lengthLimited: boolean;
  canSend: boolean;
  onRegenerate: () => void;
}) {
  const isUser = message.role === 'user';
  const isAssistant = message.role === 'assistant';
  const previous = isAssistant && message.is_current === false;
  const status = message.generation_status ?? message.status;
  const statusLabel =
    status === 'cancelled'
      ? '중단됨'
      : status === 'failed'
        ? '생성 실패'
        : ['pending', 'usage_pending'].includes(status)
          ? '확인 대기'
          : '';
  const body = (
    <div
      className={`group/message min-w-0 ${isUser ? 'flex flex-col items-end' : 'w-full'}`}
    >
      <span className="sr-only">
        {isUser
          ? userName
          : message.role === 'system'
            ? '시스템 메시지'
            : 'AI 답변'}
      </span>
      {streaming && (
        <p
          className="mb-3 flex items-center gap-2 text-xs leading-5 text-muted-foreground before:size-1.5 before:shrink-0 before:rounded-full before:bg-current"
          aria-live="polite"
        >
          {cancelling
            ? '중단 중'
            : searching
              ? '검색 중'
              : compacting
                ? '대화 정리 중'
                : '답변 생성 중'}
        </p>
      )}
      {!streaming && statusLabel && (
        <p className="mb-3 w-fit rounded-full bg-muted/80 px-2.5 py-1 text-xs text-muted-foreground">
          {statusLabel}
        </p>
      )}
      <div
        className={[
          'min-w-0 break-words text-[15px] leading-7 sm:text-base',
          isUser
            ? 'max-w-[90%] whitespace-pre-wrap rounded-[24px] bg-muted px-5 py-3 text-foreground sm:max-w-[82%]'
            : 'w-full text-foreground',
          streaming && !cancelling && !reducedMotion ? 'streaming-caret' : '',
        ].join(' ')}
      >
        <StreamingText
          content={message.content}
          active={streaming}
          frozen={streaming && cancelling}
          reducedMotion={reducedMotion}
          markdown={!isUser}
          fallback={
            streaming
              ? cancelling
                ? '응답 생성을 중단하고 있습니다.'
                : searching
                  ? '웹에서 참고 자료를 찾는 중…'
                  : compacting
                    ? '이전 대화를 정리 중…'
                    : '응답을 준비하는 중...'
              : '저장된 응답이 없습니다.'
          }
        />
      </div>
      {isAssistant && <SearchSources search={search ?? message.search} />}
      {!streaming && isAssistant && lengthLimited && (
        <p className="mt-4 rounded-2xl bg-muted/70 px-4 py-3 text-xs leading-5 text-muted-foreground">
          길이 제한에 도달했습니다. “이어서 말해”라고 입력하면 이어서 답합니다.
        </p>
      )}
      {!streaming && (
        <div
          className={`mt-2 flex min-h-8 flex-wrap items-center gap-0.5 ${isUser ? '-mr-1 justify-end' : '-ml-2'}`}
        >
          <CopyButton
            text={message.content}
            label={isUser ? '메시지 복사' : '답변 복사'}
          />
          {isAssistant && !previous && message.can_regenerate && (
            <button
              type="button"
              disabled={!canSend}
              onClick={onRegenerate}
              aria-label={status === 'completed' ? '다시 생성' : '다시 시도'}
              title="현재 생성 설정으로 같은 질문에 다시 답합니다. 사용한 토큰은 새로 정산됩니다."
              className="inline-flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-40"
            >
              <RotateCcw
                className="size-4"
                strokeWidth={1.7}
                aria-hidden="true"
              />
            </button>
          )}
        </div>
      )}
    </div>
  );
  return previous ? (
    <details className="group/version rounded-2xl border border-border/60 bg-muted/20 px-4 py-3 transition-colors open:bg-transparent">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <ChevronDown
          className="size-3.5 transition-transform group-open/version:rotate-180"
          aria-hidden="true"
        />
        이전 답변{statusLabel ? ` · ${statusLabel}` : ''}
      </summary>
      <div className="mt-4">{body}</div>
    </details>
  ) : (
    body
  );
}
