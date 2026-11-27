import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { languages, translate } from './translations';
import type { Language } from './translations';

const storageKey = 'spec-agent-language';
const LanguageContext = createContext<{
  language: Language;
  setLanguage: (language: Language) => void;
} | null>(null);

function stored(): Language {
  try {
    const value = localStorage.getItem(storageKey);
    return languages.some(item => item.code === value) ? value as Language : 'en';
  } catch { return 'en'; }
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(stored);

  useEffect(() => {
    document.documentElement.lang = language;
    document.title = `YardMate · ${translate('Shipyard supplier workspace', language)}`;
    try { localStorage.setItem(storageKey, language); } catch {}
  }, [language]);

  return <LanguageContext.Provider value={{ language, setLanguage }}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) throw new Error('LanguageProvider is required.');
  return {
    ...context,
    locale: languages.find(item => item.code === context.language)?.locale ?? 'en-US',
    t: (text: string) => translate(text, context.language),
  };
}

export function LanguageToggle() {
  const { language, setLanguage, t } = useLanguage();
  return <div className="language-toggle" role="group" aria-label="Language">
    {languages.map(item => <button type="button" key={item.code} lang={item.code}
      aria-pressed={language === item.code} aria-label={t(item.switchLabel)} title={t(item.switchLabel)}
      className={language === item.code ? 'selected' : ''} onClick={() => setLanguage(item.code)}>
      {item.short}</button>)}
  </div>;
}
