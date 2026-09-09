export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'project-llm-theme';

// 사용자 값을 코드로 삽입하지 않고, 허용한 세 값만 첫 화면의 테마에 반영한다.
export const THEME_BOOTSTRAP_SCRIPT = `(function(){var theme='system';try{var stored=window.localStorage.getItem('project-llm-theme');if(stored==='light'||stored==='dark'||stored==='system')theme=stored;}catch{}var dark=theme==='dark'||(theme==='system'&&typeof window.matchMedia==='function'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',Boolean(dark));document.documentElement.dataset.theme=theme;})();`;

export function themePreference(value: unknown): ThemePreference {
  return value === 'light' || value === 'dark' ? value : 'system';
}

export function resolveTheme(
  preference: ThemePreference,
  systemDark: boolean,
): ResolvedTheme {
  return preference === 'dark' || (preference === 'system' && systemDark)
    ? 'dark'
    : 'light';
}

export type ThemeEnvironment = {
  read: () => unknown;
  write: (preference: ThemePreference) => void;
  systemDark: () => boolean;
  apply: (preference: ThemePreference, resolved: ResolvedTheme) => void;
  watchSystem: (listener: () => void) => () => void;
  watchStorage: (listener: (value: unknown) => void) => () => void;
};

function browserEnvironment(): ThemeEnvironment | null {
  if (typeof window === 'undefined' || typeof document === 'undefined')
    return null;
  const media =
    typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-color-scheme: dark)')
      : null;
  return {
    read: () => window.localStorage.getItem(THEME_STORAGE_KEY),
    write: (preference) =>
      window.localStorage.setItem(THEME_STORAGE_KEY, preference),
    systemDark: () => media?.matches ?? false,
    apply: (preference, resolved) => {
      document.documentElement.classList.toggle('dark', resolved === 'dark');
      document.documentElement.dataset.theme = preference;
    },
    watchSystem: (listener) => {
      media?.addEventListener('change', listener);
      return () => media?.removeEventListener('change', listener);
    },
    watchStorage: (listener) => {
      const update = (event: StorageEvent) => {
        if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
        try {
          if (event.storageArea && event.storageArea !== window.localStorage)
            return;
        } catch {
          // 저장소 접근이 차단돼도 다른 탭에서 받은 테마 선택은 반영한다.
        }
        listener(event.newValue);
      };
      window.addEventListener('storage', update);
      return () => window.removeEventListener('storage', update);
    },
  };
}

export class ThemeStore {
  private preference: ThemePreference = 'system';
  private environment: ThemeEnvironment | null = null;
  private createEnvironment: () => ThemeEnvironment | null;
  private listeners = new Set<() => void>();
  private connections = 0;
  private unsaved = false;

  constructor(createEnvironment = browserEnvironment) {
    this.createEnvironment = createEnvironment;
  }

  getSnapshot = (): ThemePreference => this.preference;
  getServerSnapshot = (): ThemePreference => 'system';

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private apply(environment = this.environment) {
    environment?.apply(
      this.preference,
      resolveTheme(this.preference, environment.systemDark()),
    );
  }

  private update(preference: ThemePreference) {
    const changed = this.preference !== preference;
    this.preference = preference;
    this.apply();
    if (changed) this.listeners.forEach((listener) => listener());
  }

  connect = () => {
    if (this.connections > 0) {
      this.connections += 1;
      return this.connectionCleanup();
    }
    const environment = this.createEnvironment();
    if (!environment) return () => undefined;
    this.environment = environment;
    this.connections = 1;
    if (!this.unsaved) {
      try {
        this.update(themePreference(environment.read()));
      } catch {
        // 저장소가 막힌 브라우저에서는 현재 탭의 선택과 시스템 기본값을 쓴다.
      }
    }
    this.apply();
    const stopSystem = environment.watchSystem(() => {
      if (this.preference === 'system') this.apply();
    });
    const stopStorage = environment.watchStorage((value) => {
      this.unsaved = false;
      this.update(themePreference(value));
    });
    this.stopWatching = () => {
      stopSystem();
      stopStorage();
    };
    return this.connectionCleanup();
  };

  private stopWatching: (() => void) | null = null;

  private connectionCleanup() {
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.release();
    };
  }

  private release = () => {
    if (this.connections === 0) return;
    this.connections -= 1;
    if (this.connections > 0) return;
    this.stopWatching?.();
    this.stopWatching = null;
    this.environment = null;
  };

  setPreference = (value: ThemePreference) => {
    const preference = themePreference(value);
    const environment = this.environment ?? this.createEnvironment();
    try {
      environment?.write(preference);
      this.unsaved = false;
    } catch {
      // 디스크에 저장하지 못해도 현재 탭의 테마 변경은 즉시 적용한다.
      this.unsaved = true;
    }
    this.update(preference);
    if (environment !== this.environment) this.apply(environment);
  };
}

export const themeStore = new ThemeStore();
