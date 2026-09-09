const INTERVAL_MS = 10;
const MAX_BACKLOG = 128;
const CATCH_UP_THRESHOLD = 32;

// 마지막 묶음은 다음 조각과 합쳐질 수 있어 스트림이 끝날 때까지 보류한다.
export class GraphemeTyper {
  private segmenter = new Intl.Segmenter('ko', { granularity: 'grapheme' });
  private text: string;
  private visible: string;
  private tail = '';
  private queue: string[] = [];
  private timer: ReturnType<typeof setTimeout> | undefined;
  private frozen = false;
  private listeners = new Set<() => void>();

  constructor(initial = '') {
    this.text = initial;
    this.visible = initial;
  }
  getSnapshot = () => this.visible;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private emit() {
    for (const listener of this.listeners) listener();
  }
  private clearTimer() {
    clearTimeout(this.timer);
    this.timer = undefined;
  }
  private schedule() {
    if (this.timer !== undefined || this.frozen || !this.queue.length) return;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      if (this.frozen) return;
      const count = Math.max(
        1,
        Math.ceil(this.queue.length / CATCH_UP_THRESHOLD),
      );
      this.visible += this.queue.splice(0, count).join('');
      this.emit();
      this.schedule();
    }, INTERVAL_MS);
  }
  update(text: string) {
    if (this.frozen) return;
    if (!text.startsWith(this.text)) {
      this.clearTimer();
      this.visible = '';
      this.text = '';
      this.tail = '';
      this.queue = [];
      this.emit();
    }
    const incoming = this.tail + text.slice(this.text.length);
    this.text = text;
    const parts = Array.from(
      this.segmenter.segment(incoming),
      (part) => part.segment,
    );
    this.tail = parts.pop() ?? '';
    this.queue = this.queue.concat(parts);
    if (this.queue.length > MAX_BACKLOG) {
      // 긴 답변의 표시 지연이 계속 쌓이지 않도록 오래된 묶음부터 따라잡는다.
      this.visible += this.queue
        .splice(0, this.queue.length - MAX_BACKLOG)
        .join('');
      this.emit();
    }
    this.schedule();
  }
  freeze() {
    this.frozen = true;
    this.clearTimer();
    this.queue = [];
    this.tail = '';
    this.text = this.visible;
  }
  resume(text: string) {
    this.frozen = false;
    this.update(text);
  }
  flushStable() {
    this.clearTimer();
    if (this.frozen || !this.queue.length) return;
    this.visible += this.queue.join('');
    this.queue = [];
    this.emit();
  }
  complete(text: string) {
    this.clearTimer();
    if (this.frozen) return;
    this.queue = [];
    this.tail = '';
    this.text = text;
    if (this.visible !== text) {
      this.visible = text;
      this.emit();
    }
  }
  dispose() {
    this.freeze();
  }
}
