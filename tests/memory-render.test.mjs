import assert from 'node:assert/strict';
import { register } from 'node:module';
import { it } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { AccountSettingsContent } =
  await import('../features/preferences/components/account-settings-dialog.tsx');
const { MemorySettings } =
  await import('../features/memory/components/memory-settings.tsx');

it('설정의 기억 탭에 명시적인 저장 화면과 접근성 이름이 연결된다', () => {
  const html = renderToStaticMarkup(
    createElement(AccountSettingsContent, {
      usage: null,
      onRefresh() {},
      initialTab: 'memory',
      memorySettings: createElement(MemorySettings, {
        request: async () => {
          throw new Error('SSR에서 조회하지 않음');
        },
      }),
    }),
  );
  assert.match(html, /aria-selected="true"[^>]*>.*?기억/s);
  assert.match(html, /aria-label="저장된 기억"/);
  assert.match(html, /기억 이름/);
  assert.match(html, /기억할 내용/);
  assert.match(html, /대화 내용은 자동으로 저장되지 않습니다/);
  assert.match(html, /기억 저장/);
  assert.match(html, /maxLength="500"/);
  assert.match(html, /기억을 불러오는 중/);
});
