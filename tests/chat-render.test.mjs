import assert from 'node:assert/strict';
import { register } from 'node:module';
import { describe, it } from 'node:test';
import { createElement, Fragment } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { safeMarkdownUrl } from '../components/content/markdown-policy.ts';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { MarkdownText } =
  await import('../components/content/markdown-text.tsx');
const { ChatMessageView } =
  await import('../features/chat/components/chat-message-view.tsx');
const { NetworkModeSwitch } =
  await import('../features/network/components/network-mode-switch.tsx');
const { SearchSources } =
  await import('../features/network/components/search-sources.tsx');
const { ChatComposer } =
  await import('../features/chat/components/chat-composer.tsx');
const { ConversationSidebar } =
  await import('../features/chat/components/conversation-sidebar.tsx');
const { TokenUsagePanel } =
  await import('../features/usage/components/token-usage-panel.tsx');
const { AccountSettingsContent, TokenUsageDetails } =
  await import('../features/preferences/components/account-settings-dialog.tsx');
const { default: Home } = await import('../app/page.tsx');
const { default: ConversationPage } =
  await import('../app/chat/[conversationId]/page.tsx');
const markdown = (content) =>
  renderToStaticMarkup(createElement(MarkdownText, { content }));
const base = {
  userName: '김민석',
  streaming: false,
  cancelling: false,
  compacting: false,
  reducedMotion: false,
  lengthLimited: false,
  canSend: true,
  onRegenerate() {},
  message: {
    id: 'answer',
    role: 'assistant',
    sequence: 2,
    content: '**답변**',
    token_count: 12,
    status: 'completed',
    generation_status: 'completed',
    is_current: true,
    can_regenerate: true,
  },
};
const message = (props = {}) =>
  renderToStaticMarkup(createElement(ChatMessageView, { ...base, ...props }));

