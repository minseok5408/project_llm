'use client';

import { useEffect, useId, useRef, useState, type SubmitEvent } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import { sidebarReturnFocus } from '../hooks/sidebar-focus.ts';
import type { Conversation } from '../state/chat-store.ts';

export type ConversationManagementTarget = {
  conversation: Conversation;
  kind: 'rename' | 'delete';
};

type Props = {
  target: ConversationManagementTarget | null;
  onClose: () => void;
  onRename: (id: string, title: string) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
  error: string | null;
};

export function ConversationManagementDialog(props: Props) {
  if (!props.target) return null;
  return (
    <ConversationManagementForm
      key={`${props.target.conversation.id}:${props.target.kind}`}
      {...props}
      target={props.target}
    />
  );
}

function ConversationManagementForm({
  target,
  onClose,
  onRename,
  onDelete,
  error,
}: Props & { target: ConversationManagementTarget }) {
  const rename = target.kind === 'rename';
  const titleId = useId();
  const [title, setTitle] = useState(target.conversation.title);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const [origin] = useState(() =>
    typeof document === 'undefined'
      ? null
      : (document.activeElement as HTMLElement | null),
  );
  const changed = title.trim() !== target.conversation.title;
  const canSubmit = !busy && (!rename || (Boolean(title.trim()) && changed));

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current || !canSubmit) return;
    // 재렌더 전에 다시 누르거나 Enter를 반복해도 한 번만 요청한다.
    pending.current = true;
    setBusy(true);
    setFailure(null);
    try {
      const succeeded = rename
        ? await onRename(target.conversation.id, title.trim())
        : await onDelete(target.conversation.id);
      if (!mounted.current) return;
      if (succeeded) onClose();
      else setFailure('변경하지 못했습니다. 다시 시도해 주세요.');
    } catch {
      if (mounted.current)
        setFailure('변경하지 못했습니다. 다시 시도해 주세요.');
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending.current) onClose();
      }}
    >
      <DialogContent
        showCloseButton={false}
        initialFocus={rename ? input : cancel}
        finalFocus={() => sidebarReturnFocus(origin)}
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl p-5 sm:max-w-md sm:p-6"
      >
        <div className="flex items-center justify-between gap-3">
          <DialogTitle>{rename ? '대화 제목 변경' : '대화 삭제'}</DialogTitle>
          <DialogClose
            disabled={busy}
            aria-label="대화 관리 닫기"
            render={
              <Button
                variant="ghost"
                size="icon"
                className="size-10 rounded-full"
              />
            }
          >
            <X className="size-4" aria-hidden="true" />
          </DialogClose>
        </div>
        <DialogDescription>
          {rename
            ? '대화를 구분하기 쉬운 제목으로 바꾸세요.'
            : '선택한 대화를 삭제합니다. 삭제한 대화는 다시 불러올 수 없습니다.'}
        </DialogDescription>
        <form onSubmit={submit} aria-busy={busy} className="space-y-5">
          {rename ? (
            <label htmlFor={titleId} className="block space-y-2 text-sm">
              <span>대화 제목</span>
              <Input
                id={titleId}
                ref={input}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                disabled={busy}
                maxLength={300}
                required
                className="h-11 rounded-xl"
              />
            </label>
          ) : (
            <p className="break-words rounded-xl bg-muted px-4 py-3 text-sm font-medium">
              {target.conversation.title}
            </p>
          )}
          {(error || failure) && (
            <p role="alert" className="text-sm text-destructive">
              {error || failure}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <DialogClose
              ref={cancel}
              disabled={busy}
              render={
                <Button
                  type="button"
                  variant="ghost"
                  className="h-11 rounded-xl px-4"
                />
              }
            >
              취소
            </DialogClose>
            <Button
              type="submit"
              disabled={!canSubmit}
              variant={rename ? 'default' : 'destructive'}
              className="h-11 rounded-xl px-4"
            >
              {busy
                ? rename
                  ? '저장하는 중…'
                  : '삭제하는 중…'
                : rename
                  ? '저장'
                  : '삭제'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
