import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { copyText } from '../components/content/clipboard.ts';

function legacyDocument({
  succeeds = true,
  throws = false,
  input = false,
} = {}) {
  const events = [];
  const ranges = [{ cloneRange: () => '저장한 선택 범위' }];
  const selection = {
    rangeCount: 1,
    getRangeAt: (index) => ranges[index],
    removeAllRanges: () => events.push('선택 해제'),
    addRange: (range) => events.push(['선택 복원', range]),
  };
  const active = {
    focus: (options) => events.push(['초점 복원', options]),
    ...(input
      ? {
          selectionStart: 2,
          selectionEnd: 5,
          selectionDirection: 'backward',
          setSelectionRange: (...args) =>
            events.push(['입력 선택 복원', ...args]),
        }
      : {}),
  };
  const textarea = {
    value: '',
    style: {},
    setAttribute: () => {},
    focus: (options) => events.push(['복사 초점', options]),
    select: () => events.push('복사 선택'),
    setSelectionRange: (...args) => events.push(['복사 범위', ...args]),
    remove: () => events.push('임시 입력 제거'),
  };
  const document = {
    activeElement: active,
    getSelection: () => selection,
    body: { append: (element) => assert.equal(element, textarea) },
    createElement: (name) => {
      assert.equal(name, 'textarea');
      return textarea;
    },
    execCommand: (command) => {
      assert.equal(command, 'copy');
      events.push(['복사', textarea.value]);
      if (throws) throw new Error('브라우저 복사 거절');
      return succeeds;
    },
  };
  return { document, events, textarea };
}

describe('메시지와 코드 복사', () => {
  it('Clipboard API가 있으면 한글·들여쓰기·개행을 그대로 전달한다', async () => {
    const text = '# 답변\n```py\n    print("한글🌿")\n```\n';
    const writes = [];
    await copyText(text, {
      clipboard: { writeText: async (value) => writes.push(value) },
    });
    assert.deepEqual(writes, [text]);
  });
  it('같은 Wi-Fi HTTP에서 Clipboard API가 없어도 복사하고 선택·초점을 복원한다', async () => {
    const { document, events, textarea } = legacyDocument();
    await copyText('    코드\n', { document });
    assert.equal(textarea.value, '    코드\n');
    assert.equal(textarea.readOnly, true);
    assert.ok(
      events.some((event) => event[0] === '복사' && event[1] === '    코드\n'),
    );
    assert.deepEqual(events.slice(-4), [
      '임시 입력 제거',
      ['초점 복원', { preventScroll: true }],
      '선택 해제',
      ['선택 복원', '저장한 선택 범위'],
    ]);
  });
  it('Clipboard 권한 거부 때도 대체 복사를 시도하고 작성중 입력의 선택 위치를 보존한다', async () => {
    const { document, events } = legacyDocument({ input: true });
    await copyText('답변', {
      document,
      clipboard: {
        writeText: async () => {
          throw new Error('권한 거부');
        },
      },
    });
    assert.deepEqual(events.at(-1), ['입력 선택 복원', 2, 5, 'backward']);
  });
  it('대체 복사 실패를 성공으로 표시하지 않으며 임시 입력과 초점을 정리한다', async () => {
    for (const options of [{ succeeds: false }, { throws: true }]) {
      const { document, events } = legacyDocument(options);
      await assert.rejects(copyText('답변', { document }), /복사/);
      assert.ok(events.includes('임시 입력 제거'));
      assert.ok(events.some((event) => event[0] === '초점 복원'));
    }
    await assert.rejects(copyText('답변', {}), /사용할 수 없습니다/);
  });
});
