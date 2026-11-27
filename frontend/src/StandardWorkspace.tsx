import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from './api';
import { DocLanguagePicker } from './docLanguage';
import { Icon } from './icons';
import { displayStandard } from './legacyDisplay';
import { useLanguage } from './language';
import type { Language } from './translations';
import type { RevisionProposal, Standard, StandardDiff, StandardVersion } from './types';

const symbols = { lt: '<', lte: '≤', gt: '>', gte: '≥' };
const statusLabels: Record<Standard['status'], string> = { ACTIVE: 'Active', DRAFT: 'Draft', SUPERSEDED: 'Superseded' };
const quickRequests = [
  { label: 'Change limit', text: 'Burr height 0.20 mm → 0.18 mm' },
  { label: 'Warning margin', text: 'Burr warning margin 10%' },
  { label: 'All areas', text: 'All areas burr height 0.15 mm or less' },
  { label: 'Not applicable', text: 'Crack not applicable' },
];

interface ChatMessage {
  id: number;
  role: 'user' | 'agent';
  text: string;
  file?: string;
  proposal?: RevisionProposal;
  selected?: boolean[];
  revision?: string;
  applied?: boolean;
}

function DiffTable({ diff }: { diff: StandardDiff }) {
  const { t } = useLanguage();
  if (!diff.changed) return <p className="muted">{t("No differences from the previous version.")}</p>;
  return <div className="diff-table" role="table" aria-label={t("Changes")}>
    {diff.header.map(entry => <div className="diff-row" role="row" key={entry.field}>
      <span className="diff-name">{t(entry.field)}</span><span className="before">{entry.before}</span><Icon name="next" size={14} /><span className="after">{entry.after}</span></div>)}
    {diff.rules.map(entry => <div className={`diff-row ${entry.change}`} role="row" key={entry.rule_id}>
      <span className="diff-name"><span className={`badge ${entry.change}`}>{t(entry.change)}</span>{t(entry.area)} / {t(entry.item)}</span>
      <span className="before">{t(entry.before)}</span><Icon name="next" size={14} /><span className="after">{t(entry.after)}</span></div>)}
  </div>;
}

function describe(operation: RevisionProposal['operations'][number]): string {
  const values = Object.entries(operation.values).map(([key, value]) => `${key}=${value ?? '∅'}`).join(', ');
  const target = [operation.rule_id, operation.field].filter(Boolean).join('.');
  const condition = operation.condition
    ? `${operation.condition.label} ${symbols[operation.condition.operator]} ${operation.condition.limit} ${operation.condition.unit}`
    : '';
  return `${operation.op} ${target} ${values || condition || operation.rule?.item || ''}`.trim();
}

