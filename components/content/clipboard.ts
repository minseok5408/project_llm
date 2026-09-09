type ClipboardEnvironment = {
  clipboard?: Pick<Clipboard, 'writeText'>;
  document?: Document;
};

export async function copyText(
  text: string,
  environment: ClipboardEnvironment = {
    clipboard: globalThis.navigator?.clipboard,
    document: globalThis.document,
  },
) {
  try {
    if (environment.clipboard) {
      await environment.clipboard.writeText(text);
      return;
    }
  } catch {
    // 권한 거부나 HTTP 접속에서도 사용자의 복사 동작을 이어서 처리한다.
  }
  const document = environment.document;
  if (!document?.body) throw new Error('복사 기능을 사용할 수 없습니다.');
  const active = document.activeElement as HTMLElement | null;
  const selection = document.getSelection();
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) =>
        selection.getRangeAt(index).cloneRange(),
      )
    : [];
  const input = active as HTMLInputElement | HTMLTextAreaElement | null;
  const inputSelection =
    input && typeof input.selectionStart === 'number'
      ? {
          start: input.selectionStart,
          end: input.selectionEnd,
          direction: input.selectionDirection,
        }
      : null;
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.readOnly = true;
  textarea.setAttribute('aria-hidden', 'true');
  textarea.style.cssText =
    'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px';
  document.body.append(textarea);
  try {
    textarea.focus({ preventScroll: true });
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    // 같은 Wi-Fi의 HTTP에서는 Clipboard API가 없어 기존 복사 API를 보조 수단으로 쓴다.
    // oxlint-disable-next-line typescript/no-deprecated
    if (!document.execCommand('copy'))
      throw new Error('복사에 실패했습니다. 내용을 직접 선택해 주세요.');
  } finally {
    textarea.remove();
    active?.focus({ preventScroll: true });
    if (inputSelection && input)
      input.setSelectionRange(
        inputSelection.start,
        inputSelection.end,
        inputSelection.direction ?? undefined,
      );
    else if (selection) {
      selection.removeAllRanges();
      for (const range of ranges) selection.addRange(range);
    }
  }
}
