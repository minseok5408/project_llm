import assert from 'node:assert/strict';
import { register } from 'node:module';
import { it } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { sidebarReturnFocus } from '../features/chat/hooks/sidebar-focus.ts';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { ConversationSidebar } =
  await import('../features/chat/components/conversation-sidebar.tsx');
const { ConversationRowActions } =
  await import('../features/chat/components/conversation-row-actions.tsx');

const conversation = (id, extra = {}) => ({
  id,
  workspace_id: 'workspace',
  title: `대화 ${id}`,
  status: 'active',
  is_pinned: false,
  last_message_at: '2026-09-12T00:00:00Z',
  ...extra,
});
const callbacks = {
  onClose() {},
  onMobileClose() {},
  onExpand() {},
  onNewChat() {},
  onSearch() {},
  onFilterChange() {},
  onOpenConversation() {},
  onRename() {},
  onTogglePin() {},
  onToggleArchive() {},
  onDelete() {},
  onLoadMore() {},
};
const walk = (node, match) => {
  if (!node || typeof node !== 'object') return [];
  return [
    ...(match(node) ? [node] : []),
    ...[node.props?.children]
      .flat(Infinity)
      .flatMap((child) => walk(child, match)),
  ];
};

it('선택하지 않은 대화도 관리 메뉴를 제공하고 생성 중인 행만 삭제와 보관을 막는다', () => {
  const selected = conversation('selected');
  const other = conversation('other');
  const running = conversation('running', {
    active_generation_id: 'generation',
  });
  const html = renderToStaticMarkup(
    createElement(
      ConversationSidebar,
      {
        ...callbacks,
        state: {
          filter: 'active',
          selected,
          listLoading: false,
          conversations: [selected, other, running],
          conversationCursor: null,
          tasks: [],
        },
        sidebarOpen: false,
        isGenerating: true,
      },
      createElement('p', null, '계정 표시 하나'),
    ),
  );
  assert.equal((html.match(/<aside\b/g) ?? []).length, 1);
  assert.equal((html.match(/계정 표시 하나/g) ?? []).length, 1);
  for (const item of [selected, other, running])
    assert.ok(html.includes(`aria-label="${item.title} 대화 관리"`));
  const menus = [...html.matchAll(/<details\b[\s\S]*?<\/details>/g)].map(
    ([value]) => value,
  );
  assert.equal(menus.length, 3);
  assert.equal((menus[0].match(/\sdisabled=""/g) ?? []).length, 2);
  assert.equal((menus[1].match(/\sdisabled=""/g) ?? []).length, 0);
  assert.equal((menus[2].match(/\sdisabled=""/g) ?? []).length, 2);
});

it('모든 행 작업은 대화를 열지 않고 그 행의 객체를 전달하며 메뉴 시작점에 초점을 남긴다', () => {
  const item = conversation('other');
  const calls = [];
  const names = ['onRename', 'onTogglePin', 'onToggleArchive', 'onDelete'];
  const tree = ConversationRowActions({
    conversation: item,
    selected: false,
    generating: false,
    ...Object.fromEntries(
      names.map((name) => [name, (target) => calls.push([name, target])]),
    ),
  });
  const buttons = walk(
    tree,
    (node) => typeof node.props?.onClick === 'function',
  );
  let focuses = 0;
  const menu = {
    open: true,
    querySelector: () => ({
      focus: () => {
        focuses += 1;
      },
    }),
  };
  for (const button of buttons) {
    menu.open = true;
    button.props.onClick({ currentTarget: { closest: () => menu } });
    assert.equal(menu.open, false);
  }
  assert.deepEqual(
    calls,
    names.map((name) => [name, item]),
  );
  assert.equal(focuses, 4);
});

const element = (name, visible = true) => ({
  name,
  isConnected: true,
  getClientRects: () => (visible ? [{}] : []),
});
function focusDocument(
  t,
  { dialogs = [], trigger = null, selected = null, input = null } = {},
) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'document');
  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: {
      querySelectorAll: () => dialogs,
      querySelector: (selector) =>
        selector === '[data-chat-sidebar-trigger]' ? trigger : selected,
      getElementById: () => input,
    },
  });
  t.after(() =>
    previous
      ? Object.defineProperty(globalThis, 'document', previous)
      : delete globalThis.document,
  );
}

it('모바일 목록이 닫히면 숨겨진 행 대신 열기 버튼에 초점을 돌린다', (t) => {
  const trigger = element('열기');
  focusDocument(t, { trigger, input: element('입력') });
  assert.equal(sidebarReturnFocus(element('숨겨진 행', false)), trigger);
});

it('데스크톱에서는 모바일 버튼을 건너뛰고 보이는 행이나 입력으로 복귀한다', (t) => {
  const selected = element('선택 대화');
  focusDocument(t, {
    trigger: element('열기', false),
    selected,
    input: element('입력'),
  });
  assert.equal(sidebarReturnFocus(null), selected);
  const original = element('관리 시작점');
  assert.equal(sidebarReturnFocus(original), original);
});

it('다른 검색 모달의 초점을 빼앗지 않고 중첩 설정은 부모 목록 안으로 복귀한다', (t) => {
  const account = element('계정');
  const dialog = { contains: (target) => target === account };
  focusDocument(t, { dialogs: [dialog], trigger: element('열기') });
  assert.equal(sidebarReturnFocus(element('본문')), false);
  assert.equal(sidebarReturnFocus(account), account);
});
