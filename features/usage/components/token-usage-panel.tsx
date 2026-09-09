'use client';

import { Button } from '@/components/ui/button';
import type { TokenBalance } from '../types.ts';

const number = (value: number | null | undefined) =>
  value == null ? '—' : value.toLocaleString();

export function TokenUsagePanel({
  usage,
  email,
  logoutPending,
  onRefresh,
  onLogoutAll,
}: {
  usage: TokenBalance | null;
  email: string;
  logoutPending: boolean;
  onRefresh: () => void;
  onLogoutAll: () => void;
}) {
  return (
    <section
      className="shrink-0 border-t border-border px-4 py-4"
      aria-label="토큰 사용량"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-xs text-muted-foreground">토큰 사용량</h2>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 rounded-none px-1 text-[10px]"
          onClick={onRefresh}
        >
          새로고침
        </Button>
      </div>
      <p className="mt-2 text-lg text-primary">
        {usage?.unlimited
          ? '한도 없음'
          : `${number(usage?.remaining_tokens)} 남음`}
      </p>
      {usage?.budget_source === 'free_monthly' && (
        <p className="mt-2 text-xs text-muted-foreground">
          무료 · 매월 {number(usage.token_limit)}토큰
        </p>
      )}
      {usage?.budget_source === 'plan' && usage.plan_name && (
        <p className="mt-2 text-xs text-muted-foreground">{usage.plan_name}</p>
      )}
      <dl className="mt-3 space-y-1 text-[11px] text-muted-foreground">
        <div className="flex justify-between">
          <dt>실사용</dt>
          <dd>{number(usage?.used_tokens)}</dd>
        </div>
        {!usage?.unlimited && (
          <div className="flex justify-between">
            <dt>
              {usage?.budget_source === 'free_monthly'
                ? '월 한도'
                : '기간 한도'}
            </dt>
            <dd>{number(usage?.token_limit)}</dd>
          </div>
        )}
      </dl>
      {usage?.ends_at && (
        <p className="mt-2 text-[10px] text-muted-foreground">
          {usage.budget_source === 'free_monthly'
            ? `다음 갱신 ${new Date(usage.ends_at).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric' })} (한국시간)`
            : `${new Date(usage.ends_at).toLocaleDateString('ko-KR')}까지`}
        </p>
      )}
      {usage?.budget_source === 'free_monthly' && (
        <p className="mt-2 text-[10px] leading-5 text-muted-foreground">
          매월 1일 갱신 · 남은 토큰은 이월되지 않습니다.
        </p>
      )}
      <p
        className="mt-3 truncate text-[11px] text-muted-foreground"
        title={email}
      >
        {email}
      </p>
      <Button
        variant="ghost"
        disabled={logoutPending}
        onClick={onLogoutAll}
        className="mt-2 h-7 rounded-none px-0 text-[10px] text-muted-foreground"
      >
        모든 기기에서 로그아웃
      </Button>
    </section>
  );
}
