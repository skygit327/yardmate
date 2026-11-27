import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import App from './App';
import { api, setCsrfToken } from './api';
import type { Session } from './types';
import { LanguageToggle, useLanguage } from './language';
import { ThemeToggle } from './theme';

export default function AuthGate() {
  const { t } = useLanguage();
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const channel = useRef<BroadcastChannel | null>(null);
  const version = useRef(0);
  const authBusy = useRef(false);

  function clearSession() {
    version.current += 1;
    setCsrfToken('');
    setSession(null);
    setPassword('');
  }

  useEffect(() => {
    let active = true;
    async function check() {
      if (authBusy.current) return;
      const current = version.current;
      try {
        const next = await api<Session>('/auth/me');
        if (active && current === version.current) {
          version.current += 1;
          setCsrfToken(next.csrf_token);
          setSession(next);
        }
      } catch {
        if (active && current === version.current) clearSession();
      } finally {
        if (active) setChecking(false);
      }
    }
    const unauthorized = () => { clearSession(); setChecking(false); };
    const switched = () => { clearSession(); setChecking(true); void check(); };
    window.addEventListener('spec-agent:unauthorized', unauthorized);
    window.addEventListener('focus', check);
    const interval = window.setInterval(check, 60_000);
    if (typeof BroadcastChannel !== 'undefined') {
      channel.current = new BroadcastChannel('spec-agent-auth');
      channel.current.onmessage = switched;
    }
    void check();
    return () => {
      active = false;
      window.removeEventListener('spec-agent:unauthorized', unauthorized);
      window.removeEventListener('focus', check);
      window.clearInterval(interval);
      channel.current?.close();
    };
  }, []);

  useEffect(() => {
    if (!session) return;
    const timeout = window.setTimeout(() => {
      clearSession();
      setError('Your session has expired. Sign in again. Saved work is preserved.');
    }, Math.max(0, session.expires_at * 1000 - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [session]);

  async function login(event: FormEvent) {
    event.preventDefault();
    if (authBusy.current) return;
    authBusy.current = true;
    setBusy(true);
    setError('');
    version.current += 1;
    const current = version.current;
    try {
      const next = await api<Session>('/auth/login', { username, password });
      if (current !== version.current) return;
      version.current += 1;
      setCsrfToken(next.csrf_token);
      setSession(next);
      channel.current?.postMessage('changed');
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Sign-in failed.');
    } finally { authBusy.current = false; setPassword(''); setBusy(false); }
  }

  async function logout() {
    if (authBusy.current) return;
    authBusy.current = true;
    setBusy(true);
    setError('');
    try {
      await api('/auth/logout', {});
      clearSession();
      channel.current?.postMessage('changed');
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Sign-out failed. Please try again.');
    } finally { authBusy.current = false; setBusy(false); }
  }

  if (checking) return <main className="login-shell"><div className="login-language"><ThemeToggle /><LanguageToggle /></div><p role="status">{t("Checking your session...")}</p></main>;
  if (session) return <>
    {error && <div role="alert" className="error-banner">{t(error)}</div>}
    <App key={session.csrf_token} user={session.user} onLogout={logout} loggingOut={busy} />
  </>;
  return <main className="login-shell"><div className="login-language"><ThemeToggle /><LanguageToggle /></div><section className="panel login-panel">
    <div className="login-brand"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 40 40" width="40" height="40"><path d="M6 22h28l-4 9H10z" fill="#c4532f" /><path d="M6 22h28" stroke="#fff" strokeWidth="2" /><path d="M12 22V12h9v10M23 22v-6h6v6" fill="none" stroke="#dbe7ef" strokeWidth="2.4" strokeLinejoin="round" /></svg></span><h1>YardMate</h1></div>
    {error && <div role="alert" className="error-banner">{t(error)}</div>}
    <form onSubmit={login}><fieldset disabled={busy}>
      <label>{t("Username")}<input required autoComplete="username" maxLength={80} value={username}
        onChange={event => setUsername(event.target.value)} /></label>
      <label>{t("Password")}<input required type="password" autoComplete="current-password" maxLength={128}
        value={password} onChange={event => setPassword(event.target.value)} /></label>
      <button type="submit" className="primary">{busy ? t("Signing in...") : t("Sign in")}</button>
    </fieldset></form>
  </section></main>;
}
