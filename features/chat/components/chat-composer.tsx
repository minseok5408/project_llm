'use client';

import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { ChatState } from '../state/chat-store.ts';

export function ChatComposer({
  state,
  userName,
  model,
  isGenerating,
  cancelling,
  compacting,
  searching,
  noBalance,
  logoutPending,
  canSend,
  thinking,
  maxTokens,
  onReconnect,
  onCancel,
  onSend,
  onDraftChange,
  onThinkingChange,
  onMaxTokensChange,
}: {
  state: Pick<
    ChatState,
    'stream' | 'selected' | 'sending' | 'draft' | 'generation'
  >;
  userName: string;
  model: string;
  isGenerating: boolean;
  cancelling: boolean;
  compacting: boolean;
  searching: boolean;
  noBalance: boolean;
  logoutPending: boolean;
  canSend: boolean;
  thinking: boolean;
  maxTokens: number;
  onReconnect: () => void;
  onCancel: () => void;
  onSend: () => void;
  onDraftChange: (value: string) => void;
  onThinkingChange: (value: boolean) => void;
  onMaxTokensChange: (value: number) => void;
}) {
  const generation = state.generation;
  return (
    <footer className="shrink-0 border-t border-border bg-card">
      <div className="mx-auto max-w-5xl px-3 py-3 sm:px-6 sm:py-4">
        {isGenerating && (
          <div
            className="mb-3 flex flex-wrap items-center gap-2 text-xs text-amber-200"
            aria-live="polite"
          >
            <span className="flex-1">
              {cancelling
                ? '중단 확인 중 · 다음 질문을 작성할 수 있습니다.'
                : state.stream === 'paused'
                  ? '생성 상태 연결이 끊겼습니다.'
                  : state.stream === 'reconnecting'
                    ? '생성 상태에 다시 연결하는 중...'
                    : generation?.status === 'queued'
                      ? '생성 대기 중...'
                      : compacting
                        ? '이전 대화를 정리 중…'
                        : searching
                          ? '웹에서 참고 자료를 찾는 중…'
                          : '응답 생성 중...'}
            </span>
            {state.stream === 'paused' && (
              <Button
                size="sm"
                variant="outline"
                onClick={onReconnect}
                className="h-7 rounded-none text-xs"
              >
                다시 연결
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={Boolean(cancelling)}
              onClick={onCancel}
              className="h-7 rounded-none border-rose-400/40 text-xs text-rose-200"
            >
              {cancelling ? '중단 중...' : '답변 중단'}
            </Button>
          </div>
        )}
        {noBalance && (
          <p className="mb-3 text-xs leading-5 text-amber-200">
            사용할 수 있는 토큰을 모두 사용했습니다.
          </p>
        )}
        {state.selected?.status === 'archived' && (
          <p className="mb-3 text-xs text-muted-foreground">
            보관된 대화입니다. 대화 목록에서 복원하면 이어갈 수 있습니다.
          </p>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
          className="border border-border bg-background focus-within:border-primary/70"
        >
          <div className="flex items-center justify-between gap-3 border-b border-border/60 px-3 py-2 text-[11px] text-muted-foreground">
            <span className="min-w-0 truncate">
              <span className="text-primary">{userName}</span>:~/chat$ compose
              --stdin
            </span>
            <details className="relative shrink-0">
              <summary className="cursor-pointer text-primary">
                생성 설정
              </summary>
              <div className="absolute bottom-7 right-0 z-10 w-72 border border-border bg-card p-4 shadow-xl">
                <p className="break-all text-xs leading-5">
                  {state.selected?.model ?? model}
                </p>
                <p className="mt-2 text-[10px]">
                  MLX / 4비트 · 문맥 32,768 토큰
                </p>
                <div className="mt-4 flex items-center justify-between">
                  <label htmlFor="thinking">깊이 생각하기</label>
                  <Switch
                    id="thinking"
                    checked={thinking}
                    onCheckedChange={onThinkingChange}
                    disabled={isGenerating || state.sending}
                  />
                </div>
                <p className="mt-4">최대 출력 토큰</p>
                <div className="mt-2 grid grid-cols-3 gap-1">
                  {[512, 1024, 2048].map((value) => (
                    <Button
                      type="button"
                      key={value}
                      size="sm"
                      variant={value === maxTokens ? 'secondary' : 'ghost'}
                      disabled={isGenerating || state.sending}
                      onClick={() => onMaxTokensChange(value)}
                      className="h-8 rounded-none text-xs"
                    >
                      {value}
                    </Button>
                  ))}
                </div>
              </div>
            </details>
          </div>
          <div className="flex items-end">
            <span className="py-3 pl-3 text-primary" aria-hidden="true">
              &gt;_
            </span>
            <Textarea
              value={state.draft}
              onChange={(event) => onDraftChange(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === 'Enter' &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  onSend();
                }
              }}
              rows={2}
              maxLength={100000}
              disabled={
                (isGenerating && !cancelling) || state.sending || logoutPending
              }
              placeholder="명령 또는 질문 입력..."
              aria-label="채팅 메시지"
              className="max-h-40 min-h-[3.5rem] resize-none rounded-none border-0 bg-transparent px-3 py-3 text-base leading-6 shadow-none focus-visible:ring-0 dark:bg-transparent"
            />
            <Button
              type="submit"
              disabled={!state.draft.trim() || !canSend}
              className="m-2 h-9 rounded-none px-4 text-xs"
            >
              {state.sending ? '접수 중...' : '전송 ↵'}
            </Button>
          </div>
        </form>
        <div className="mt-2 flex flex-wrap justify-between gap-2 text-[10px] text-muted-foreground">
          <span>Enter: 전송 · Shift+Enter: 줄바꿈</span>
          <span>답변이 끝나거나 중단된 뒤 확인된 사용량만 차감합니다.</span>
        </div>
      </div>
    </footer>
  );
}
