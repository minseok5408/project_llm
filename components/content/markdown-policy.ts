// 모델이 만든 주소도 사용자 입력처럼 취급하고 실행 가능한 프로토콜을 차단한다.
export function safeMarkdownUrl(value: string): string | undefined {
  const url = value.trim();
  if (
    !url ||
    Array.from(url).some((character) => {
      const point = character.charCodeAt(0);
      return point <= 31 || point === 127;
    })
  )
    return undefined;
  try {
    const parsed = new URL(url, 'https://local.invalid');
    if (!['http:', 'https:', 'mailto:'].includes(parsed.protocol))
      return undefined;
    return url;
  } catch {
    return undefined;
  }
}
