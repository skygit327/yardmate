import { useState } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { LanguageToggle, useLanguage } from './language';
import { ThemeToggle } from './theme';
import type { User } from './types';

/** My page: name, password, display preferences and shortcuts. */
export default function Account({ user, busy, perform, onRenamed, onGo, onLogout }: {
  user: User; busy: boolean; perform: (task: () => Promise<void>) => Promise<void>;
  onRenamed: (user: User) => void; onGo: (page: string) => void; onLogout: () => void;
}) {
  const { t } = useLanguage();
  const [name, setName] = useState(user.display_name);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saved, setSaved] = useState('');

  async function rename() {
    await perform(async () => {
      const session = await api<{ user: User }>('/auth/me', { display_name: name.trim() }, 'PATCH');
      onRenamed(session.user);
      setSaved(t("Name saved."));
    });
  }

  async function changePassword() {
    await perform(async () => {
      await api('/auth/password', { current_password: current, new_password: next });
      setCurrent(''); setNext(''); setConfirm('');
      setSaved(t("Password changed."));
    });
  }

  const initial = (user.display_name || user.username).trim().charAt(0).toUpperCase();
  const mismatch = confirm.length > 0 && confirm !== next;

  return <div className="account">
    <header className="account-hero">
      <span className="account-avatar" aria-hidden="true">{initial}</span>
      <div><h1>{user.display_name}</h1><small>@{user.username}</small></div>
      <button className="ghost" onClick={onLogout} disabled={busy}><Icon name="logout" size={16} />{t("Sign out")}</button>
    </header>
    {saved && <p className="success-banner" role="status"><Icon name="check" size={16} />{saved}</p>}

    <div className="account-grid">
      <section className="panel account-card">
        <h2>{t("Profile")}</h2>
        <form onSubmit={event => { event.preventDefault(); void rename(); }}>
          <label>{t("Name shown in the app and on reports")}
            <input value={name} maxLength={80} required onChange={event => { setName(event.target.value); setSaved(''); }} /></label>
          <label>{t("Sign-in ID")}<input value={user.username} readOnly /></label>
          <div className="card-actions"><button className="primary" disabled={busy || !name.trim() || name.trim() === user.display_name}>
            <Icon name="check" size={16} />{t("Save name")}</button></div>
        </form>
      </section>

      <section className="panel account-card">
        <h2>{t("Change password")}</h2>
        <form onSubmit={event => { event.preventDefault(); if (!mismatch) void changePassword(); }}>
          <label>{t("Current password")}<input type="password" autoComplete="current-password" value={current} required onChange={event => setCurrent(event.target.value)} /></label>
          <label>{t("New password (12 characters or more)")}<input type="password" autoComplete="new-password" minLength={12} maxLength={128} value={next} required onChange={event => setNext(event.target.value)} /></label>
          <label>{t("New password again")}<input type="password" autoComplete="new-password" value={confirm} required onChange={event => setConfirm(event.target.value)}
            aria-invalid={mismatch} /></label>
          {mismatch && <p className="warning-text">{t("The two new passwords are different.")}</p>}
          <div className="card-actions"><button className="primary" disabled={busy || !current || next.length < 12 || mismatch}>
            <Icon name="lock" size={16} />{t("Change password")}</button></div>
        </form>
      </section>

      <section className="panel account-card">
        <h2>{t("Display")}</h2>
        <div className="account-row"><span>{t("Theme")}</span><ThemeToggle /></div>
        <div className="account-row"><span>{t("Language")}</span><LanguageToggle /></div>
      </section>

      <section className="panel account-card">
        <h2>{t("Shortcuts")}</h2>
        <div className="account-links">
          <button onClick={() => onGo('profile')}><Icon name="building" size={18} /><span>{t("Company profile")}</span><Icon name="next" size={16} /></button>
          <button onClick={() => onGo('ai')}><Icon name="gear" size={18} /><span>{t("AI settings")}</span><Icon name="next" size={16} /></button>
          <button onClick={() => onGo('tasks')}><Icon name="calendar" size={18} /><span>{t("Tasks")}</span><Icon name="next" size={16} /></button>
        </div>
      </section>
    </div>
  </div>;
}
