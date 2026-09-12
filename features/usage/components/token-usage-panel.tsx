'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronUp, LogOut, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AccountSettingsDialog } from '../../preferences/components/account-settings-dialog.tsx';
import type { TokenBalance } from '../types.ts';

export function TokenUsagePanel({
  usage,
  email,
  userName,
  collapsed = false,
  generalSettings,
  networkSettings,
  memorySettings,
  logoutPending,
  onRefresh,
  onLogout,
  onExpand,
}: {
  usage: TokenBalance | null;
  email: string;
  userName?: string;
  collapsed?: boolean;
  generalSettings?: ReactNode;
  networkSettings?: ReactNode;
  memorySettings?: ReactNode;
  logoutPending: boolean;
  onRefresh: () => void;
  onLogout: () => void;
  onExpand?: () => void;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const menu = useRef<HTMLDetailsElement>(null);
  const accountButton = useRef<HTMLElement>(null);
  const openAfterExpand = useRef(false);
  const displayName = userName?.trim() || email.split('@')[0] || '내 계정';
  const planLabel = usage?.unlimited
    ? '토큰 한도 없음'
    : usage?.budget_source === 'free_monthly'
      ? '무료 플랜'
      : usage?.plan_name || '내 계정';

  useEffect(() => {
    if (collapsed) {
      if (window.matchMedia('(min-width: 768px)').matches && menu.current)
        menu.current.open = false;
      return;
    }
    if (openAfterExpand.current && menu.current) {
      menu.current.open = true;
      openAfterExpand.current = false;
    }
  }, [collapsed]);

  return (
    <section
      className={`shrink-0 border-t border-sidebar-border/60 px-3 py-2.5 ${collapsed ? 'md:px-1.5' : ''}`}
      aria-label="계정 및 설정"
    >
      <details ref={menu} className="group/account relative" data-chat-menu>
        <summary
          ref={accountButton}
          className={`flex cursor-pointer list-none items-center gap-2.5 rounded-xl px-2 py-2 outline-none transition-colors hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-ring/30 group-open/account:bg-sidebar-accent [&::-webkit-details-marker]:hidden ${collapsed ? 'md:justify-center md:gap-0 md:px-1' : ''}`}
          aria-label={`${displayName} 계정 메뉴`}
          title={`${displayName} 계정 메뉴`}
          onClick={(event) => {
            if (
              collapsed &&
              onExpand &&
              window.matchMedia('(min-width: 768px)').matches
            ) {
              event.preventDefault();
              openAfterExpand.current = true;
              onExpand();
            }
          }}
        >
          <span
            className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-foreground"
            aria-hidden="true"
          >
            {Array.from(displayName).slice(0, 2).join('').toUpperCase()}
          </span>
          <span className={`min-w-0 flex-1 ${collapsed ? 'md:hidden' : ''}`}>
            <span className="block truncate text-[13px] font-medium">
              {displayName}
            </span>
            <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
              {planLabel}
            </span>
          </span>
          <ChevronUp
            className={`size-3.5 shrink-0 text-muted-foreground transition-transform group-open/account:rotate-180 ${collapsed ? 'md:hidden' : ''}`}
            aria-hidden="true"
          />
        </summary>
        <div
          className={`absolute bottom-full left-0 z-40 mb-2 max-h-[calc(100dvh-6rem)] w-full overflow-y-auto overscroll-contain rounded-2xl border border-border/80 bg-popover p-2 text-popover-foreground shadow-[0_8px_24px_rgb(0_0_0/0.12)] dark:border-sidebar-border dark:bg-sidebar ${collapsed ? 'md:hidden' : ''}`}
        >
          <div className="px-3 pb-3 pt-2">
            <h2 className="truncate text-sm font-semibold" title={displayName}>
              {displayName}
            </h2>
            <p
              className="mt-1 truncate text-xs text-muted-foreground"
              title={email}
            >
              {email}
            </p>
            <p className="mt-2 text-[11px] text-muted-foreground">
              {planLabel}
            </p>
          </div>
          <div className="border-t border-border/70 py-1">
            <Button
              type="button"
              variant="ghost"
              aria-haspopup="dialog"
              onClick={() => {
                if (menu.current) menu.current.open = false;
                setSettingsOpen(true);
              }}
              className="h-10 w-full justify-start gap-2.5 rounded-xl px-3 text-[13px] font-normal"
            >
              <Settings
                className="size-4 text-muted-foreground"
                strokeWidth={1.7}
                aria-hidden="true"
              />
              설정
            </Button>
          </div>
          <div className="space-y-0.5 border-t border-border/70 pt-1">
            <Button
              type="button"
              variant="ghost"
              disabled={logoutPending}
              onClick={onLogout}
              className="h-10 w-full justify-start gap-2.5 rounded-xl px-3 text-[13px] font-normal"
            >
              <LogOut
                className="size-4 text-muted-foreground"
                strokeWidth={1.7}
                aria-hidden="true"
              />
              {logoutPending ? '로그아웃 중...' : '로그아웃'}
            </Button>
          </div>
        </div>
      </details>
      <AccountSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        returnFocus={accountButton}
        usage={usage}
        generalSettings={generalSettings}
        networkSettings={networkSettings}
        memorySettings={memorySettings}
        onRefresh={onRefresh}
      />
    </section>
  );
}
