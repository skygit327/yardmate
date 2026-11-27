import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { useLanguage } from './language';

interface AISettingsState {
  provider: 'none' | 'openai' | 'anthropic';
  model: string;
  base_url: string;
  key_set: boolean;
  key_hint: string | null;
  allow_internal: boolean;
  external: boolean;
  source: 'app' | 'env' | 'none';
}

interface SaveResult { saved: boolean; test: { ok: boolean; error: string | null } | null; settings: AISettingsState }

const providers = [
  { value: 'none', label: 'Off (rule-based)' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Claude' },
  { value: 'local', label: 'Local model (Ollama)' },
] as const;
const modelSuggestions: Record<string, string[]> = {
  openai: ['gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4o'],
  anthropic: ['claude-sonnet-5-5', 'claude-haiku-5-5'],
  local: ['qwen2.5:7b', 'llama3.1:8b'],
};

export default function AISettings({ busy, perform, onChanged }: {
  busy: boolean; perform: (task: () => Promise<void>) => Promise<void>; onChanged: () => void;
}) {
  const { t } = useLanguage();
  const [current, setCurrent] = useState<AISettingsState | null>(null);
  const [choice, setChoice] = useState<typeof providers[number]['value']>('none');
  const [model, setModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [allowInternal, setAllowInternal] = useState(false);
  const [result, setResult] = useState<SaveResult['test'] | null>(null);
  const [saved, setSaved] = useState(false);

  function show(next: AISettingsState) {
    setCurrent(next);
    const local = next.provider === 'openai' && !next.external;
    setChoice(local ? 'local' : next.provider);
    setModel(next.model);
    setBaseUrl(next.base_url);
    setAllowInternal(next.allow_internal);
    setApiKey('');
  }

  useEffect(() => { void perform(async () => show(await api<AISettingsState>('/ai-settings'))); }, []);

  function pick(value: typeof choice) {
    setChoice(value);
    setResult(null);
    setSaved(false);
    const sameAsCurrent = current && (value === 'local' ? current.provider === 'openai' && !current.external : current.provider === value && current.external);
    if (value === 'local') setBaseUrl(sameAsCurrent ? current!.base_url : 'http://localhost:11434/v1');
    else setBaseUrl(sameAsCurrent ? current!.base_url : '');
    if (value !== 'none') setModel(sameAsCurrent ? current!.model : modelSuggestions[value][0]);
  }

  async function save(event?: FormEvent) {
    event?.preventDefault();
    await perform(async () => {
      const provider = choice === 'local' ? 'openai' : choice;
      const response = await api<SaveResult>('/ai-settings', {
        provider, model, base_url: choice === 'local' || choice === 'openai' ? baseUrl : '', api_key: apiKey || null, allow_internal: allowInternal, test: true,
      }, 'PUT');
      setResult(response.test);
      setSaved(response.saved);
      if (response.saved) { show(response.settings); onChanged(); }
    });
  }

  async function reset() {
    await perform(async () => { show(await api<AISettingsState>('/ai-settings', undefined, 'DELETE')); setResult(null); setSaved(false); onChanged(); });
  }

  const needsKey = choice === 'openai' || choice === 'anthropic';
  const keyPlaceholder = current?.key_set && current.external && choice === current.provider
    ? `${t("Saved")}: ${current.key_hint} — ${t("leave empty to keep")}` : choice === 'anthropic' ? 'sk-ant-...' : 'sk-...';

  return <>
    <header className="page-header"><h1>{t("AI settings")}</h1></header>
    <section className="panel ai-settings">
      {current && <div className={`ai-status ${current.provider === 'none' ? 'off' : 'on'}`}>
        <span className="live-dot ai" /><strong>{current.provider === 'none' ? t("AI is off — rule-based analysis is used") : `${t("Connected")}: ${current.provider === 'openai' && !current.external ? t('Local model (Ollama)') : current.provider === 'openai' ? 'OpenAI' : 'Claude'} · ${current.model}`}</strong>
        <small>{current.source === 'app' ? t("Saved in this app") : current.source === 'env' ? t("From the .env file") : ''}</small>
      </div>}

      <form onSubmit={save}><fieldset disabled={busy}>
        <h2 className="step-title"><span>1</span>{t("Choose the AI")}</h2>
        <div className="choice-grid">{providers.map(item => <button type="button" key={item.value} aria-pressed={choice === item.value}
          className={`choice ${choice === item.value ? 'selected' : ''}`} onClick={() => pick(item.value)}><strong>{t(item.label)}</strong></button>)}</div>

        {choice !== 'none' && <>
          <h2 className="step-title"><span>2</span>{t("Model")}</h2>
          <div className="quick">{modelSuggestions[choice].map(name => <button type="button" key={name} className={model === name ? 'selected' : ''} onClick={() => setModel(name)}>{name}</button>)}</div>
          <input required maxLength={120} value={model} aria-label={t("Model")} onChange={event => setModel(event.target.value)} />

          {needsKey && <><h2 className="step-title"><span>3</span>{t("API key")}</h2>
            <input type="password" autoComplete="off" maxLength={500} value={apiKey} placeholder={keyPlaceholder} aria-label={t("API key")} onChange={event => setApiKey(event.target.value)} />
            <p className="muted">{t("The key is stored only on this computer's data folder and is never shown again.")}</p></>}

          {(choice === 'local' || choice === 'openai') && <details open={choice === 'local'}><summary>{t("Endpoint (optional)")}</summary>
            <input maxLength={300} value={baseUrl} placeholder={choice === 'local' ? 'http://localhost:11434/v1' : 'https://api.openai.com/v1'} aria-label={t("Endpoint")} onChange={event => setBaseUrl(event.target.value)} /></details>}

          <h2 className="step-title"><span>{needsKey ? 4 : 3}</span>{t("Data sent to the AI")}</h2>
          <div className="segmented">
            <button type="button" className={!allowInternal ? 'selected good' : ''} onClick={() => setAllowInternal(false)}>{t("Demo data only")}</button>
            <button type="button" className={allowInternal ? 'selected bad' : ''} onClick={() => setAllowInternal(true)}>{t("Also internal standards and customer requests")}</button>
          </div>
          <p className="muted">{allowInternal ? t("Use only with masked data and approved external AI use.") : t("Internal standards and customer requests use the rule-based analysis.")}</p>
        </>}

        {result && !result.ok && <div role="alert" className="error-banner">{t("Connection failed")}: {result.error}</div>}
        {saved && <div className="success-banner"><Icon name="check" />{result?.ok ? t("Connection OK — saved") : t("Saved")}</div>}

        <div className="action-bar">
          {current?.source === 'app' ? <button type="button" className="ghost" onClick={() => void reset()}><Icon name="undo" />{t("Reset to .env")}</button> : <span />}
          <button className="primary big" type="submit" disabled={choice !== 'none' && (!model.trim() || (needsKey && !apiKey && !(current?.key_set && current.provider === choice)))}>
            <Icon name="check" />{choice === 'none' ? t("Save") : t("Test connection and save")}</button>
        </div>
      </fieldset></form>
    </section>
  </>;
}
