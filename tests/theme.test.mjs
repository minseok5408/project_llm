import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { runInNewContext } from 'node:vm';
import {
  THEME_BOOTSTRAP_SCRIPT,
  THEME_STORAGE_KEY,
  ThemeStore,
  resolveTheme,
  themePreference,
} from '../features/preferences/state/theme.ts';

function harness({ stored = null, dark = false, blocked = false } = {}) {
  const systemListeners = new Set();
  const storageListeners = new Set();
  const applied = [];
  const writes = [];
  const environment = {
    read: () => {
      if (blocked) throw new Error('저장소 접근 차단');
      return stored;
    },
    write: (value) => {
      if (blocked) throw new Error('저장소 접근 차단');
      writes.push(value);
      stored = value;
    },
    systemDark: () => dark,
    apply: (preference, resolved) => applied.push({ preference, resolved }),
    watchSystem: (listener) => {
      systemListeners.add(listener);
      return () => systemListeners.delete(listener);
    },
    watchStorage: (listener) => {
      storageListeners.add(listener);
      return () => storageListeners.delete(listener);
    },
  };
  const store = new ThemeStore(() => environment);
  return {
    store,
    applied,
    writes,
    systemListeners,
    storageListeners,
    system(value) {
      dark = value;
      systemListeners.forEach((listener) => listener());
    },
    external(value) {
      stored = value;
      storageListeners.forEach((listener) => listener(value));
    },
  };
}

describe('테마 선택과 브라우저 동기화', () => {
  it('알 수 없는 저장값은 시스템 설정으로 처리하고 명시적 선택은 OS보다 우선한다', () => {
    for (const value of [null, undefined, '', 'invalid', {}, 'system'])
      assert.equal(themePreference(value), 'system');
    assert.equal(resolveTheme('system', true), 'dark');
    assert.equal(resolveTheme('system', false), 'light');
    assert.equal(resolveTheme('light', true), 'light');
    assert.equal(resolveTheme('dark', false), 'dark');
  });

  it('서버와 최초 렌더는 system이고 연결 뒤 저장한 선택을 복원한다', () => {
    const { store, applied } = harness({ stored: 'dark' });
    assert.equal(store.getSnapshot(), 'system');
    assert.equal(store.getServerSnapshot(), 'system');
    const changes = [];
    const unsubscribe = store.subscribe(() =>
      changes.push(store.getSnapshot()),
    );
    const disconnect = store.connect();
    assert.equal(store.getSnapshot(), 'dark');
    assert.equal(store.getServerSnapshot(), 'system');
    assert.deepEqual(applied.at(-1), { preference: 'dark', resolved: 'dark' });
    assert.deepEqual(changes, ['dark']);
    disconnect();
    unsubscribe();
  });

  it('시스템 변경은 system에서만 반영하고 선택 변경을 즉시 저장한다', () => {
    const state = harness();
    const disconnect = state.store.connect();
    state.system(true);
    assert.deepEqual(state.applied.at(-1), {
      preference: 'system',
      resolved: 'dark',
    });
    state.store.setPreference('light');
    const appliedCount = state.applied.length;
    state.system(false);
    state.system(true);
    assert.equal(state.applied.length, appliedCount);
    assert.deepEqual(state.applied.at(-1), {
      preference: 'light',
      resolved: 'light',
    });
    state.store.setPreference('system');
    assert.deepEqual(state.applied.at(-1), {
      preference: 'system',
      resolved: 'dark',
    });
    assert.deepEqual(state.writes, ['light', 'system']);
    disconnect();
  });

  it('저장소 읽기·쓰기 실패에도 OS 기본값과 현재 탭 선택이 유지된다', () => {
    const state = harness({ dark: true, blocked: true });
    const disconnect = state.store.connect();
    assert.deepEqual(state.applied.at(-1), {
      preference: 'system',
      resolved: 'dark',
    });
    state.store.setPreference('light');
    assert.deepEqual(state.applied.at(-1), {
      preference: 'light',
      resolved: 'light',
    });
    disconnect();
    const reconnect = state.store.connect();
    assert.equal(state.store.getSnapshot(), 'light');
    assert.deepEqual(state.applied.at(-1), {
      preference: 'light',
      resolved: 'light',
    });
    reconnect();
  });

  it('다른 탭 변경·삭제는 저장소에 다시 쓰지 않고 화면과 설정에 반영한다', () => {
    const state = harness({ dark: true });
    const disconnect = state.store.connect();
    state.external('light');
    assert.equal(state.store.getSnapshot(), 'light');
    assert.deepEqual(state.applied.at(-1), {
      preference: 'light',
      resolved: 'light',
    });
    state.external('invalid');
    assert.equal(state.store.getSnapshot(), 'system');
    assert.deepEqual(state.applied.at(-1), {
      preference: 'system',
      resolved: 'dark',
    });
    state.external('dark');
    state.external(null);
    assert.equal(state.store.getSnapshot(), 'system');
    assert.deepEqual(state.writes, []);
    disconnect();
  });

  it('전역 동기화와 설정 화면이 함께 연결돼도 리스너를 중복 등록하지 않는다', () => {
    const state = harness();
    const first = state.store.connect();
    const second = state.store.connect();
    assert.equal(state.systemListeners.size, 1);
    assert.equal(state.storageListeners.size, 1);
    first();
    first();
    assert.equal(state.systemListeners.size, 1);
    state.system(true);
    assert.equal(state.applied.at(-1).resolved, 'dark');
    second();
    assert.equal(state.systemListeners.size, 0);
    assert.equal(state.storageListeners.size, 0);
    const count = state.applied.length;
    state.system(false);
    assert.equal(state.applied.length, count);
  });

  it('브라우저 연결은 해당 localStorage 이벤트만 적용하고 리스너를 정리한다', () => {
    const previousWindow = Object.getOwnPropertyDescriptor(
      globalThis,
      'window',
    );
    const previousDocument = Object.getOwnPropertyDescriptor(
      globalThis,
      'document',
    );
    const storageListeners = new Set();
    const systemListeners = new Set();
    const applied = [];
    const storage = { getItem: () => null, setItem() {} };
    const media = {
      matches: true,
      addEventListener: (_type, listener) => systemListeners.add(listener),
      removeEventListener: (_type, listener) =>
        systemListeners.delete(listener),
    };
    const root = {
      dataset: {},
      classList: { toggle: (_name, enabled) => applied.push(enabled) },
    };
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: {
        localStorage: storage,
        matchMedia: () => media,
        addEventListener: (_type, listener) => storageListeners.add(listener),
        removeEventListener: (_type, listener) =>
          storageListeners.delete(listener),
      },
    });
    Object.defineProperty(globalThis, 'document', {
      configurable: true,
      value: { documentElement: root },
    });
    let disconnect;
    try {
      const store = new ThemeStore();
      disconnect = store.connect();
      const notify = (event) =>
        storageListeners.forEach((listener) => listener(event));
      assert.equal(root.dataset.theme, 'system');
      assert.equal(applied.at(-1), true);
      notify({ key: 'unrelated', newValue: 'light', storageArea: storage });
      notify({ key: THEME_STORAGE_KEY, newValue: 'light', storageArea: {} });
      assert.equal(store.getSnapshot(), 'system');
      notify({
        key: THEME_STORAGE_KEY,
        newValue: 'light',
        storageArea: storage,
      });
      assert.equal(root.dataset.theme, 'light');
      assert.equal(applied.at(-1), false);
      notify({ key: null, newValue: null, storageArea: storage });
      assert.equal(root.dataset.theme, 'system');
      assert.equal(applied.at(-1), true);
      media.matches = false;
      systemListeners.forEach((listener) => listener());
      assert.equal(applied.at(-1), false);
      disconnect();
      assert.equal(storageListeners.size, 0);
      assert.equal(systemListeners.size, 0);
    } finally {
      disconnect?.();
      if (previousWindow)
        Object.defineProperty(globalThis, 'window', previousWindow);
      else delete globalThis.window;
      if (previousDocument)
        Object.defineProperty(globalThis, 'document', previousDocument);
      else delete globalThis.document;
    }
  });
});

