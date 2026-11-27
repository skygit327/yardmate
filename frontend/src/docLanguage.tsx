import { useLanguage } from './language';
import { languages } from './translations';
import type { Language } from './translations';

export function DocLanguagePicker({ value, onChange, available }: {
  value: Language; onChange: (language: Language) => void; available?: Language[];
}) {
  const { t } = useLanguage();
  const options = languages.filter(item => !available || available.includes(item.code));
  return <div className="doc-language" role="group" aria-label={t("Document language")}>
    {options.map(item => <button type="button" key={item.code} lang={item.code} aria-pressed={value === item.code}
      className={value === item.code ? 'selected' : ''} onClick={() => onChange(item.code)}>{item.short}</button>)}
  </div>;
}

export function reportLanguages(files: Record<string, unknown> | undefined): Language[] {
  return languages.map(item => item.code).filter(code => files && Object.hasOwn(files, code === 'en' ? 'pdf' : `pdf-${code}`));
}
