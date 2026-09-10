import type { ContextStatus } from '../state/context-status.ts';

const labels = {
  idle: '압축 없음',
  pending: '압축 대기',
  running: '압축 중',
  completed: '압축 완료',
  failed: '압축 실패',
  cancelled: '압축 중단',
};
const number = (value: number | null) =>
  value === null ? '미확인' : value.toLocaleString('ko-KR');

export function ContextStatusPanel({
  context,
}: {
  context?: ContextStatus | null;
}) {
  const input = context?.input_tokens ?? null;
  const output = context?.output_tokens ?? null;
  const used = input === null ? null : input + (output ?? 0);
  const percent =
    context && used !== null
      ? Math.round((used / context.context_window) * 100)
      : null;
  return (
    <details
      className="mt-2 rounded-xl px-3 text-[11px] text-muted-foreground"
      data-chat-menu
    >
      <summary className="w-fit cursor-pointer rounded-md py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
        문맥 {percent === null ? '미확인' : `${percent}%`}
        {context && ` · ${labels[context.compaction_status]}`}
      </summary>
      <div className="mt-1 max-h-[28dvh] space-y-2 overflow-y-auto rounded-xl border border-border/60 bg-background p-3 leading-5">
        {context ? (
          <>
            <p>
              {context.phase === 'preparing'
                ? '요청 준비 중 · 최종 입력은 달라질 수 있습니다.'
                : '최근 요청 기준 · 작성 중인 입력은 포함하지 않습니다.'}
            </p>
            <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1">
              <dt>입력 문맥</dt>
              <dd>
                {number(input)}
                {input !== null && ' 토큰'}
              </dd>
              <dt>확인된 답변 출력</dt>
              <dd>
                {number(output)}
                {output !== null && ' 토큰'}
              </dd>
              <dt>모델 문맥 상한</dt>
              <dd>{number(context.context_window)} 토큰</dd>
              <dt>이번 답변 출력 한도</dt>
              <dd>{number(context.max_output_tokens)} 토큰</dd>
            </dl>
            {used !== null && (
              <progress
                aria-label={
                  output === null
                    ? '입력 기준 문맥 사용 비율'
                    : '확인된 입력과 출력의 문맥 사용 비율'
                }
                max={context.context_window}
                value={Math.min(used, context.context_window)}
                className="h-1.5 w-full overflow-hidden rounded-full accent-foreground"
              />
            )}
            {output === null && (
              <p>출력 사용량이 미확인이므로 비율은 입력 기준입니다.</p>
            )}
            <p>
              {labels[context.compaction_status]} ·{' '}
              {context.summary_through_sequence === null
                ? '요약 반영 범위 미확인'
                : context.summary_through_sequence === 0
                  ? '이번 입력에 반영된 요약 없음'
                  : `대화 시작부터 메시지 순번 ${context.summary_through_sequence}까지 요약 반영`}
            </p>
            {['failed', 'cancelled'].includes(context.compaction_status) && (
              <p>원본 대화는 보존되며 미완성 요약은 재사용하지 않습니다.</p>
            )}
            <p>
              문맥 크기는 계정의 잔여 토큰과 별개입니다. 자동 압축 비용은 사용자
              한도에서 차감하지 않습니다.
            </p>
          </>
        ) : (
          <p>
            확인된 문맥 정보가 없습니다. 새 요청부터 입력량과 압축 상태를
            표시합니다.
          </p>
        )}
      </div>
    </details>
  );
}
