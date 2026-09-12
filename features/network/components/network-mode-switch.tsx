'use client';

import type { NetworkModeState } from '../state/network-mode-store.ts';

export function NetworkModeSwitch({
  state,
  onDataUsage,
  onCheck,
}: {
  state: NetworkModeState;
  onDataUsage: (value: boolean) => void;
  onCheck: () => void;
}) {
  const value = state.value;
  const dataUsage = value ? !value.local_only : false;
  const busy = state.loading || state.saving || state.checking;
  const online = !state.error && !busy && value?.mode === 'online' && dataUsage;
  const explanation = state.saving
    ? '설정 저장 중 · 지금 보내는 질문은 로컬로 답합니다.'
    : state.error
      ? `설정을 확인하지 못해 로컬로 답합니다. ${state.error}`
      : state.loading || state.checking
        ? '설정을 확인하는 동안 로컬로 답합니다.'
        : !dataUsage
          ? '웹검색과 외부 연결 확인을 사용하지 않습니다. 로컬 지식과 첨부한 자료로 답합니다.'
          : value?.reason === 'provider_unconfigured'
            ? '검색 서비스가 설정되지 않아 로컬로 답합니다.'
            : value?.reason === 'offline'
              ? '인터넷 검색에 연결할 수 없어 로컬로 답합니다.'
              : '최신 정보나 검색 요청 등 필요한 질문에만 웹검색을 사용합니다. 인사와 기초 질문은 로컬로 답합니다. 검색할 때는 질문과 최근 대화에서 필요한 이름·검색어만 외부 검색 서비스에 전달되며 답변은 로컬에서 생성합니다.';
  return (
    <section
      aria-label="데이터 사용 설정"
      className="w-full space-y-4 rounded-2xl bg-background p-4 text-sm text-foreground"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold">데이터 사용 설정</h2>
        <span
          aria-live="polite"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground"
        >
          <span
            aria-hidden="true"
            className={`size-1.5 rounded-full ${online ? 'bg-foreground' : 'bg-muted-foreground/50'}`}
          />
          {busy ? '확인 중' : online ? '온라인' : '로컬 모드'}
        </span>
      </div>
      <div className="overflow-hidden rounded-xl border border-border">
        <button
          type="button"
          role="switch"
          aria-checked={dataUsage}
          aria-label="데이터 사용"
          disabled={state.loading || state.saving}
          onClick={() => onDataUsage(!dataUsage)}
          className="flex min-h-14 w-full items-center justify-between gap-3 px-3 text-left transition-colors hover:bg-muted/70 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span className="font-medium">
            데이터 사용 {dataUsage ? 'ON' : 'OFF'}
          </span>
          <span
            aria-hidden="true"
            className={`flex h-6 w-10 shrink-0 items-center rounded-full p-0.5 transition-colors ${dataUsage ? 'justify-end bg-primary' : 'justify-start bg-muted-foreground/30'}`}
          >
            <span className="size-5 rounded-full bg-background shadow-sm" />
          </span>
        </button>
      </div>
      <p
        className={
          state.error
            ? 'break-words rounded-xl bg-destructive/5 px-3 py-2.5 text-xs leading-5 text-destructive'
            : 'text-xs leading-5 text-muted-foreground'
        }
        role={state.error ? 'alert' : undefined}
      >
        {explanation}
      </p>
      <button
        type="button"
        disabled={busy}
        onClick={onCheck}
        className="h-10 w-full rounded-xl border border-border bg-background text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
      >
        다시 확인
      </button>
    </section>
  );
}
