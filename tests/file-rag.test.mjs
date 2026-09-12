import assert from 'node:assert/strict';
import { register } from 'node:module';
import { it } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ConversationSession } from '../features/chat/state/conversation-session.ts';
import { validFileSources } from '../features/files/types.ts';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { FileAttachments } =
  await import('../features/files/components/file-attachments.tsx');
const { FileSources } =
  await import('../features/files/components/file-sources.tsx');
const id = '11111111-1111-4111-8111-111111111111';
const source = {
  document_id: id,
  chunk_id: id,
  filename: '<script>문서</script>.txt',
  page: 2,
  chunk: 3,
  start: 10,
  end: 70,
  number: 1,
};
const file = {
  id,
  version_id: id,
  filename: '정책.txt',
  status: 'ready',
  byte_size: 50,
  pages: 1,
  chunks: 1,
  attempts: 1,
  error: null,
  can_delete: true,
  can_retry: false,
  dead_letter: false,
};

it('파일 근거는 검증한 로컬 ID만 사용하고 파일명은 HTML로 실행하지 않는다', () => {
  assert.ok(validFileSources([source]));
  assert.equal(
    validFileSources([{ ...source, document_id: '../../secret' }]),
    false,
  );
  assert.equal(validFileSources([{ ...source, page: -1 }]), false);
  const html = renderToStaticMarkup(
    createElement(FileSources, { sources: [source], conversationId: id }),
  );
  assert.match(html, /파일 근거 1개/);
  assert.match(html, /2페이지 · 조각 3/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|href="https?:|<pre/);
});

it('처리 단계와 재시도 상한·삭제 권한을 구분한다', () => {
  const render = (files) =>
    renderToStaticMarkup(
      createElement(FileAttachments, {
        files,
        busy: false,
        error: null,
        onDelete() {},
        onRetry() {},
      }),
    );
  assert.match(render([{ ...file, status: 'indexing' }]), /검색 준비 중/);
  const failed = render([
    {
      ...file,
      status: 'failed',
      can_retry: true,
      error: '처리가 중단되었습니다.',
    },
  ]);
  assert.match(failed, /재시도/);
  const dead = render([
    {
      ...file,
      status: 'failed',
      can_retry: false,
      can_delete: false,
      dead_letter: true,
      error: '처리가 중단되었습니다.',
    },
  ]);
  assert.match(dead, /재시도 한도/);
  assert.doesNotMatch(dead, /<button/);
});

it('새 대화에서 파일을 첨부해도 작성한 질문을 유지하며 준비 중 전송을 막는다', async () => {
  const requests = [];
  let ready = false;
  const request = async (path, init = {}) => {
    requests.push({ path, init });
    if (path === '/api/v1/conversations')
      return Response.json({ id, workspace_id: id, status: 'active' });
    if (path.endsWith('/files') && init.method === 'POST') {
      assert.equal(init.body instanceof File, true);
      assert.equal(init.headers['X-File-Name'], encodeURIComponent('정책.txt'));
      return Response.json({ ...file, status: 'uploaded' }, { status: 201 });
    }
    if (path.endsWith('/files'))
      return Response.json({
        enabled: true,
        items: [{ ...file, status: ready ? 'ready' : 'indexing' }],
      });
    return Response.json({ items: [], next_cursor: null });
  };
  const session = new ConversationSession(request);
  session.seed({
    loading: false,
    workspaceId: id,
    draft: '파일에서 휴가 정책을 알려줘',
  });
  assert.equal(
    await session.uploadFile(
      new File(['정책 본문'], '정책.txt', { type: 'text/plain' }),
    ),
    true,
  );
  assert.equal(session.getSnapshot().draft, '파일에서 휴가 정책을 알려줘');
  assert.equal(await session.send('질문', { thinking: false }), false);
  assert.equal(
    requests.some(({ path }) => path.endsWith('/messages')),
    false,
  );
  ready = true;
  await session.refreshFiles();
  assert.equal(session.getSnapshot().files[0].status, 'ready');
  session.dispose();
});

it('계정·대화 해제 뒤 늦게 도착한 파일 응답을 반영하지 않는다', async () => {
  let finish;
  const session = new ConversationSession(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  session.seed({
    loading: false,
    workspaceId: id,
    selected: { id, workspace_id: id, status: 'active' },
  });
  const refresh = session.refreshFiles();
  session.dispose();
  finish(Response.json({ enabled: true, items: [file] }));
  await refresh;
  assert.equal(session.getSnapshot().files.length, 0);
});

it('파일 삭제 후 목록과 저장된 출처를 다시 읽고 질문 원문은 보존한다', async () => {
  const calls = [];
  const savedMessage = {
    id: 'answer',
    role: 'assistant',
    sequence: 2,
    content: '기존 답변',
    file_sources: [{ ...source, available: false }],
  };
  const session = new ConversationSession(async (path, init = {}) => {
    calls.push([path, init.method ?? 'GET']);
    if (init.method === 'DELETE') {
      assert.equal(init.body, '{}');
      return new Response(null, { status: 204 });
    }
    if (path.endsWith('/files'))
      return Response.json({ enabled: true, items: [] });
    if (path.includes('/messages?'))
      return Response.json({ items: [savedMessage], next_cursor: null });
    assert.fail('예상하지 못한 파일 요청입니다.');
  });
  session.seed({
    loading: false,
    workspaceId: id,
    draft: '작성 중인 질문',
    files: [file],
    selected: { id, workspace_id: id, status: 'active' },
    messages: [{ ...savedMessage, file_sources: [source] }],
  });
  try {
    assert.equal(await session.changeFile(id, 'delete'), true);
    assert.deepEqual(session.getSnapshot().files, []);
    assert.equal(
      session.getSnapshot().messages[0].file_sources[0].available,
      false,
    );
    assert.equal(session.getSnapshot().messages[0].content, '기존 답변');
    assert.equal(session.getSnapshot().draft, '작성 중인 질문');
    assert.deepEqual(calls, [
      [`/api/v1/conversations/${id}/files/${id}`, 'DELETE'],
      [`/api/v1/conversations/${id}/files`, 'GET'],
      [`/api/v1/conversations/${id}/messages?limit=50`, 'GET'],
    ]);
  } finally {
    session.dispose();
  }
});

it('계정을 닫은 뒤 완료된 업로드·삭제는 새 파일 조회나 화면 갱신을 시작하지 않는다', async () => {
  for (const operation of ['upload', 'delete']) {
    const calls = [];
    let finish;
    const session = new ConversationSession((path, init = {}) => {
      calls.push([path, init.method ?? 'GET']);
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    session.seed({
      loading: false,
      workspaceId: id,
      files: [file],
      selected: { id, workspace_id: id, status: 'active' },
    });
    const pending =
      operation === 'upload'
        ? session.uploadFile(
            new File(['본문'], '정책.txt', { type: 'text/plain' }),
          )
        : session.changeFile(id, 'delete');
    await new Promise((resolve) => setImmediate(resolve));
    session.dispose();
    finish(Response.json({}));
    assert.equal(await pending, false);
    assert.equal(calls.length, 1);
    assert.deepEqual(session.getSnapshot().files, []);
    assert.equal(session.getSnapshot().selected, null);
  }
});

it('동시에 조회한 파일 목록은 나중 요청만 반영하고 준비 중 폴링은 대화 해제 시 취소한다', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const responses = [];
  const session = new ConversationSession(
    () => new Promise((resolve) => responses.push(resolve)),
  );
  session.seed({
    loading: false,
    workspaceId: id,
    selected: { id, workspace_id: id, status: 'active' },
  });
  try {
    const older = session.refreshFiles();
    const newer = session.refreshFiles();
    responses[1](Response.json({ enabled: true, items: [file] }));
    await newer;
    responses[0](
      Response.json({
        enabled: true,
        items: [{ ...file, status: 'indexing' }],
      }),
    );
    await older;
    assert.equal(session.getSnapshot().files[0].status, 'ready');
    context.mock.timers.tick(2000);
    assert.equal(responses.length, 2);
    const processing = session.refreshFiles();
    responses[2](
      Response.json({
        enabled: true,
        items: [{ ...file, status: 'indexing' }],
      }),
    );
    await processing;
    session.newDraft();
    context.mock.timers.tick(2000);
    assert.equal(responses.length, 3);
    assert.deepEqual(session.getSnapshot().files, []);
  } finally {
    session.dispose();
  }
});