export default function StandardWorkspace({ lineage, initialInstruction = '', busy, perform, onClose, onChanged }: {
  lineage: string;
  initialInstruction?: string;
  busy: boolean;
  perform: (task: () => Promise<void>) => Promise<void>;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const { t, language, locale } = useLanguage();
  const [versions, setVersions] = useState<StandardVersion[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [diff, setDiff] = useState<StandardDiff | null>(null);
  const [docLanguage, setDocLanguage] = useState<Language | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [instruction, setInstruction] = useState(initialInstruction);
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const counter = useRef(0);
  const log = useRef<HTMLDivElement>(null);

  const draft = versions.find(version => version.status === 'DRAFT');
  const active = versions.find(version => version.status === 'ACTIVE');
  const target = draft ?? active;
  const rawSelected = versions.find(version => version.id === selectedId) ?? target ?? versions[0];
  const selected = rawSelected ? displayStandard(rawSelected, language) : undefined;

  async function load(preferred?: string) {
    const next = await api<StandardVersion[]>(`/standards/${lineage}/versions`);
    setVersions(next);
    const fallback = next.find(version => version.status === 'DRAFT') ?? next.find(version => version.status === 'ACTIVE') ?? next[0];
    setSelectedId(preferred && next.some(version => version.id === preferred) ? preferred : fallback?.id ?? '');
  }

  useEffect(() => { void perform(() => load()); setMessages([]); }, [lineage]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: 'smooth' }); }, [messages.length]);
  useEffect(() => {
    setDiff(null);
    if (rawSelected?.previous_id) {
      api<StandardDiff>(`/standards/${rawSelected.id}/diff`).then(setDiff).catch(() => setDiff(null));
    }
  }, [rawSelected?.id, rawSelected?.history?.length]);

  function push(message: Omit<ChatMessage, 'id'>) {
    counter.current += 1;
    setMessages(current => [...current, { ...message, id: counter.current }]);
  }

  function update(id: number, changes: Partial<ChatMessage>) {
    setMessages(current => current.map(message => message.id === id ? { ...message, ...changes } : message));
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (!target || (!instruction.trim() && !file)) return;
    const text = instruction.trim();
    const attachment = file;
    await perform(async () => {
      const form = new FormData();
      form.append('instruction', text);
      if (attachment) form.append('file', attachment);
      const proposal = await api<RevisionProposal>(`/standards/${target.id}/revision-proposals`, form);
      push({ role: 'user', text, file: attachment?.name });
      push({ role: 'agent', text: proposal.summary, proposal, selected: proposal.operations.map(() => true), revision: proposal.suggested_revision });
      setInstruction('');
      setFile(null);
      if (fileInput.current) fileInput.current.value = '';
    });
  }

  async function applyProposal(message: ChatMessage) {
    const proposal = message.proposal!;
    const operations = proposal.operations.filter((_, index) => message.selected?.[index]);
    if (!operations.length) return;
    await perform(async () => {
      const saved = await api<Standard>(`/standards/${proposal.base_id}/revisions`, {
        operations,
        summary: operations.length === proposal.operations.length ? proposal.summary : '',
        instruction: proposal.instruction,
        revision: message.revision?.trim() || null,
        interpreter: proposal.interpreter,
        request_file: proposal.attachment?.stored_name ?? null,
        request_filename: proposal.attachment?.filename ?? null,
      });
      update(message.id, { applied: true });
      push({ role: 'agent', text: `${t('Draft saved:')} ${t('Rev')} ${saved.revision}` });
      await load(saved.id);
      await onChanged();
    });
  }

  async function activate(version: Standard) {
    await perform(async () => {
      await api(`/standards/${version.id}/activate`, {});
      push({ role: 'agent', text: `${t('Now active:')} ${t('Rev')} ${version.revision}` });
      await load(version.id);
      await onChanged();
    });
  }

  async function discard(version: Standard) {
    await perform(async () => {
      await api(`/standards/${version.id}`, undefined, 'DELETE');
      push({ role: 'agent', text: `${t('Draft discarded:')} ${t('Rev')} ${version.revision}` });
      await load();
      await onChanged();
    });
  }

  if (!selected) return <section className="panel"><button className="ghost" onClick={onClose}><Icon name="back" />{t("Back")}</button></section>;

  return <section className="panel standard-workspace">
    <div className="panel-top"><button className="ghost" onClick={onClose}><Icon name="back" />{t("Back")}</button>
      <div className="title-block"><h1>{selected.product_name}</h1><small>{selected.customer} · {selected.part_number} · {selected.document_number}</small></div></div>

    <div className="version-chips" role="tablist" aria-label={t("Versions")}>{versions.map(version => <button key={version.id} role="tab"
      aria-selected={version.id === selected.id} className={`version-chip ${version.status.toLowerCase()} ${version.id === selected.id ? 'selected' : ''}`} onClick={() => setSelectedId(version.id)}>
      <strong>{t('Rev')} {version.revision}</strong><small>{t(statusLabels[version.status])}</small></button>)}</div>

    <div className="workspace-grid">
      <div className="version-detail">
        <div className="card-head"><span className={`badge ${selected.status.toLowerCase()}`}>{t(statusLabels[selected.status])}</span>
          <span className="muted">v{selected.version ?? 1} · {rawSelected?.inspection_count ?? 0} {t("inspections")}</span></div>
        <div className="card-actions">
          <DocLanguagePicker value={docLanguage ?? language} onChange={setDocLanguage} />
          <a className="button" href={`/api/standards/${selected.id}/document/pdf${(docLanguage ?? language) === 'en' ? '' : `?lang=${docLanguage ?? language}`}`} target="_blank" rel="noreferrer"><Icon name="download" />PDF</a>
          <a className="button" href={`/api/standards/${selected.id}/document/xlsx${(docLanguage ?? language) === 'en' ? '' : `?lang=${docLanguage ?? language}`}`}><Icon name="download" />Excel</a>
          {selected.status === 'DRAFT' && <button className="ghost danger-text" disabled={busy} onClick={() => discard(selected)}><Icon name="trash" />{t("Discard")}</button>}
        </div>
        {selected.status === 'DRAFT' && <button className="primary big wide" disabled={busy} onClick={() => activate(selected)}><Icon name="check" />{t("Activate this revision")}</button>}
        {diff && <><h3 className="sub">{t("Changes from")} {t('Rev')} {diff.before_revision}</h3><DiffTable diff={diff} /></>}
        <details><summary>{t("Items")} · {selected.rules.length}</summary>{selected.rules.map(rule => <p key={rule.id} className="rule-line"><strong>{rule.area} / {rule.item}</strong>
          <span>{rule.kind === 'NUMERIC' ? rule.conditions.map(condition => `${condition.label} ${symbols[condition.operator]} ${condition.limit} ${condition.unit}${condition.warning_margin ? ` (±${condition.warning_margin})` : ''}`).join(' · ') : t(rule.kind)}</span></p>)}</details>
        {(selected.history?.length ?? 0) > 0 && <details><summary>{t("History")} · {selected.history!.length}</summary>
          {selected.history!.map((entry, index) => <div className="history-entry" key={index}><p><strong>{t(entry.kind)}</strong> · {new Date(entry.at).toLocaleString(locale)} · {entry.actor}</p>
            {entry.summary && <p className="pre muted">{t(entry.summary)}</p>}
            {entry.instruction && <blockquote>{entry.instruction}</blockquote>}
            {entry.request_file && <a className="button small" href={`/api/requests/${entry.request_file}`}><Icon name="clip" size={14} />{entry.request_filename ?? entry.request_file}</a>}</div>)}</details>}
      </div>

      <div className="revision-chat">
        <div className="chat-head"><Icon name="spark" /><h3>{t("Revision assistant")}</h3>{target && <span className="chip">{t('Rev')} {target.revision}</span>}</div>
        <div className="quick" role="group" aria-label={t("Quick requests")}>{quickRequests.map(item => <button type="button" key={item.label} disabled={busy || !target}
          onClick={() => setInstruction(t(item.text))}>{t(item.label)}</button>)}</div>
        <div className="chat-log" aria-live="polite" ref={log}>
          {messages.length === 0 && <p className="muted center">{t("Type a change or attach a request.")}</p>}
          {messages.map(message => <div key={message.id} className={`chat-message ${message.role}`}>
            {message.file && <span className="chip"><Icon name="clip" size={12} />{message.file}</span>}
            {message.text && !(message.proposal?.interpreter === 'rules' && message.proposal.diff) && <p className="pre">{message.role === 'agent' ? t(message.text) : message.text}</p>}
            {message.proposal && <div className="proposal">
              {message.proposal.diff && <DiffTable diff={message.proposal.diff} />}
              {message.proposal.notes.length > 0 && <ul className="notes">{message.proposal.notes.map((note, index) => <li key={index}>{t(note)}</li>)}</ul>}
              {message.proposal.operations.length === 0 && <p className="warning-text">{t("No changes found. Name the area and item.")}</p>}
              {message.proposal.operations.length > 0 && !message.applied && <>
                <div className="apply-row">
                  <label className="rev-input">{t('Rev')}<input value={message.revision ?? ''} maxLength={40} disabled={message.proposal.base_status === 'DRAFT'}
                    aria-label={t("Revision label")} onChange={event => update(message.id, { revision: event.target.value })} /></label>
                  <button className="primary" disabled={busy || !message.selected?.some(Boolean)} onClick={() => applyProposal(message)}>
                    <Icon name="check" />{message.proposal.base_status === 'DRAFT' ? t("Apply to draft") : t("Create draft")}</button></div>
                <details><summary>{t("Details")} · {message.proposal.operations.length} · {message.proposal.interpreter === 'rules' ? t('Rule-based (offline)') : message.proposal.interpreter}</summary>
                  {message.proposal.operations.map((operation, index) => <label className="operation" key={index}>
                    <input type="checkbox" checked={message.selected?.[index] ?? true} onChange={event => update(message.id, { selected: message.selected?.map((value, position) => position === index ? event.target.checked : value) })} />
                    <span><code>{describe(operation)}</code><small>{operation.reason}</small></span></label>)}</details></>}
              {message.applied && <span className="badge pass"><Icon name="check" size={12} /> {t("Applied")}</span>}
            </div>}
          </div>)}
        </div>
        <form onSubmit={send} className="chat-input"><fieldset disabled={busy || !target}>
          {file && <span className="chip attached"><Icon name="clip" size={12} />{file.name}<button type="button" aria-label={t("Remove attachment")} onClick={() => { setFile(null); if (fileInput.current) fileInput.current.value = ''; }}><Icon name="x" size={12} /></button></span>}
          <div className="composer">
            <label className="icon-button button" title={t("Attach request")} aria-label={t("Attach request")}><Icon name="clip" />
              <input ref={fileInput} className="file-input" type="file" accept=".pdf,.xlsx,.docx,.eml,.txt,.md" onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
            <textarea rows={2} maxLength={20000} value={instruction} placeholder={t("e.g. Burr height 0.20 mm → 0.18 mm")} aria-label={t("Change request")}
              onChange={event => setInstruction(event.target.value)}
              onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
            <button className="primary icon-button" type="submit" aria-label={t("Send")} title={t("Send")} disabled={!instruction.trim() && !file}><Icon name="send" /></button>
          </div>
        </fieldset></form>
      </div>
    </div>
  </section>;
}
