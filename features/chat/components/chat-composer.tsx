'use client';

import { FilePicker } from '../../files/components/file-picker.tsx';

import {
  ArrowUp,
  Brain,
  ChevronDown,
  LoaderCircle,
  RotateCcw,
  SlidersHorizontal,
  Square,
} from 'lucide-react';
import { FileAttachments } from '../../files/components/file-attachments.tsx';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { ChatState } from '../state/chat-store.ts';
import {
  ComposerExpansion,
  ComposerExpansionToggle,
} from './composer-expansion.tsx';

export function ChatComposer({
  state,
  onUpload,
  onDeleteFile,
  onRetryFile,
  onRefreshFiles,
  userName,
  isGenerating,
  cancelling,
  compacting,
  searching,
  noBalance,
  logoutPending,
  canSend,
  thinking,
  onReconnect,
  onCancel,
  onSend,
  onDraftChange,
  onThinkingChange,
}: {
  state: Pick<
    ChatState,
    | 'stream'
    | 'selected'
    | 'sending'
    | 'draft'
    | 'generation'
    | 'files'
    | 'filesEnabled'
    | 'fileBusy'
    | 'fileError'
    | 'loading'
    | 'workspaceId'
  >;
  onUpload?: (file: File) => void;
  onDeleteFile?: (id: string) => void;
  onRetryFile?: (id: string) => void;
  onRefreshFiles?: () => void;
  userName: string;
  isGenerating: boolean;
  cancelling: boolean;
  compacting: boolean;
  searching: boolean;
  noBalance: boolean;
  logoutPending: boolean;
  canSend: boolean;
  thinking: boolean;
  onReconnect: () => void;
  onCancel: () => void;
  onSend: () => void;
  onDraftChange: (value: string) => void;
  onThinkingChange: (value: boolean) => void;
}) {
  const generation = state.generation;
  return (
    <footer className="relative z-10 shrink-0 bg-background pb-[env(safe-area-inset-bottom)]">
      <div className="mx-auto w-full max-w-3xl px-4 pb-3 pt-3 sm:px-6 sm:pb-4">
        {isGenerating && (
          <div
            className="mb-2 flex min-h-7 flex-wrap items-center gap-2 px-3 text-xs text-muted-foreground"
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
                type="button"
                size="sm"
                variant="ghost"
                onClick={onReconnect}
                className="h-7 gap-1.5 rounded-full px-2.5 text-xs"
              >
                <RotateCcw className="size-3.5" aria-hidden="true" />
                다시 연결
              </Button>
            )}
          </div>
        )}
        {noBalance && (
          <p className="mb-3 rounded-2xl bg-muted px-4 py-3 text-xs leading-5 text-muted-foreground">
            사용할 수 있는 토큰을 모두 사용했습니다.
          </p>
        )}
        {state.selected?.status === 'archived' && (
          <p className="mb-3 px-1 text-xs text-muted-foreground">
            보관된 대화입니다. 대화 목록에서 복원하면 이어갈 수 있습니다.
          </p>
        )}
        <ComposerExpansion
          key={`${state.workspaceId ?? ''}:${state.selected?.id ?? 'new'}`}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (canSend) onSend();
            }}
            className="rounded-[28px] border border-border/80 bg-card p-2 shadow-[0_2px_12px_rgb(0_0_0/0.04)] transition-[border-color,box-shadow] focus-within:border-foreground/20 focus-within:shadow-[0_3px_18px_rgb(0_0_0/0.06)] sm:p-2.5 dark:border-border/40 dark:bg-muted/80 dark:shadow-none"
          >
            <FileAttachments
              files={state.files ?? []}
              busy={state.fileBusy ?? false}
              error={state.fileError ?? null}
              onDelete={onDeleteFile ?? (() => {})}
              onRetry={onRetryFile ?? (() => {})}
            />
            {state.fileError && (
              <button
                type="button"
                className="px-3 py-1 text-xs underline"
                onClick={onRefreshFiles}
              >
                첨부 상태 다시 확인
              </button>
            )}
            <label htmlFor="chat-message-input" className="sr-only">
              {userName}님의 메시지
            </label>
            <Textarea
              id="chat-message-input"
              value={state.draft}
              onChange={(event) => onDraftChange(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === 'Enter' &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing &&
                  canSend
                ) {
                  event.preventDefault();
                  onSend();
                }
              }}
              rows={2}
              maxLength={100000}
              disabled={state.sending || logoutPending}
              placeholder="무엇이든 물어보세요"
              aria-label="채팅 메시지"
              className="max-h-[min(12rem,25dvh)] min-h-16 resize-none rounded-none border-0 bg-transparent px-3.5 py-3 text-base leading-6 shadow-none placeholder:text-muted-foreground/85 focus-visible:ring-0 disabled:bg-transparent disabled:opacity-70 group-data-[expanded=true]/composer:h-[min(50dvh,30rem)] group-data-[expanded=true]/composer:max-h-[min(50dvh,30rem)] group-data-[expanded=true]/composer:field-sizing-fixed md:text-base dark:bg-transparent"
            />
            <div className="flex items-center gap-2 px-1 pb-0.5 pt-1">
              {state.filesEnabled !== false && onUpload && (
                <FilePicker
                  onUpload={onUpload}
                  disabled={Boolean(
                    state.fileBusy ||
                    state.sending ||
                    state.loading ||
                    logoutPending ||
                    state.selected?.status === 'archived',
                  )}
                />
              )}
              <details
                className="group/settings relative min-w-0"
                data-chat-menu
              >
                <summary className="flex min-h-9 w-fit cursor-pointer list-none items-center gap-2 rounded-full px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring group-open/settings:bg-accent group-open/settings:text-foreground [&::-webkit-details-marker]:hidden">
                  <SlidersHorizontal
                    className="size-4"
                    strokeWidth={1.7}
                    aria-hidden="true"
                  />
                  <span>생성 설정</span>
                  <ChevronDown
                    className="size-3.5 transition-transform group-open/settings:rotate-180"
                    aria-hidden="true"
                  />
                </summary>
                <div className="absolute bottom-12 left-0 z-20 max-h-[45dvh] w-80 max-w-[calc(100vw-3.5rem)] overflow-y-auto rounded-2xl border border-border/80 bg-popover p-4 text-sm shadow-[0_8px_32px_rgb(0_0_0/0.12)]">
                  <div className="border-b border-border/70 pb-3">
                    <p className="font-medium text-popover-foreground">
                      답변 설정
                    </p>
                  </div>
                  <div className="mt-4 flex items-center justify-between gap-3">
                    <label
                      htmlFor="thinking"
                      className="flex items-center gap-2"
                    >
                      <Brain
                        className="size-4 text-muted-foreground"
                        strokeWidth={1.7}
                        aria-hidden="true"
                      />
                      깊이 생각하기
                    </label>
                    <Switch
                      id="thinking"
                      checked={thinking}
                      onCheckedChange={onThinkingChange}
                      disabled={isGenerating || state.sending}
                    />
                  </div>
                </div>
              </details>
              <ComposerExpansionToggle />
              {thinking && (
                <span className="mr-auto hidden items-center gap-1.5 rounded-full px-2 py-1.5 text-xs text-muted-foreground sm:inline-flex">
                  <Brain
                    className="size-3.5"
                    strokeWidth={1.7}
                    aria-hidden="true"
                  />
                  깊이 생각하기
                </span>
              )}
              {isGenerating ? (
                <Button
                  type="button"
                  size="icon"
                  disabled={Boolean(cancelling)}
                  onClick={onCancel}
                  aria-label={cancelling ? '중단 중...' : '답변 중단'}
                  title={cancelling ? '중단 중...' : '답변 중단'}
                  className="ml-auto size-10 rounded-full bg-primary text-primary-foreground hover:bg-primary/85"
                >
                  <Square
                    className="size-3.5 fill-current"
                    aria-hidden="true"
                  />
                </Button>
              ) : (
                <Button
                  type="submit"
                  size="icon"
                  disabled={!state.draft.trim() || !canSend}
                  aria-label={state.sending ? '접수 중...' : '메시지 전송'}
                  title={state.sending ? '접수 중...' : '메시지 전송'}
                  className="ml-auto size-10 rounded-full bg-primary text-primary-foreground hover:bg-primary/85 disabled:bg-muted disabled:text-muted-foreground/70 disabled:opacity-100 dark:disabled:bg-foreground/15"
                >
                  {state.sending ? (
                    <LoaderCircle
                      className="size-4 animate-spin motion-reduce:animate-none"
                      aria-hidden="true"
                    />
                  ) : (
                    <ArrowUp
                      className="size-5"
                      strokeWidth={2.5}
                      aria-hidden="true"
                    />
                  )}
                </Button>
              )}
            </div>
          </form>
        </ComposerExpansion>
        <p className="mt-3 hidden text-center text-[11px] leading-4 text-muted-foreground sm:block">
          {canSend ? 'Enter로 전송' : '다음 질문을 작성할 수 있습니다.'}{' '}
          <span className="mx-1.5" aria-hidden="true">
            ·
          </span>{' '}
          Shift+Enter로 줄바꿈
        </p>
      </div>
    </footer>
  );
}
