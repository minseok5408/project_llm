'use client';

import {
  useId,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react';
import { Brain, Coins, Globe2, RefreshCw, Settings2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import type { TokenBalance } from '../../usage/types.ts';
import { sidebarReturnFocus } from '../../chat/hooks/sidebar-focus.ts';

const number = (value: number | null | undefined) =>
  value == null ? '—' : value.toLocaleString();

const sections = [
  { id: 'general', label: '일반', icon: Settings2 },
  { id: 'network', label: '연결 및 검색', icon: Globe2 },
  { id: 'memory', label: '기억', icon: Brain },
  { id: 'usage', label: '사용량', icon: Coins },
] as const;

type SettingsTab = (typeof sections)[number]['id'];

type SettingsContentProps = {
  usage: TokenBalance | null;
  generalSettings?: ReactNode;
  networkSettings?: ReactNode;
  memorySettings?: ReactNode;
  onRefresh: () => void;
};

export function TokenUsageDetails({
  usage,
  onRefresh,
}: Pick<SettingsContentProps, 'usage' | 'onRefresh'>) {
  const planLabel = usage?.unlimited
    ? '한도 없음'
    : usage?.budget_source === 'free_monthly'
      ? '무료 플랜'
      : usage?.plan_name || '내 계정';

  return (
    <section aria-label="토큰 사용량" className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <span className="rounded-full bg-muted px-3 py-1.5 text-xs font-medium">
          {planLabel}
        </span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onRefresh}
          aria-label="토큰 사용량 새로고침"
          className="h-9 gap-2 rounded-lg px-2.5 text-xs text-muted-foreground"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          새로고침
        </Button>
      </div>
      <div className="rounded-2xl border border-border/70 bg-muted/35 p-5">
        <p className="text-xs text-muted-foreground">
          {usage?.unlimited ? '사용한 토큰' : '남은 토큰'}
        </p>
        <p className="mt-2 text-3xl font-semibold tabular-nums tracking-tight">
          {number(
            usage?.unlimited ? usage.used_tokens : usage?.remaining_tokens,
          )}
        </p>
        <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-border/70 pt-4 text-xs">
          <div>
            <dt className="text-muted-foreground">실사용</dt>
            <dd className="mt-1.5 text-sm font-medium tabular-nums">
              {number(usage?.used_tokens)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">
              {usage?.unlimited
                ? '사용 한도'
                : usage?.budget_source === 'free_monthly'
                  ? '월 한도'
                  : '기간 한도'}
            </dt>
            <dd className="mt-1.5 text-sm font-medium tabular-nums">
              {usage?.unlimited ? '한도 없음' : number(usage?.token_limit)}
            </dd>
          </div>
        </dl>
      </div>
      <div className="space-y-1 text-xs leading-5 text-muted-foreground">
        {usage?.ends_at && (
          <p>
            {usage.budget_source === 'free_monthly'
              ? `다음 갱신 ${new Date(usage.ends_at).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric' })} (한국시간)`
              : `${new Date(usage.ends_at).toLocaleDateString('ko-KR')}까지`}
          </p>
        )}
        {usage?.budget_source === 'free_monthly' && (
          <p>매월 1일 갱신 · 남은 토큰은 이월되지 않습니다.</p>
        )}
        <p>답변 완료·중단 후 확인된 사용량을 반영합니다.</p>
      </div>
    </section>
  );
}

export function AccountSettingsContent({
  usage,
  generalSettings,
  networkSettings,
  memorySettings,
  onRefresh,
  initialTab = 'general',
}: SettingsContentProps & { initialTab?: SettingsTab }) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const id = useId();
  const moveTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const nextIndex =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? sections.length - 1
          : ['ArrowRight', 'ArrowDown'].includes(event.key)
            ? (index + 1) % sections.length
            : ['ArrowLeft', 'ArrowUp'].includes(event.key)
              ? (index + sections.length - 1) % sections.length
              : null;
    if (nextIndex == null) return;
    event.preventDefault();
    setTab(sections[nextIndex].id);
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      [nextIndex]?.focus();
  };
  const selected = sections.find((section) => section.id === tab)!;

  return (
    <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
      <div
        role="tablist"
        aria-label="설정 항목"
        className="flex shrink-0 gap-1 overflow-x-auto border-b border-border/70 bg-muted/20 p-2 sm:w-44 sm:flex-col sm:border-b-0 sm:border-r sm:p-3"
      >
        {sections.map(({ id: sectionId, label, icon: Icon }, index) => (
          <button
            key={sectionId}
            id={`${id}-${sectionId}-tab`}
            type="button"
            role="tab"
            aria-selected={tab === sectionId}
            aria-controls={`${id}-${sectionId}-panel`}
            tabIndex={tab === sectionId ? 0 : -1}
            onClick={() => setTab(sectionId)}
            onKeyDown={(event) => moveTab(event, index)}
            className={`flex min-h-10 min-w-0 flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl px-2 text-left text-xs transition-colors focus-visible:outline-2 focus-visible:outline-ring sm:flex-none sm:justify-start sm:gap-2.5 sm:px-3 sm:text-[13px] ${tab === sectionId ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}
          >
            <Icon
              className="hidden size-4 shrink-0 min-[360px]:block"
              strokeWidth={1.7}
              aria-hidden="true"
            />
            {label}
          </button>
        ))}
      </div>
      <div
        id={`${id}-${tab}-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-${tab}-tab`}
        tabIndex={0}
        className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain p-5 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/30 sm:p-7"
      >
        <h2 className="mb-5 text-base font-semibold tracking-tight">
          {selected.label}
        </h2>
        {tab === 'general' && generalSettings}
        {tab === 'network' && networkSettings}
        {tab === 'memory' && memorySettings}
        {tab === 'usage' && (
          <TokenUsageDetails usage={usage} onRefresh={onRefresh} />
        )}
      </div>
    </div>
  );
}

export function AccountSettingsDialog({
  open,
  onOpenChange,
  returnFocus,
  ...contentProps
}: SettingsContentProps & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnFocus: RefObject<HTMLElement | null>;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        finalFocus={() => sidebarReturnFocus(returnFocus.current)}
        className="flex h-[min(560px,calc(100dvh-2rem))] max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden rounded-3xl p-0 sm:max-w-[760px]"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border/70 px-5 py-4 sm:px-6">
          <DialogTitle className="text-lg font-semibold">설정</DialogTitle>
          <DialogDescription className="sr-only">
            화면, 연결 및 검색, 기억, 사용량을 관리합니다.
          </DialogDescription>
          <DialogClose
            render={
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="size-9 rounded-full"
              />
            }
          >
            <X className="size-[18px]" aria-hidden="true" />
            <span className="sr-only">설정 닫기</span>
          </DialogClose>
        </div>
        <AccountSettingsContent {...contentProps} />
      </DialogContent>
    </Dialog>
  );
}
