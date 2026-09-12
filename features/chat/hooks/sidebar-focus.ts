/** 닫힌 모바일 목록 대신 보이는 시작점으로 돌아가고 새 모달의 초점은 유지한다. */
export function sidebarReturnFocus(
  preferred?: HTMLElement | null,
): HTMLElement | false {
  const dialog = [
    ...document.querySelectorAll<HTMLElement>(
      '[data-slot="dialog-content"][data-open]',
    ),
  ].at(-1);
  if (dialog)
    return preferred?.isConnected &&
      preferred.getClientRects().length &&
      dialog.contains(preferred)
      ? preferred
      : false;
  const candidates = [
    preferred,
    document.querySelector<HTMLElement>('[data-chat-sidebar-trigger]'),
    document.querySelector<HTMLElement>(
      '[aria-label="대화 목록"] [aria-current="page"]',
    ),
    document.getElementById('chat-message-input'),
  ];
  return (
    candidates.find(
      (item) => item?.isConnected && item.getClientRects().length,
    ) ?? false
  );
}
