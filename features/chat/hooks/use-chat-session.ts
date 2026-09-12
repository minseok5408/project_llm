'use client';

import { useEffect, useState, useSyncExternalStore } from 'react';
import { NetworkModeStore } from '../../network/state/network-mode-store.ts';
import { ChatStore } from '../state/chat-store.ts';
import type { RequestFn } from '../../../lib/http.ts';

const pathId = () =>
  window.location.pathname.match(/^\/chat\/([0-9a-f-]{36})\/?$/i)?.[1] ?? null;

type NavigationStore = Pick<
  ChatStore,
  | 'initialize'
  | 'openConversation'
  | 'newDraft'
  | 'refresh'
  | 'refreshFiles'
  | 'dispose'
>;

/** 브라우저 주소·복귀 이벤트를 연결하며 화면을 떠나면 저장소도 함께 정리한다. */
export function connectChatNavigation(
  store: NavigationStore,
  onNavigateBack: () => void,
) {
  void store.initialize(pathId());
  const navigateBack = () => {
    const id = pathId();
    if (id) void store.openConversation(id, false);
    else store.newDraft();
    onNavigateBack();
  };
  const refresh = () => {
    void store.refresh();
    void store.refreshFiles();
  };
  const visible = () => {
    if (document.visibilityState === 'visible') refresh();
  };
  window.addEventListener('popstate', navigateBack);
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', visible);
  return () => {
    window.removeEventListener('popstate', navigateBack);
    window.removeEventListener('focus', refresh);
    document.removeEventListener('visibilitychange', visible);
    store.dispose();
  };
}

/** 계정별 저장소의 수명과 주소 이동·화면 복귀 갱신을 함께 관리한다. */
export function useChatSession(request: RequestFn, onNavigateBack: () => void) {
  const [networkStore] = useState(() => new NetworkModeStore(request));
  const networkState = useSyncExternalStore(
    networkStore.subscribe,
    networkStore.getSnapshot,
    networkStore.getServerSnapshot,
  );
  const [store] = useState(
    () =>
      new ChatStore(
        request,
        (id) => {
          const target = id ? `/chat/${id}` : '/';
          if (window.location.pathname !== target)
            window.history.pushState(window.history.state, '', target);
        },
        networkStore.generationPolicy,
      ),
  );
  const state = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getServerSnapshot,
  );

  useEffect(() => {
    void networkStore.initialize();
    return () => networkStore.dispose();
  }, [networkStore]);
  useEffect(() => {
    networkStore.observeSearch(state.generation?.search);
  }, [state.generation?.search, networkStore]);
  useEffect(
    () => connectChatNavigation(store, onNavigateBack),
    [store, onNavigateBack],
  );
  return { store, state, networkStore, networkState };
}