describe('기능별 화면 연결', () => {
  it('홈과 대화 주소는 서버 렌더링에서 같은 로그인 확인 화면을 제공한다', () => {
    const home = renderToStaticMarkup(createElement(Home));
    const conversation = renderToStaticMarkup(createElement(ConversationPage));
    assert.equal(home, conversation);
    assert.match(home, /로그인 확인 중/);
    assert.doesNotMatch(home, /채팅 메시지/);
  });

  it('분리한 입력창은 생성 중 잠금과 중단 중 초안 입력을 유지한다', () => {
    const props = {
      state: {
        stream: 'live',
        selected: null,
        sending: false,
        draft: '다음 질문',
        generation: { status: 'running' },
      },
      userName: '김민석',
      isGenerating: true,
      cancelling: false,
      compacting: false,
      searching: true,
      noBalance: false,
      logoutPending: false,
      canSend: false,
      thinking: false,
      maxTokens: 1024,
      onReconnect() {},
      onCancel() {},
      onSend() {},
      onDraftChange() {},
      onThinkingChange() {},
      onMaxTokensChange() {},
    };
    const searching = renderToStaticMarkup(createElement(ChatComposer, props));
    assert.match(searching, /웹에서 참고 자료를 찾는 중/);
    assert.match(searching, /<textarea\b[^>]*disabled=""/);
    const cancelling = renderToStaticMarkup(
      createElement(ChatComposer, { ...props, cancelling: true }),
    );
    assert.match(cancelling, /중단 확인 중/);
    assert.match(cancelling, /다음 질문/);
    assert.doesNotMatch(cancelling, /<textarea\b[^>]*disabled=""/);
    assert.match(
      cancelling,
      /<button\b[^>]*disabled=""[^>]*aria-label="중단 중\.\.\."/,
    );
    assert.doesNotMatch(cancelling, /<button\b[^>]*type="submit"/);
    assert.match(searching, /aria-label="답변 중단"/);
    assert.match(cancelling, /김민석/);
  });

  it('사이드바는 고정 순서와 목록에 아직 없는 선택 대화를 보존한다', () => {
    const selected = {
      id: 'selected',
      title: '선택한 대화',
      status: 'active',
      is_pinned: false,
      last_message_at: '2026-09-09T00:00:00Z',
      active_generation_id: 'running',
    };
    const conversations = [
      { ...selected, id: 'regular', title: '일반 대화' },
      { ...selected, id: 'pinned', title: '고정 대화', is_pinned: true },
    ];
    const rendered = renderToStaticMarkup(
      createElement(
        ConversationSidebar,
        {
          state: {
            filter: 'active',
            selected,
            listLoading: false,
            conversations,
            conversationCursor: null,
          },
          sidebarOpen: false,
          isGenerating: true,
          onClose() {},
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
        },
        '계정 사용량',
      ),
    );
    const ids = [...rendered.matchAll(/href="\/chat\/([^"]+)"/g)].map(
      (match) => match[1],
    );
    assert.deepEqual(ids, ['pinned', 'selected', 'regular']);
    assert.deepEqual(
      conversations.map((item) => item.id),
      ['regular', 'pinned'],
    );
    assert.match(rendered, /aria-current="page"/);
    assert.match(rendered, /응답 생성 중/);
    assert.match(rendered, /계정 사용량/);
  });

  it('사용량 설정은 월 무료 한도와 시스템 계정의 무제한 표시를 구분한다', () => {
    const props = {
      email: 'member@example.com',
      logoutPending: false,
      onRefresh() {},
      usage: {
        unlimited: false,
        remaining_tokens: 19000,
        used_tokens: 1000,
        token_limit: 20000,
        budget_source: 'free_monthly',
        ends_at: '2026-09-30T15:00:00Z',
      },
    };
    const member = renderToStaticMarkup(
      createElement(TokenUsageDetails, props),
    );
    assert.match(member, /남은 토큰/);
    assert.match(member, /19,000/);
    assert.match(member, /무료 플랜/);
    assert.match(member, /월 한도/);
    assert.match(member, /20,000/);
    assert.match(member, /한국시간/);
    const system = renderToStaticMarkup(
      createElement(TokenUsageDetails, {
        ...props,
        usage: { unlimited: true, used_tokens: 1000 },
      }),
    );
    assert.match(system, /한도 없음/);
    assert.match(system, /1,000/);
    assert.doesNotMatch(system, /월 한도|기간 한도|무료 · 매월/);
  });

  it('계정 메뉴와 일반·검색 설정은 토큰 수치를 렌더링하지 않고 사용량 탭에서만 표시한다', () => {
    const props = {
      email: 'member@example.com',
      userName: '김민석',
      logoutPending: false,
      onRefresh() {},
      onLogout() {},
      generalSettings: createElement('p', null, '화면 테마 선택'),
      networkSettings: createElement('p', null, '검색 방식 선택'),
      usage: {
        unlimited: false,
        remaining_tokens: 19000,
        used_tokens: 1000,
        token_limit: 20000,
        budget_source: 'free_monthly',
      },
    };
    const account = renderToStaticMarkup(createElement(TokenUsagePanel, props));
    assert.match(account, /김민석/);
    assert.match(account, /무료 플랜/);
    assert.match(account, /aria-haspopup="dialog"/);
    assert.match(account, /로그아웃/);
    assert.doesNotMatch(account, /모든 기기/);
    assert.doesNotMatch(
      account,
      /19,000|1,000|20,000|남은 토큰|화면 테마 선택|검색 방식 선택/,
    );
    const general = renderToStaticMarkup(
      createElement(AccountSettingsContent, props),
    );
    assert.match(general, /화면 테마 선택/);
    assert.doesNotMatch(
      general,
      /19,000|1,000|20,000|남은 토큰|검색 방식 선택/,
    );
    const network = renderToStaticMarkup(
      createElement(AccountSettingsContent, {
        ...props,
        initialTab: 'network',
      }),
    );
    assert.match(network, /검색 방식 선택/);
    assert.doesNotMatch(
      network,
      /19,000|1,000|20,000|남은 토큰|화면 테마 선택/,
    );
    const usage = renderToStaticMarkup(
      createElement(AccountSettingsContent, { ...props, initialTab: 'usage' }),
    );
    assert.match(usage, /19,000/);
    assert.match(usage, /1,000/);
    assert.match(usage, /20,000/);
    assert.match(usage, /role="tabpanel"/);
    assert.doesNotMatch(usage, /화면 테마 선택|검색 방식 선택/);
  });
});

