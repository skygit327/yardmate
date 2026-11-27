import { useEffect, useState } from 'react';
import { Icon } from './icons';
import { useLanguage } from './language';

type Theme = 'light' | 'dark';
const storageKey = 'yardmate-theme';

export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {}
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#111317' : '#f3f5f8');
}

/** Sun / moon switch; the choice is remembered on this browser. */
export function ThemeToggle() {
  const { t } = useLanguage();
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    applyTheme(theme);
    try { localStorage.setItem(storageKey, theme); } catch {}
  }, [theme]);
  const next = theme === 'dark' ? 'light' : 'dark';
  return <button type="button" className="icon-button theme-toggle" aria-label={t(next === 'dark' ? 'Dark mode' : 'Light mode')}
    title={t(next === 'dark' ? 'Dark mode' : 'Light mode')} onClick={() => setTheme(next)}>
    <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={18} />
  </button>;
}
