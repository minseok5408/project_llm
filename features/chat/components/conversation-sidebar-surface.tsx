'use client';

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react';
import { Dialog as SheetPrimitive } from '@base-ui/react/dialog';
import { Sheet, SheetDescription, SheetTitle } from '@/components/ui/sheet';
import { cn } from '@/lib/utils';
import { sidebarReturnFocus } from '../hooks/sidebar-focus.ts';

export function ConversationSidebarSurface({
  mobile,
  open,
  collapsed,
  onMobileClose,
  returnFocus,
  children,
}: {
  mobile: boolean;
  open: boolean;
  collapsed: boolean;
  onMobileClose: () => void;
  returnFocus?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const popup = useRef<HTMLDivElement>(null);
  const wasMobile = useRef(mobile);
  useEffect(() => {
    // 넓은 화면으로 옮긴 뒤 다시 줄여도 이전 모달 상태로 목록이 갑자기 열리지 않는다.
    if (wasMobile.current && !mobile && open) onMobileClose();
    wasMobile.current = mobile;
  }, [mobile, open, onMobileClose]);

  const closeMenuFirst = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    const menus = event.currentTarget.querySelectorAll<HTMLDetailsElement>(
      'details[data-chat-menu][open]',
    );
    if (!menus.length) return;
    event.preventDefault();
    event.stopPropagation();
    const focused = [...menus].find((menu) =>
      menu.contains(document.activeElement),
    );
    for (const menu of menus) menu.open = false;
    focused?.querySelector('summary')?.focus();
  };
  const desktopClass = cn(
    'hidden w-[260px] shrink-0 flex-col border-r border-sidebar-border/60 bg-sidebar text-sidebar-foreground md:flex',
    collapsed && 'md:w-14',
  );

  return (
    <div ref={setHost} className="contents">
      {/* 서버 첫 화면 이후에는 같은 Portal과 자식을 유지해 설정 입력을 보존한다. */}
      {!host && !mobile && (
        <aside className={desktopClass} aria-label="저장된 대화">
          {children}
        </aside>
      )}
      <Sheet
        open={!mobile || open}
        modal={mobile}
        disablePointerDismissal={!mobile}
        onOpenChange={(next, details) => {
          if (!mobile) {
            details.cancel();
            details.allowPropagation();
          } else if (!next) onMobileClose();
        }}
      >
        {host && (
          <SheetPrimitive.Portal
            container={host}
            keepMounted
            className="contents"
          >
            <SheetPrimitive.Backdrop
              hidden={!mobile || !open}
              className={cn(
                'fixed inset-0 z-50 bg-black/30 backdrop-blur-[2px]',
                (!mobile || !open) && 'hidden',
              )}
            />
            <SheetPrimitive.Popup
              ref={popup}
              render={<aside />}
              role={mobile ? 'dialog' : 'complementary'}
              aria-label={mobile ? undefined : '저장된 대화'}
              data-slot={mobile ? 'dialog-content' : 'conversation-sidebar'}
              hidden={mobile && !open}
              initialFocus={mobile}
              finalFocus={() => {
                const active = document.activeElement;
                // 검색창이나 본문으로 이미 옮겨간 초점은 폭 변경·모달 종료 때 유지한다.
                if (
                  active instanceof HTMLElement &&
                  active !== document.body &&
                  active.getClientRects().length &&
                  !popup.current?.contains(active)
                )
                  return false;
                return sidebarReturnFocus(returnFocus?.current);
              }}
              onKeyDownCapture={closeMenuFirst}
              className={
                mobile
                  ? cn(
                      'fixed inset-y-0 left-0 z-50 w-[280px] max-w-[85vw] flex-col overflow-hidden border-r border-sidebar-border/60 bg-sidebar text-sidebar-foreground outline-none',
                      open ? 'flex' : 'hidden',
                    )
                  : desktopClass
              }
            >
              <SheetTitle className="sr-only">저장된 대화</SheetTitle>
              <SheetDescription className="sr-only">
                대화를 선택하거나 새 대화와 계정 설정을 엽니다.
              </SheetDescription>
              {children}
            </SheetPrimitive.Popup>
          </SheetPrimitive.Portal>
        )}
      </Sheet>
    </div>
  );
}
