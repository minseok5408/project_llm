import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  ChatScrollFollow,
  bottomPosition,
  preserveScrollAnchor,
} from '../features/chat/scroll/chat-scroll.ts';

const position = (scrollTop, scrollHeight = 1200, clientHeight = 400) => ({
  scrollTop,
  scrollHeight,
  clientHeight,
});

describe('대화 스크롤 따라가기', () => {
  it('위로 스크롤하면 바닥 근처에서도 멈추고 이어지는 답변으로 내려가지 않는다', () => {
    const scroll = new ChatScrollFollow();
    scroll.recordPosition(800);
    scroll.onScroll(position(790));
    assert.equal(scroll.following, false);
    assert.equal(scroll.targetAfterResize(position(790, 1600)), null);
    assert.equal(scroll.hasNewerContent(position(790, 1600)), true);
  });

  it('위 방향 입력 직후 실제 스크롤 이벤트보다 먼저 따라가기를 멈춘다', () => {
    const scroll = new ChatScrollFollow();
    scroll.recordPosition(800);
    scroll.pause();
    scroll.onScroll(position(800));
    assert.equal(scroll.targetAfterResize(position(800, 1250)), null);
  });

  it('사용자가 아래로 이동해 바닥 근처에 도달하면 새 글자를 따라간다', () => {
    const scroll = new ChatScrollFollow();
    scroll.pause();
    scroll.recordPosition(200);
    scroll.onScroll(position(700));
    assert.equal(scroll.following, false);
    scroll.onScroll(position(760));
    assert.equal(scroll.following, true);
    assert.equal(scroll.targetAfterResize(position(760, 1500)), 1100);
  });

  it('최신 답변 버튼이나 새 질문은 명시적으로 따라가기를 다시 시작한다', () => {
    const scroll = new ChatScrollFollow();
    scroll.pause();
    scroll.recordPosition(200);
    scroll.resume();
    const target = scroll.targetAfterResize(position(200));
    assert.equal(target, 800);
    scroll.recordPosition(target);
    scroll.onScroll(position(800));
    assert.equal(scroll.following, true);
    assert.equal(scroll.hasNewerContent(position(800)), false);
  });

  it('이전 메시지 추가는 읽던 메시지 위치를 보존하고 답변 높이 변화는 무시한다', () => {
    const scroll = new ChatScrollFollow();
    scroll.pause();
    // 이전 메시지가 900px 추가되고 아래 답변이 더 길어져도 기준 메시지만 사용한다.
    const target = preserveScrollAnchor(120, -80, 820);
    assert.equal(target, 1020);
    scroll.recordPosition(target);
    scroll.onScroll(position(target, 3000));
    assert.equal(scroll.following, false);
    assert.equal(scroll.targetAfterResize(position(target, 3200)), null);
  });

  it('화면보다 짧은 대화와 소수점 스크롤에서도 불필요하게 상태를 바꾸지 않는다', () => {
    const scroll = new ChatScrollFollow();
    assert.equal(bottomPosition(position(0, 100)), 0);
    scroll.recordPosition(800);
    scroll.onScroll(position(799.75));
    assert.equal(scroll.following, true);
  });
});