describe('첫 화면 테마 적용', () => {
  for (const example of [
    { stored: null, systemDark: true, expected: 'dark', preference: 'system' },
    {
      stored: 'light',
      systemDark: true,
      expected: 'light',
      preference: 'light',
    },
    { stored: 'dark', systemDark: false, expected: 'dark', preference: 'dark' },
    {
      stored: '<script>invalid</script>',
      systemDark: false,
      expected: 'light',
      preference: 'system',
    },
    { blocked: true, systemDark: true, expected: 'dark', preference: 'system' },
  ]) {
    it(`${example.blocked ? '저장소 차단' : example.preference} 초기 설정에서 ${example.expected} 테마를 적용한다`, () => {
      let darkClass;
      const root = {
        dataset: {},
        classList: {
          toggle(name, enabled) {
            assert.equal(name, 'dark');
            darkClass = enabled;
          },
        },
      };
      runInNewContext(THEME_BOOTSTRAP_SCRIPT, {
        document: { documentElement: root },
        window: {
          localStorage: {
            getItem(key) {
              assert.equal(key, THEME_STORAGE_KEY);
              if (example.blocked) throw new Error('저장소 접근 차단');
              return example.stored;
            },
          },
          matchMedia(query) {
            assert.equal(query, '(prefers-color-scheme: dark)');
            return { matches: example.systemDark };
          },
        },
      });
      assert.equal(root.dataset.theme, example.preference);
      assert.equal(darkClass, example.expected === 'dark');
    });
  }
});
