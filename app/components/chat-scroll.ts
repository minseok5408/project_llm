export type ScrollPosition = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

const BOTTOM_THRESHOLD = 48;

export function bottomPosition(position: ScrollPosition) {
  return Math.max(0, position.scrollHeight - position.clientHeight);
}

export function preserveScrollAnchor(
  scrollTop: number,
  previousOffset: number,
  currentOffset: number,
) {
  return Math.max(0, scrollTop + currentOffset - previousOffset);
}

export class ChatScrollFollow {
  following = true;
  private previousTop = 0;

  pause() {
    this.following = false;
  }

  resume() {
    this.following = true;
  }

  recordPosition(scrollTop: number) {
    this.previousTop = scrollTop;
  }

  onScroll(position: ScrollPosition) {
    const movement = position.scrollTop - this.previousTop;
    // 바닥 근처라도 위로 읽으려는 움직임을 먼저 존중한다.
    if (movement < -0.5) this.pause();
    else if (
      movement > 0.5 &&
      bottomPosition(position) - position.scrollTop <= BOTTOM_THRESHOLD
    )
      this.resume();
    this.recordPosition(position.scrollTop);
  }

  targetAfterResize(position: ScrollPosition) {
    return this.following ? bottomPosition(position) : null;
  }

  hasNewerContent(position: ScrollPosition) {
    return !this.following && bottomPosition(position) - position.scrollTop > 4;
  }
}
