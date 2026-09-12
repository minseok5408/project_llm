'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type HTMLAttributes,
} from 'react';
import {
  ChatScrollFollow,
  preserveScrollAnchor,
} from '../scroll/chat-scroll.ts';

type HistoryAnchor = {
  conversationId: string;
  messageId: string;
  offset: number;
};

/** 읽던 위치와 사용자 스크롤 의도를 보존하고 이전 메시지의 비동기 복원을 격리한다. */
export function useChatScroll({
  conversationId,
  messages,
  loadOlderMessages,
  reveal,
}: {
  conversationId: string | null;
  messages: readonly { id: string }[];
  loadOlderMessages: () => Promise<void>;
  reveal?: { conversationId: string; messageId: string; key: number } | null;
}) {
  const scrollViewport = useRef<HTMLElement | null>(null);
  const scrollContent = useRef<HTMLDivElement | null>(null);
  const [scrollFollow] = useState(() => new ChatScrollFollow());
  const [showLatest, setShowLatest] = useState(false);
  const [pendingOlder, setPendingOlder] = useState<{
    conversationId: string;
    version: number;
  } | null>(null);
  const loadingOlder = pendingOlder?.conversationId === conversationId;
  const messageElements = useRef(new Map<string, HTMLElement>());
  const scrollConversation = useRef<string | null>(null);
  const touchY = useRef<number | null>(null);
  const historyAnchor = useRef<HistoryAnchor | null>(null);
  const loadVersion = useRef(0);
  const pendingLoad = useRef(false);
  const mounted = useRef(false);
  const revealedKey = useRef<number | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const updateScroll = useCallback(() => {
    const viewport = scrollViewport.current;
    if (!viewport) return;
    const target = scrollFollow.targetAfterResize(viewport);
    if (target !== null) {
      viewport.scrollTop = target;
      scrollFollow.recordPosition(viewport.scrollTop);
    }
    setShowLatest(scrollFollow.hasNewerContent(viewport));
  }, [scrollFollow]);
  const followLatest = useCallback(() => {
    scrollFollow.resume();
    updateScroll();
  }, [scrollFollow, updateScroll]);
  const pauseFollowing = useCallback(() => {
    scrollFollow.pause();
    updateScroll();
  }, [scrollFollow, updateScroll]);

  useLayoutEffect(() => {
    const viewport = scrollViewport.current;
    if (!viewport) return;
    if (
      scrollConversation.current !== conversationId ||
      messages.length === 0
    ) {
      scrollConversation.current = conversationId;
      historyAnchor.current = null;
      scrollFollow.resume();
      // 이전 대화의 로딩 완료가 새 대화의 앵커나 버튼 상태를 바꾸지 않게 한다.
      ++loadVersion.current;
      pendingLoad.current = false;
    }
    const anchor = historyAnchor.current;
    if (
      anchor &&
      anchor.conversationId === conversationId &&
      messages[0]?.id !== anchor.messageId
    ) {
      const element = messageElements.current.get(anchor.messageId);
      if (element) {
        const offset =
          element.getBoundingClientRect().top -
          viewport.getBoundingClientRect().top;
        viewport.scrollTop = preserveScrollAnchor(
          viewport.scrollTop,
          anchor.offset,
          offset,
        );
        scrollFollow.recordPosition(viewport.scrollTop);
      }
      historyAnchor.current = null;
    } else if (!loadingOlder) historyAnchor.current = null;
    updateScroll();
  }, [messages, conversationId, loadingOlder, scrollFollow, updateScroll]);

  useEffect(() => {
    const viewport = scrollViewport.current;
    const content = scrollContent.current;
    if (!viewport || !content) return;
    // 글자 단위 표시와 화면 크기 변화도 사용자가 선택한 따라가기 상태를 지킨다.
    const observer = new ResizeObserver(updateScroll);
    observer.observe(content);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [updateScroll]);

  const loadOlder = async () => {
    if (pendingLoad.current || !conversationId) return;
    const viewport = scrollViewport.current;
    const firstId = messages[0]?.id;
    if (!firstId) return;
    const element = messageElements.current.get(firstId);
    if (viewport && element) {
      pauseFollowing();
      historyAnchor.current = {
        conversationId,
        messageId: firstId,
        offset:
          element.getBoundingClientRect().top -
          viewport.getBoundingClientRect().top,
      };
    }
    const version = ++loadVersion.current;
    pendingLoad.current = true;
    setPendingOlder({ conversationId, version });
    try {
      await loadOlderMessages();
    } finally {
      if (mounted.current) {
        if (version === loadVersion.current) pendingLoad.current = false;
        setPendingOlder((current) =>
          current?.version === version ? null : current,
        );
      }
    }
  };
  const registerMessage = useCallback(
    (id: string, element: HTMLElement | null) => {
      if (element) messageElements.current.set(id, element);
      else messageElements.current.delete(id);
    },
    [],
  );
  useLayoutEffect(() => {
    if (
      !reveal ||
      reveal.conversationId !== conversationId ||
      revealedKey.current === reveal.key
    )
      return;
    const viewport = scrollViewport.current;
    const element = messageElements.current.get(reveal.messageId);
    if (!viewport || !element) return;
    scrollFollow.pause();
    historyAnchor.current = null;
    viewport.scrollTop +=
      element.getBoundingClientRect().top -
      viewport.getBoundingClientRect().top -
      16;
    scrollFollow.recordPosition(viewport.scrollTop);
    element.tabIndex = -1;
    element.focus({ preventScroll: true });
    revealedKey.current = reveal.key;
    setShowLatest(scrollFollow.hasNewerContent(viewport));
  }, [reveal, conversationId, messages, scrollFollow]);
  const viewportHandlers: Pick<
    HTMLAttributes<HTMLElement>,
    | 'onScroll'
    | 'onWheel'
    | 'onTouchStart'
    | 'onTouchMove'
    | 'onTouchEnd'
    | 'onKeyDown'
  > = {
    onScroll(event) {
      const viewport = event.currentTarget;
      scrollFollow.onScroll(viewport);
      const anchor = historyAnchor.current;
      const element = anchor
        ? messageElements.current.get(anchor.messageId)
        : null;
      if (anchor && element)
        anchor.offset =
          element.getBoundingClientRect().top -
          viewport.getBoundingClientRect().top;
      setShowLatest(scrollFollow.hasNewerContent(viewport));
    },
    onWheel(event) {
      if (event.deltaY < 0) pauseFollowing();
    },
    onTouchStart(event) {
      touchY.current = event.touches[0]?.clientY ?? null;
    },
    onTouchMove(event) {
      const currentY = event.touches[0]?.clientY;
      if (
        currentY != null &&
        touchY.current != null &&
        currentY > touchY.current
      )
        pauseFollowing();
      touchY.current = currentY ?? null;
    },
    onTouchEnd() {
      touchY.current = null;
    },
    onKeyDown(event) {
      if (
        ['ArrowUp', 'PageUp', 'Home'].includes(event.key) ||
        (event.key === ' ' && event.shiftKey)
      )
        pauseFollowing();
    },
  };

  return {
    scrollViewport,
    scrollContent,
    viewportHandlers,
    registerMessage,
    showLatest,
    loadingOlder,
    followLatest,
    pauseFollowing,
    loadOlder,
  };
}
