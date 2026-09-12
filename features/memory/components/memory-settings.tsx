'use client';

import { useEffect, useId, useState, useSyncExternalStore } from 'react';
import { Button } from '@/components/ui/button';
import { MemoryStore, type MemoryRequest } from '../state/memory-store.ts';

export function MemorySettings({ request }: { request: MemoryRequest }) {
  const [store] = useState(() => new MemoryStore(request));
  const state = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getServerSnapshot,
  );
  const [editing, setEditing] = useState<string | undefined>();
  const [key, setKey] = useState('');
  const [content, setContent] = useState('');
  const [deleting, setDeleting] = useState<string | null>(null);
  const id = useId();
  useEffect(() => {
    void store.load();
    return store.dispose;
  }, [store]);
  const clear = () => {
    setEditing(undefined);
    setKey('');
    setContent('');
  };
  const busy = state.loading || state.saving || !state.value;
  const full =
    !editing && (state.value?.items.length ?? 0) >= (state.value?.limit ?? 20);
  return (
    <section aria-label="저장된 기억" className="space-y-5">
      <div className="space-y-2 text-sm leading-6 text-muted-foreground">
        <p>
          이름, 직업, 답변 선호처럼 새 채팅에서도 참고할 내용을 직접 저장하세요.
          대화 내용은 자동으로 저장되지 않습니다.
        </p>
        <p className="text-xs">
          기억을 변경하면 이전 내용을 사용 중인 답변은 중단됩니다. 기존 채팅
          원문은 유지됩니다.
        </p>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {state.value
            ? `${state.value.items.length} / ${state.value.limit}개`
            : '기억 불러오기'}
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={state.loading || state.saving}
          onClick={() => void store.load()}
        >
          새로고침
        </Button>
      </div>
      {state.error && (
        <p role="alert" className="text-sm text-destructive">
          {state.error}
        </p>
      )}
      {state.loading && (
        <output className="block text-sm text-muted-foreground">
          기억을 불러오는 중입니다.
        </output>
      )}
      {state.value && state.value.items.length === 0 && (
        <p className="rounded-xl bg-muted/40 p-4 text-sm text-muted-foreground">
          아직 저장된 기억이 없습니다.
        </p>
      )}
      <ul className="space-y-3">
        {state.value?.items.map((item) => (
          <li key={item.id} className="rounded-xl border border-border/70 p-4">
            <p className="break-words text-sm font-medium">{item.key}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
              {item.content}
            </p>
            <div className="mt-2 flex flex-wrap justify-end gap-1">
              {deleting === item.id ? (
                <>
                  <span className="mr-auto self-center text-xs">
                    이 기억을 삭제할까요?
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setDeleting(null)}
                  >
                    취소
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={async () => {
                      if (await store.remove(item.id)) {
                        setDeleting(null);
                        if (editing === item.id) clear();
                      }
                    }}
                  >
                    삭제 확인
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    aria-label={`${item.key} 수정`}
                    onClick={() => {
                      setEditing(item.id);
                      setKey(item.key);
                      setContent(item.content);
                      setDeleting(null);
                      document.getElementById(`${id}-key`)?.focus();
                    }}
                  >
                    수정
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    aria-label={`${item.key} 삭제`}
                    onClick={() => setDeleting(item.id)}
                  >
                    삭제
                  </Button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
      <form
        className="space-y-3 border-t border-border/70 pt-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await store.save(key, content, editing)) clear();
        }}
      >
        <h3 className="text-sm font-medium">
          {editing ? '기억 수정' : '기억 추가'}
        </h3>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-key`} className="text-xs">
            기억 이름
          </label>
          <input
            id={`${id}-key`}
            value={key}
            onChange={(event) => setKey(event.target.value)}
            maxLength={80}
            required
            disabled={state.saving}
            placeholder="예: 직업, 답변 선호"
            className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-content`} className="text-xs">
            기억할 내용
          </label>
          <textarea
            id={`${id}-content`}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            maxLength={500}
            required
            rows={3}
            disabled={state.saving}
            placeholder="예: 파이썬 백엔드 개발자이며 구체적인 코드 예시를 선호합니다."
            className="w-full resize-y rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
          />
          <p className="text-right text-xs text-muted-foreground">
            {content.length} / 500
          </p>
        </div>
        {full && (
          <p className="text-xs text-muted-foreground">
            기억이 가득 찼습니다. 기존 기억을 수정하거나 삭제해 주세요.
          </p>
        )}
        <div className="flex justify-end gap-2">
          {editing && (
            <Button
              type="button"
              variant="ghost"
              disabled={state.saving}
              onClick={clear}
            >
              수정 취소
            </Button>
          )}
          <Button
            type="submit"
            disabled={busy || full || !key.trim() || !content.trim()}
          >
            {state.saving ? '저장 중…' : editing ? '변경 저장' : '기억 저장'}
          </Button>
        </div>
      </form>
    </section>
  );
}