describe('Markdown 답변 표시', () => {
  it('서로 다른 답변의 같은 각주 번호는 고유한 주소로 본문과 연결한다', () => {
    const rendered = renderToStaticMarkup(
      createElement(
        Fragment,
        null,
        createElement(MarkdownText, { content: '첫째[^1]\n\n[^1]: 첫 설명' }),
        createElement(MarkdownText, { content: '둘째[^1]\n\n[^1]: 둘째 설명' }),
      ),
    );
    const references = [
      ...rendered.matchAll(/id="(message-[^"]+-fnref-1)"/g),
    ].map((match) => match[1]);
    assert.equal(references.length, 2);
    assert.equal(new Set(references).size, 2);
    for (const id of references) assert.ok(rendered.includes(`href="#${id}"`));
    assert.match(rendered, /본문으로 돌아가기/);
  });
  it('제목·목록·인라인 코드·표를 의미 있는 HTML로 표시한다', () => {
    const rendered = markdown(
      '# 제목\n\n- **항목**과 `코드`\n\n| 이름 | 값 |\n| --- | --- |\n| 한글 | 42 |',
    );
    for (const text of [
      '<h1>제목</h1>',
      '<ul>',
      '<strong>항목</strong>',
      '<code>코드</code>',
      '<table>',
      '<td>한글</td>',
    ])
      assert.ok(rendered.includes(text), text);
    assert.ok(rendered.includes('aria-label="표"'));
  });
  it('코드블록은 공백·줄바꿈과 이스케이프를 보존하고 코드 복사를 제공한다', () => {
    const rendered = markdown(
      '```python\ndef greet():\n    return "<script>안녕</script>"\n```',
    );
    assert.match(rendered, /language-python/);
    assert.match(rendered, /코드 복사/);
    assert.match(
      rendered,
      /def greet\(\):\n    return &quot;&lt;script&gt;안녕&lt;\/script&gt;&quot;\n/,
    );
    assert.equal(rendered.includes('<script>'), false);
  });
  it('아직 닫히지 않은 코드블록도 스트리밍 본문을 안전하게 표시한다', () => {
    const rendered = markdown('먼저 실행하세요.\n\n```sh\necho "한글');
    assert.match(rendered, /language-sh/);
    assert.match(rendered, /echo &quot;한글/);
    assert.match(rendered, /코드 복사/);
  });
  it('원시 HTML·이벤트 속성·실행 프로토콜을 렌더링하지 않는다', () => {
    const rendered = markdown(
      '<script>alert(1)</script>\n\n<img src="https://tracker.invalid/a" onerror="alert(2)">\n\n[실행](javascript:alert%281%29) [파일](file:///etc/passwd) [문서](data:text/html,evil)',
    );
    for (const forbidden of [
      '<script',
      '<img',
      'onerror=',
      'href="javascript:',
      'href="file:',
      'href="data:',
    ])
      assert.equal(rendered.includes(forbidden), false, forbidden);
    assert.match(rendered, /실행/);
  });
  it('이미지는 자동 로드나 preload 없이 사용자가 선택할 링크로만 표시한다', () => {
    const rendered = markdown(
      '![다이어그램](https://images.invalid/private?q=secret)',
    );
    assert.match(rendered, /이미지: 다이어그램/);
    assert.match(rendered, /이미지 링크 열기/);
    assert.match(
      rendered,
      /href="https:\/\/images.invalid\/private\?q=secret"/,
    );
    assert.equal(rendered.includes('<img'), false);
    assert.equal(rendered.includes('<link'), false);
    assert.match(rendered, /rel="noopener noreferrer"/);
    assert.match(rendered, /referrerPolicy="no-referrer"/);
  });
  it('대소문자·제어문자를 섞은 유해 주소와 상대 경로를 구분한다', () => {
    for (const url of [
      'JaVaScRiPt:alert(1)',
      'java\nscript:alert(1)',
      '\u0000javascript:alert(1)',
      'vbscript:msgbox(1)',
      'data:text/html,test',
      'file:///tmp/a',
      'blob:https://example.com/id',
    ])
      assert.equal(safeMarkdownUrl(url), undefined, url);
    for (const url of [
      'https://example.com/a',
      'http://192.168.0.5/help',
      '/help',
      '#user-content-fn-1',
      'mailto:user@example.com',
    ])
      assert.equal(safeMarkdownUrl(url), url);
  });
});

