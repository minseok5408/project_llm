export type GenerationEvent = {
  id: number;
  event: string;
  data: Record<string, unknown>;
};

export function parseGenerationEvent(block: string): GenerationEvent | null {
  let id = 0;
  let event = 'message';
  const data: string[] = [];
  for (const line of block.split('\n')) {
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
    if (field === 'id' && /^\d+$/.test(value)) id = Number(value);
    if (field === 'event') event = value;
    if (field === 'data') data.push(value);
  }
  if (!Number.isSafeInteger(id) || id < 1 || !data.length) return null;
  try {
    const value: unknown = JSON.parse(data.join('\n'));
    if (!value || typeof value !== 'object' || Array.isArray(value))
      return null;
    return { id, event, data: value as Record<string, unknown> };
  } catch {
    return null;
  }
}

export async function consumeGenerationEvents(
  response: Response,
  onEvent: (event: GenerationEvent) => void,
) {
  if (!response.body) throw new Error('응답 스트림을 열 수 없습니다.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replaceAll('\r\n', '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const event = parseGenerationEvent(buffer.slice(0, boundary));
        if (event) onEvent(event);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }
    const event = parseGenerationEvent(buffer);
    if (event) onEvent(event);
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export function requestId() {
  if (globalThis.crypto.randomUUID) return globalThis.crypto.randomUUID();
  // HTTP LAN에서도 사용할 수 있는 난수 기반 UUID v4를 만든다.
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, '0'),
  ).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const terminalGeneration = (status: string) =>
  ['completed', 'failed', 'cancelled', 'usage_pending'].includes(status);
