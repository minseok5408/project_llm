'use client';

import { useEffect, useId, useSyncExternalStore } from 'react';
import { ChevronDown, Monitor } from 'lucide-react';
import { themeStore } from '../state/theme.ts';
import type { ThemePreference } from '../state/theme.ts';

export function ThemeSync() {
  useEffect(() => themeStore.connect(), []);
  return null;
}

export function ThemeSetting() {
  const id = useId();
  const preference = useSyncExternalStore(
    themeStore.subscribe,
    themeStore.getSnapshot,
    themeStore.getServerSnapshot,
  );
  useEffect(() => themeStore.connect(), []);

  return (
    <section aria-label="화면 테마 설정" className="text-sm text-foreground">
      <label htmlFor={id} className="flex items-center justify-between gap-3">
        <span className="flex shrink-0 items-center gap-2 font-medium">
          <Monitor
            className="size-4 text-muted-foreground"
            aria-hidden="true"
          />
          화면 테마
        </span>
        <span className="relative inline-flex shrink-0 items-center">
          <select
            id={id}
            aria-label="테마 선택"
            value={preference}
            onChange={(event) =>
              themeStore.setPreference(event.target.value as ThemePreference)
            }
            className="h-9 w-auto cursor-pointer appearance-none rounded-none border-0 bg-transparent pl-3 pr-9 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/30 [&>option]:bg-background"
          >
            <option value="system">시스템 설정</option>
            <option value="light">라이트 모드</option>
            <option value="dark">다크 모드</option>
          </select>
          <ChevronDown
            className="pointer-events-none absolute right-3 size-4 text-muted-foreground"
            aria-hidden="true"
          />
        </span>
      </label>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">
        시스템 설정을 선택하면 기기의 밝은 화면·어두운 화면 설정을 따릅니다.
      </p>
    </section>
  );
}
