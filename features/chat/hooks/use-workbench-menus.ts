'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/** 검색 단축키와 메뉴 닫기만 담당하며 모달·접힌 대화 본문의 초점은 건드리지 않는다. */
export function useWorkbenchMenus() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchReturnFocus = useRef<HTMLElement | null>(null);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const openSearch = useCallback(() => {
    searchReturnFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setSearchOpen(true);
  }, []);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === 'k' &&
        !event.isComposing &&
        !document.querySelector('[data-slot="dialog-content"][data-open]')
      ) {
        event.preventDefault();
        openSearch();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [openSearch]);

  useEffect(() => {
    const openMenus = () =>
      document.querySelectorAll<HTMLDetailsElement>(
        'details[data-chat-menu][open]',
      );
    const closeOutside = (event: Event) => {
      for (const menu of openMenus()) {
        if (event.target instanceof Node && !menu.contains(event.target))
          menu.open = false;
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      // 모달이 열린 동안에는 모달의 닫기와 초점 복귀가 Escape를 처리한다.
      if (
        event.key !== 'Escape' ||
        event.defaultPrevented ||
        document.querySelector('[data-slot="dialog-content"][data-open]')
      )
        return;
      const menus = [...openMenus()];
      if (menus.length) {
        const focusedMenu = menus.find((menu) =>
          menu.contains(document.activeElement),
        );
        for (const menu of menus) menu.open = false;
        focusedMenu?.querySelector('summary')?.focus();
      } else closeSidebar();
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('focusin', closeOutside);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('focusin', closeOutside);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [closeSidebar]);

  return {
    sidebarOpen,
    setSidebarOpen,
    closeSidebar,
    searchOpen,
    setSearchOpen,
    searchReturnFocus,
    openSearch,
  };
}
