'use client';

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
    <>
      <div className="mb-3 flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">
          {String(message.sequence).padStart(3, '0')}
        </span>
        <span
          className={`max-w-48 truncate ${isUser ? 'text-sky-300' : 'text-primary'}`}
          title={isUser ? userName : undefined}
        >
          {isUser
            ? userName
            : message.role === 'system'
              ? 'system'
              : 'qwen@mlx'}
        </span>
        <span className="text-muted-foreground">
          {isUser ? ':~/chat$' : ':~/response>'}
        </span>
        {streaming && (
          <span className="ml-auto text-amber-300">
            {cancelling
              ? '[중단 중]'
              : searching
                ? '[검색 중]'
                : compacting
                  ? '[정리 중]'
                  : '[생성 중]'}
          </span>
        )}
        {!streaming && statusLabel && (
          <span className="ml-auto text-xs text-muted-foreground">
            {statusLabel}
          </span>
        )}
      </div>
      <div
        className={[
          'min-w-0 break-words border-l pl-4 text-base leading-7',
          isUser
            ? 'whitespace-pre-wrap border-sky-300/50 text-slate-100'
            : 'border-primary/50 text-foreground',
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
      {message.token_count != null && isAssistant && (
        <p className="mt-3 pl-4 text-[10px] text-muted-foreground">
          출력 {message.token_count.toLocaleString()} 토큰
        </p>
      )}
      {!streaming && isAssistant && lengthLimited && (
        <p className="mt-3 pl-4 text-xs text-amber-200">
          길이 제한에 도달했습니다. “이어서 말해”라고 입력하면 이어서 답합니다.
        </p>
      )}
      {!streaming && (
        <div className="mt-3 flex flex-wrap items-center gap-2 pl-2">
          <CopyButton
            text={message.content}
            label={isUser ? '메시지 복사' : '답변 복사'}
          />
          {isAssistant && !previous && message.can_regenerate && (
            <button
              type="button"
              disabled={!canSend}
              onClick={onRegenerate}
              title="현재 생성 설정으로 같은 질문에 다시 답합니다. 사용한 토큰은 새로 정산됩니다."
              className="min-h-8 px-2 text-xs text-primary hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
            >
              {status === 'completed' ? '다시 생성' : '다시 시도'}
            </button>
          )}
        </div>
      )}
    </>
  );
  return previous ? (
    <details>
      <summary className="cursor-pointer text-xs text-muted-foreground">
        이전 답변{statusLabel ? ` · ${statusLabel}` : ''}
      </summary>
      <div className="mt-4">{body}</div>
    </details>
  ) : (
    body
  );
}
