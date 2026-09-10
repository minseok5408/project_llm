import { Bell, LoaderCircle, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { ChatTask, ChatNotification } from '../state/chat-store.ts';

export function taskLabel(task: ChatTask) {
  if (task.cancelling || task.generation?.cancel_requested)
    return '중단 확인 중';
  if (task.stream === 'paused') return '연결 확인 필요';
  if (task.stream === 'reconnecting') return '다시 연결 중';
  if (task.sending) return '요청 접수 중';
  if (task.generation?.status === 'queued') return '생성 대기 중';
  if (task.generation?.stage === 'compacting') return '대화 압축 중';
  if (task.generation?.stage === 'searching') return '웹검색 중';
  return '답변 생성 중';
}
const resultLabel = (status: string) =>
  ({
    completed: '답변 완료',
    cancelled: '답변 중단',
    failed: '생성 실패',
    usage_pending: '사용량 확인 필요',
  })[status] ?? '작업 종료';

export function GenerationActivity({
  tasks,
  notifications,
  onOpen,
  onCancel,
  onReconnect,
  onDismiss,
}: {
  tasks: ChatTask[];
  notifications: ChatNotification[];
  onOpen: (id: string) => void;
  onCancel: (id: string) => void;
  onReconnect: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const latest = notifications[0];
  return (
    <>
      <output className="sr-only">
        {latest ? `${latest.title}: ${resultLabel(latest.status)}` : ''}
      </output>
      <details className="relative" data-chat-menu>
        <summary
          className="flex h-10 cursor-pointer list-none items-center gap-1.5 rounded-xl px-2.5 text-xs text-muted-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden"
          aria-label={`작업 ${tasks.length}개, 알림 ${notifications.length}개`}
        >
          {tasks.length ? (
            <LoaderCircle
              className="size-4 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
          ) : (
            <Bell className="size-4" aria-hidden="true" />
          )}
          <span>작업 {tasks.length}</span>
          {notifications.length > 0 && (
            <span className="rounded-full bg-primary px-1.5 text-primary-foreground">
              {notifications.length}
            </span>
          )}
        </summary>
        <div
          className="absolute right-0 top-12 z-40 max-h-[50dvh] w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-2xl border border-border bg-background p-3 shadow-lg"
          aria-label="작업과 완료 알림"
        >
          {!tasks.length && !notifications.length && (
            <p className="p-2 text-xs text-muted-foreground">
              진행 중인 작업과 새 알림이 없습니다.
            </p>
          )}
          {tasks.map((task) => (
            <div
              key={task.conversationId || 'pending'}
              className="mb-2 rounded-xl bg-muted/60 p-3 text-xs"
            >
              <button
                type="button"
                disabled={!task.conversationId}
                className="w-full truncate text-left font-medium underline-offset-4 hover:underline focus-visible:outline-ring"
                onClick={() => onOpen(task.conversationId)}
              >
                {task.title}
              </button>
              <p className="mt-1 text-muted-foreground">{taskLabel(task)}</p>
              {task.error && (
                <p className="mt-1 text-destructive">{task.error}</p>
              )}
              <div className="mt-2 flex gap-2">
                {task.stream === 'paused' && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onReconnect(task.conversationId)}
                  >
                    다시 연결
                  </Button>
                )}
                {task.generation && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={
                      task.cancelling || task.generation.cancel_requested
                    }
                    onClick={() => onCancel(task.conversationId)}
                  >
                    중단
                  </Button>
                )}
              </div>
            </div>
          ))}
          {notifications.map((item) => (
            <div
              key={item.id}
              className="flex items-start gap-2 rounded-xl p-2 text-xs hover:bg-muted/50"
            >
              <button
                type="button"
                className="min-w-0 flex-1 rounded-md text-left leading-5 focus-visible:outline-ring"
                onClick={() => {
                  onOpen(item.conversationId);
                  onDismiss(item.id);
                }}
              >
                <span className="block truncate font-medium">{item.title}</span>
                <span className="text-muted-foreground">
                  {resultLabel(item.status)}
                </span>
              </button>
              <Button
                size="icon"
                variant="ghost"
                className="size-8 shrink-0"
                aria-label={`${item.title} 알림 닫기`}
                onClick={() => onDismiss(item.id)}
              >
                <X className="size-3.5" aria-hidden="true" />
              </Button>
            </div>
          ))}
        </div>
      </details>
    </>
  );
}