describe('답변 작업과 이전 버전 표시', () => {
  it('완료된 현재 답변은 다시 생성과 복사, Markdown을 표시한다', () => {
    const rendered = message();
    assert.match(rendered, /<strong>답변<\/strong>/);
    assert.match(rendered, /답변 복사/);
    assert.match(rendered, /다시 생성/);
    assert.equal(rendered.includes('<details>'), false);
  });
  it('실패·중단 답변은 다시 시도로 표시하고 제한 중에는 비활성화한다', () => {
    for (const status of ['failed', 'cancelled']) {
      const rendered = message({
        message: { ...base.message, generation_status: status },
        canSend: false,
      });
      assert.match(rendered, /다시 시도/);
      assert.match(rendered, /<button type="button" disabled=""[^>]*title=/);
    }
  });
  it('이전 답변은 접어서 원문을 보존하고 재생성 버튼을 숨긴다', () => {
    const rendered = message({
      message: { ...base.message, is_current: false },
    });
    assert.match(rendered, /<details\b[^>]*><summary/);
    assert.match(rendered, /이전 답변/);
    assert.match(rendered, /<strong>답변<\/strong>/);
    assert.equal(rendered.includes('<details open'), false);
    assert.equal(rendered.includes('다시 생성'), false);
  });
  it('사용자 본문은 Markdown 원문으로 보존하고 사용자 이름을 표시한다', () => {
    const rendered = message({
      message: {
        ...base.message,
        role: 'user',
        content: '**강조** <img src=x>',
      },
    });
    assert.match(rendered, /김민석/);
    assert.match(rendered, /\*\*강조\*\* &lt;img src=x&gt;/);
    assert.equal(rendered.includes('<strong>'), false);
    assert.match(rendered, /메시지 복사/);
    assert.equal(rendered.includes('다시 생성'), false);
  });
  it('생성 중에는 재생성을 숨기고 길이 제한 안내는 완료 후 표시한다', () => {
    const active = message({ streaming: true });
    assert.match(active, /응답을 준비하는 중/);
    assert.equal(active.includes('다시 생성'), false);
    assert.equal(active.includes('답변 복사'), false);
    assert.match(message({ lengthLimited: true }), /이어서 말해/);
  });
});

