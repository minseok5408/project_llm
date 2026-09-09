'use client';

import type {
  NetworkModeState,
  WebSearchMode,
} from '../state/network-mode-store.ts';

export function NetworkModeSwitch({
  state,
  onLocalOnly,
  onCheck,
  onWebSearch,
}: {
  state: NetworkModeState;
  onLocalOnly: (value: boolean) => void;
  onCheck: () => void;
  onWebSearch: (value: WebSearchMode) => void;
}) {
  const value = state.value;
  const localOnly = value?.local_only ?? false;
  const busy = state.loading || state.saving || state.checking;
  const online =
    !state.error && !state.saving && value?.mode === 'online' && !localOnly;
  const explanation = state.saving
    ? '설정 저장 중 · 지금 보내는 질문은 로컬로 답합니다.'
    : state.error
      ? `설정을 확인하지 못해 로컬로 답합니다. ${state.error}`
      : localOnly
        ? '웹검색과 외부 연결 확인을 사용하지 않습니다.'
        : state.loading || state.checking
          ? '검색 연결을 확인하고 있습니다.'
          : value?.reason === 'provider_unconfigured'
            ? '검색 서비스가 설정되지 않아 로컬로 답합니다.'
            : value?.reason === 'offline'
              ? '인터넷 검색에 연결할 수 없어 로컬로 답합니다.'
              : '검색이 필요한 질문은 외부 검색 서비스에 전달됩니다. 답변은 로컬에서 생성합니다.';
  return (
    <section
      aria-label="인터넷 사용 설정"
      className="border-t border-border/70 px-4 py-2 text-[11px] text-muted-foreground"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="button"
          role="switch"
          aria-checked={localOnly}
          aria-label="로컬 전용 모드"
          disabled={state.loading || state.saving}
          onClick={() => onLocalOnly(!localOnly)}
          className="flex min-h-8 items-center gap-2 focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
        >
          <span
            aria-hidden="true"
            className={`flex h-4 w-7 items-center border px-0.5 ${localOnly ? 'justify-end border-primary bg-primary/20' : 'justify-start border-border bg-muted'}`}
          >
            <span
              className={`h-2.5 w-2.5 ${localOnly ? 'bg-primary' : 'bg-muted-foreground'}`}
            />
          </span>
          로컬 전용 {localOnly ? 'ON' : 'OFF'}
        </button>
        <span
          aria-live="polite"
          className={online ? 'text-primary' : 'text-amber-200'}
        >
          {busy
            ? '확인 중'
            : online
              ? '온라인'
              : localOnly
                ? '로컬 전용'
                : '로컬 모드'}
        </span>
        <label className="flex items-center gap-2">
          웹검색
          <select
            aria-label="웹검색 방식"
            value={state.webSearch}
            onChange={(event) =>
              onWebSearch(event.target.value as WebSearchMode)
            }
            disabled={
              localOnly || state.loading || state.saving || Boolean(state.error)
            }
            className="min-h-8 border border-border bg-background px-2 text-foreground disabled:opacity-50"
          >
            <option value="auto">자동</option>
            <option value="on">항상 검색</option>
            <option value="off">검색 안 함</option>
          </select>
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={onCheck}
          className="min-h-8 px-1 text-primary hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
        >
          다시 확인
        </button>
      </div>
      <p className="mt-1 leading-5" role={state.error ? 'alert' : undefined}>
        {explanation}
      </p>
    </section>
  );
}
