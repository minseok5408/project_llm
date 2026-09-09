'use client';

import { useEffect, useState, useSyncExternalStore } from 'react';
import type { SubmitEvent, ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

import { AuthSessionStore } from '../state/auth-session';
import type { AuthSession } from '../state/auth-session';

export type AuthenticatedProps = {
  session: AuthSession;
  request: AuthSessionStore['request'];
  logout: AuthSessionStore['logout'];
  logoutPending: boolean;
  authError?: string;
};

function AuthForm({
  store,
  message,
}: {
  store: AuthSessionStore;
  message?: string;
}) {
  const [signupOpen, setSignupOpen] = useState(false);
  const [mode, setMode] = useState<'login' | 'signup'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirmation, setPasswordConfirmation] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/v1/auth/config', {
      cache: 'no-store',
      credentials: 'same-origin',
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) return;
        const config = (await response.json()) as { signup_mode?: string };
        if (!controller.signal.aborted)
          setSignupOpen(config.signup_mode === 'open');
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    if (password.length < 8 || password.length > 32) {
      setError('비밀번호는 8~32자로 입력해 주세요.');
      return;
    }
    if (mode === 'signup' && !displayName.trim()) {
      setError('채팅에서 사용할 사용자 이름을 입력해 주세요.');
      return;
    }
    if (mode === 'signup' && password !== passwordConfirmation) {
      setError('비밀번호가 일치하지 않습니다. 다시 확인해 주세요.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await store.authenticate(mode, {
        email: email.trim(),
        password,
        ...(mode === 'signup' ? { display_name: displayName.trim() } : {}),
      });
    } catch (requestError) {
      setError(
        requestError instanceof Error &&
          !['TypeError', 'AbortError', 'TimeoutError'].includes(
            requestError.name,
          )
          ? requestError.message
          : '서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
      );
    } finally {
      setPassword('');
      setPasswordConfirmation('');
      setBusy(false);
    }
  };

  return (
    <div className="w-full max-w-md border border-border bg-card p-6 sm:p-8">
      <p className="mb-6 text-sm text-primary">$ Project LLM</p>
      <h1 className="text-2xl font-medium">
        {mode === 'signup' ? '회원가입' : '로그인'}
      </h1>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">
        {mode === 'signup'
          ? '내 계정을 만들고 로컬 AI와 대화를 시작하세요.'
          : '내 계정으로 로컬 AI 작업 공간에 접속하세요.'}
        <br />
        {mode === 'signup'
          ? '가입 후 바로 로그인되며 24시간 동안 유지됩니다.'
          : '로그인은 접속한 브라우저에서 24시간 유지됩니다.'}
      </p>
      {mode === 'signup' && (
        <p className="mt-3 text-sm text-primary">
          매월 20,000토큰이 무료로 제공됩니다.
        </p>
      )}
      {signupOpen && (
        <fieldset
          className="mt-6 grid grid-cols-2 border border-border"
          aria-label="계정 접속 방식"
        >
          {(['login', 'signup'] as const).map((value) => (
            <Button
              key={value}
              type="button"
              variant={mode === value ? 'default' : 'ghost'}
              className="rounded-none"
              disabled={busy}
              aria-pressed={mode === value}
              onClick={() => {
                setMode(value);
                setPassword('');
                setPasswordConfirmation('');
                setError(null);
              }}
            >
              {value === 'login' ? '로그인' : '회원가입'}
            </Button>
          ))}
        </fieldset>
      )}
      <form className="mt-6 space-y-4" onSubmit={(event) => void submit(event)}>
        <div className="space-y-2">
          <label htmlFor="auth-email" className="text-sm">
            이메일
          </label>
          <Input
            id="auth-email"
            name="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            maxLength={320}
            disabled={busy}
            className="h-11 rounded-none"
          />
        </div>
        {mode === 'signup' && (
          <div className="space-y-2">
            <label htmlFor="auth-name" className="text-sm">
              사용자 이름
            </label>
            <Input
              id="auth-name"
              name="name"
              autoComplete="nickname"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="채팅에서 표시할 이름"
              required
              maxLength={200}
              disabled={busy}
              className="h-11 rounded-none"
            />
          </div>
        )}
        <div className="space-y-2">
          <label htmlFor="auth-password" className="text-sm">
            비밀번호
          </label>
          <Input
            id="auth-password"
            name="password"
            type="password"
            autoComplete={
              mode === 'signup' ? 'new-password' : 'current-password'
            }
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            minLength={8}
            maxLength={32}
            disabled={busy}
            aria-describedby="auth-password-hint"
            className="h-11 rounded-none"
          />
          <p id="auth-password-hint" className="text-xs text-muted-foreground">
            8~32자
          </p>
        </div>
        {mode === 'signup' && (
          <div className="space-y-2">
            <label htmlFor="auth-password-confirmation" className="text-sm">
              비밀번호 확인
            </label>
            <Input
              id="auth-password-confirmation"
              name="password_confirmation"
              type="password"
              autoComplete="new-password"
              value={passwordConfirmation}
              onChange={(event) => setPasswordConfirmation(event.target.value)}
              placeholder="비밀번호를 한 번 더 입력하세요"
              required
              minLength={8}
              maxLength={32}
              disabled={busy}
              className="h-11 rounded-none"
            />
          </div>
        )}
        {(error || message) && (
          <p
            role={error ? 'alert' : 'status'}
            className={
              error
                ? 'text-sm leading-6 text-rose-300'
                : 'text-sm leading-6 text-muted-foreground'
            }
          >
            {error || message}
          </p>
        )}
        <Button
          type="submit"
          disabled={busy}
          className="h-11 w-full rounded-none"
        >
          {busy
            ? '확인 중...'
            : mode === 'signup'
              ? '가입하고 시작하기'
              : '로그인'}
        </Button>
      </form>
      {!signupOpen && (
        <p className="mt-5 text-xs leading-5 text-muted-foreground">
          신규 가입은 현재 닫혀 있습니다. 계정이 없으면 관리자에게 문의하세요.
        </p>
      )}
    </div>
  );
}

export function AuthGate({
  children,
}: {
  children: (props: AuthenticatedProps) => ReactNode;
}) {
  const [store] = useState(() => new AuthSessionStore());
  const state = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getServerSnapshot,
  );

  useEffect(() => {
    void store.verify();
    const verify = () => void store.verify();
    const onVisible = () => {
      if (document.visibilityState === 'visible') verify();
    };
    window.addEventListener('focus', verify);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', verify);
      document.removeEventListener('visibilitychange', onVisible);
      store.dispose();
    };
  }, [store]);

  if (state.status === 'authenticated') {
    return children({
      session: state.session,
      request: store.request,
      logout: store.logout,
      logoutPending: state.busy,
      authError: state.message,
    });
  }

  return (
    <main className="flex min-h-[100dvh] items-center justify-center bg-background px-4 py-10">
      {state.status === 'anonymous' ? (
        <AuthForm store={store} message={state.message} />
      ) : (
        <section
          className="w-full max-w-md border border-border bg-card p-8"
          aria-live="polite"
        >
          <p className="mb-6 text-sm text-primary">$ Project LLM</p>
          <h1 className="text-xl">
            {state.status === 'checking' ? '로그인 확인 중' : '서버 연결 확인'}
          </h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            {state.status === 'checking'
              ? '저장된 로그인 상태를 확인하고 있습니다.'
              : state.message}
          </p>
          {state.status === 'unavailable' && (
            <Button
              className="mt-6 rounded-none"
              onClick={() => void store.verify()}
            >
              다시 시도
            </Button>
          )}
        </section>
      )}
    </main>
  );
}