describe('검색 상태와 출처의 안전한 표시', () => {
  const source = {
    number: 1,
    title: '공식 문서',
    url: 'https://example.com/docs',
    snippet: '검색 결과 설명',
    retrieved_at: '2026-09-09T01:00:00Z',
  };
  const search = {
    status: 'completed',
    reason: null,
    provider: 'brave',
    sources: [source],
  };
  const render = (data) =>
    renderToStaticMarkup(createElement(SearchSources, { search: data }));

  it('출처는 기본 접힘이며 펼친 목록에는 번호와 제목 링크만 표시한다', () => {
    const result = render(search);
    assert.match(result, /<details\b[^>]*><summary\b/);
    assert.doesNotMatch(result, /<details[^>]*\bopen(?:\s|=|>)/);
    assert.match(result, /참고한 검색 자료/);
    assert.match(result, /참고한 검색 자료 \(1\)/);
    assert.match(result, /\[1\]/);
    assert.match(result, /href="https:\/\/example.com\/docs"/);
    assert.match(result, /공식 문서/);
    assert.doesNotMatch(
      result,
      /검색 결과 설명|<p\b|<time\b|조회 \(한국시간\)/,
    );
    assert.match(result, /rel="noopener noreferrer"/);
    assert.match(result, /referrerPolicy="no-referrer"/);
    assert.doesNotMatch(result, /<img|<script|<link/);
  });

  it('검색 제목의 HTML은 문자로 처리하고 본문과 위험한 출처 링크는 표시하지 않는다', () => {
    for (const url of [
      'javascript:alert(1)',
      'data:text/html,evil',
      '//attacker.invalid',
      '/api/status',
      'mailto:test@example.com',
      'https://user:password@example.com',
    ]) {
      const result = render({
        ...search,
        sources: [
          {
            ...source,
            url,
            title: '<img src=x onerror=alert(1)>',
            snippet: '<script>alert(2)</script>',
          },
        ],
      });
      assert.doesNotMatch(result, /<a |<img|<script/);
      assert.match(result, /&lt;img/);
      assert.doesNotMatch(result, /&lt;script&gt;|alert\(2\)/);
    }
  });

  it('검색 실패·미설정·오프라인·결과 없음은 최신 정보 미확인으로 안내한다', () => {
    for (const [status, reason, label] of [
      ['unavailable', 'provider_unconfigured', '검색 서비스가 설정되지'],
      ['unavailable', 'offline', '인터넷 검색에 연결하지 못해'],
      ['failed', 'provider_error', '웹검색에 실패해'],
      ['no_results', null, '관련 검색 결과가 없어'],
      ['cancelled', 'local_only', '웹검색이 중단'],
      ['omitted', 'context_limit', '이번 답변에 포함하지 못했'],
      ['disabled', 'mode_changed', '설정이 변경되어 웹검색을 중단'],
      ['disabled', 'forced_local', '로컬 전용 모드로 웹검색을 사용하지 않았'],
      ['disabled', 'search_off', '웹검색을 끄고 로컬 지식으로'],
    ]) {
      const result = render({ ...search, status, reason, sources: [] });
      assert.ok(result.includes(label));
      assert.match(result, /최신 정보는 확인하지 못했습니다/);
    }
    assert.equal(render({ ...search, status: 'disabled', sources: [] }), '');
  });

  it('검색 중에도 답변 중단 상태를 우선 표시하고 재생성한 이전 답변의 출처를 보존한다', () => {
    const active = message({
      streaming: true,
      searching: true,
      search: { ...search, status: 'searching', sources: [] },
    });
    assert.match(active, />검색 중<\/p>/);
    assert.match(active, /웹에서 참고 자료를 찾는 중/);
    assert.doesNotMatch(active, /다시 생성/);
    const stopped = message({
      streaming: true,
      searching: true,
      cancelling: true,
    });
    assert.match(stopped, />중단 중<\/p>/);
    const old = message({
      message: { ...base.message, is_current: false, search },
    });
    assert.match(old, /<details\b[^>]*><summary/);
    assert.match(old, /공식 문서/);
  });
});

describe('네트워크 모드 화면', () => {
  const value = {
    local_only: false,
    revision: 1,
    mode: 'online',
    reason: 'available',
    search_configured: true,
    checked_at: null,
  };
  const state = {
    value,
    loading: false,
    saving: false,
    checking: false,
    webSearch: 'auto',
    error: null,
  };
  const render = (patch = {}) =>
    renderToStaticMarkup(
      createElement(NetworkModeSwitch, {
        state: { ...state, ...patch },
        onLocalOnly() {},
        onCheck() {},
        onWebSearch() {},
      }),
    );

  it('기본은 로컬 전용 OFF이며 자동·항상 검색·검색 안 함을 제공한다', () => {
    const result = render();
    assert.match(result, /role="switch" aria-checked="false"/);
    assert.match(result, /로컬 전용 OFF/);
    assert.match(result, /온라인/);
    assert.match(result, /value="auto" selected=""/);
    assert.match(result, /항상 검색/);
    assert.match(result, /검색 안 함/);
    assert.match(result, /외부 검색 서비스에 전달/);
  });

  it('로컬 전용 ON은 검색 선택을 막고 외부 연결 확인도 하지 않음을 설명한다', () => {
    const result = render({
      value: {
        ...value,
        local_only: true,
        mode: 'local',
        reason: 'forced_local',
      },
    });
    assert.match(result, /role="switch" aria-checked="true"/);
    assert.match(result, /<select[^>]*disabled=""/);
    assert.match(result, /웹검색과 외부 연결 확인을 사용하지 않습니다/);
  });

  it('설정 오류 때 실제 OFF를 보존하면서 로컬 동작과 저장 실패를 안내한다', () => {
    const result = render({ error: '설정을 저장하지 못했습니다.' });
    assert.match(result, /aria-checked="false"/);
    assert.match(result, /role="alert"/);
    assert.match(result, /로컬로 답합니다/);
    assert.match(result, /설정을 저장하지 못했/);
    assert.doesNotMatch(result, />온라인</);
  });
});
