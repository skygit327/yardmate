import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from './api';
import { StatusBadge, useCode } from './Customers';
import { Icon } from './icons';
import { useLanguage } from './language';
import { languages } from './translations';
import type { CustomerOverview, CustomerRequest, ReplyLanguage, RequestSummary } from './types';

const channels = ['EMAIL', 'DOCUMENT', 'PHONE', 'MEETING', 'OTHER'] as const;
const categories = ['COMPLAINT', 'SPEC_CHANGE', 'INQUIRY', 'DELIVERY', 'REPORT_REQUEST', 'OTHER'] as const;
const flow = ['OPEN', 'IN_PROGRESS', 'RESPONDED', 'CLOSED'] as const;
const actionIcons: Record<string, string> = {
  open_revision: 'edit', review_inspection: 'list', reinspect: 'refresh', send_report: 'file', check_standard: 'book', ask_customer: 'send', check_delivery: 'history',
};

function RequestForm({ customer, customers, busy, perform, onCreated, onCancel }: {
  customer: string; customers: CustomerOverview[]; busy: boolean; perform: (task: () => Promise<void>) => Promise<void>;
  onCreated: (request: CustomerRequest) => void; onCancel: () => void;
}) {
  const { t } = useLanguage();
  const code = useCode();
  const [form, setForm] = useState({ customer, parts: '', subject: '', body: '', channel: 'EMAIL' as typeof channels[number], language: '' as ReplyLanguage | '' });
  const [files, setFiles] = useState<File[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const known = customers.find(item => item.name.toLowerCase() === form.customer.trim().toLowerCase());

  async function submit(event: FormEvent) {
    event.preventDefault();
    await perform(async () => {
      const data = new FormData();
      data.append('customer', form.customer);
      data.append('part_numbers', form.parts);
      data.append('subject', form.subject);
      data.append('body', form.body);
      data.append('channel', form.channel);
      if (form.language) data.append('response_language', form.language);
      files.forEach(file => data.append('files', file));
      onCreated(await api<CustomerRequest>('/customer-requests', data));
    });
  }

  return <section className="panel">
    <div className="panel-top"><button className="ghost" onClick={onCancel}><Icon name="back" />{t("Back")}</button><h1>{t("New request")}</h1></div>
    <form onSubmit={submit}><fieldset disabled={busy}>
      <h2 className="step-title"><span>1</span>{t("Attach or paste the request")}</h2>
      <label className="dropzone">
        <Icon name="clip" size={22} /><strong>{files.length ? files.map(file => file.name).join(', ') : t("Attach mail (.eml), documents or photos")}</strong>
        <small>{t("E-mail subject, sender and attachments are read automatically.")}</small>
        <input ref={input} className="file-input" type="file" multiple accept=".eml,.pdf,.xlsx,.docx,.txt,.md,.png,.jpg,.jpeg"
          onChange={event => setFiles(Array.from(event.target.files ?? []).slice(0, 10))} /></label>
      {files.length > 0 && <button type="button" className="small ghost" onClick={() => { setFiles([]); if (input.current) input.current.value = ''; }}><Icon name="x" size={14} />{t("Remove attachments")}</button>}
      <textarea rows={6} maxLength={60000} placeholder={t("Or paste the request text here...")} aria-label={t("Request text")} value={form.body} onChange={event => setForm({ ...form, body: event.target.value })} />

      <h2 className="step-title"><span>2</span>{t("Customer (optional — detected automatically)")}</h2>
      <div className="quick">{customers.slice(0, 8).map(item => <button type="button" key={item.name} className={known?.name === item.name ? 'selected' : ''}
        onClick={() => setForm({ ...form, customer: known?.name === item.name ? '' : item.name, parts: '' })}>{item.name}</button>)}</div>
      <div className="form-grid two">
        <label>{t("Customer")}<input list="known-customers" maxLength={100} value={form.customer} onChange={event => setForm({ ...form, customer: event.target.value })} /></label>
        <datalist id="known-customers">{customers.map(item => <option key={item.name} value={item.name} />)}</datalist>
        <label>{t("Subject")}<input maxLength={300} value={form.subject} placeholder={t("Taken from the mail if empty")} onChange={event => setForm({ ...form, subject: event.target.value })} /></label>
      </div>
      {known && known.parts.length > 0 && <div className="quick">{known.parts.map(entry => {
        const selected = form.parts.split(',').map(value => value.trim()).includes(entry.part_number);
        return <button type="button" key={entry.part_number} className={selected ? 'selected' : ''} onClick={() => {
          const current = form.parts.split(',').map(value => value.trim()).filter(Boolean);
          setForm({ ...form, parts: (selected ? current.filter(value => value !== entry.part_number) : [...current, entry.part_number]).join(', ') });
        }}>{entry.part_number}</button>;
      })}</div>}

      <h2 className="step-title"><span>3</span>{t("Channel and reply language")}</h2>
      <div className="inline wrap">
        <div className="segmented compact">{channels.map(value => <button type="button" key={value} className={form.channel === value ? 'selected' : ''} onClick={() => setForm({ ...form, channel: value })}>{code(value)}</button>)}</div>
        <div className="doc-language"><button type="button" className={form.language === '' ? 'selected' : ''} onClick={() => setForm({ ...form, language: '' })}>{t("Auto")}</button>
          {languages.map(item => <button type="button" key={item.code} className={form.language === item.code ? 'selected' : ''} onClick={() => setForm({ ...form, language: item.code })}>{item.short}</button>)}</div>
      </div>
      <div className="action-bar"><button className="primary big" type="submit" disabled={!form.body.trim() && !files.length && !form.subject.trim()}><Icon name="spark" />{t("Register and analyze")}</button></div>
    </fieldset></form>
  </section>;
}

function RequestDetail({ id, busy, perform, onBack, onOpenCustomer, onOpenInspection, onOpenStandard, onChanged }: {
  id: string; busy: boolean; perform: (task: () => Promise<void>) => Promise<void>; onBack: () => void; onChanged: () => Promise<void>;
  onOpenCustomer: (name: string) => void; onOpenInspection: (id: string) => void; onOpenStandard: (lineage: string, instruction?: string) => void;
}) {
  const { t, locale } = useLanguage();
  const code = useCode();
  const [request, setRequest] = useState<CustomerRequest | null>(null);
  const [draft, setDraft] = useState('');
  const [note, setNote] = useState('');
  const [copied, setCopied] = useState(false);

  function show(next: CustomerRequest) { setRequest(next); setDraft(next.response_draft); }
  const render = (template: string | undefined, params: Record<string, string | string[]> | undefined, fallback = '') =>
    !template ? fallback : t(template).replace(/\{(\w+)\}/g, (_, name: string) => {
      const value = params?.[name];
      return Array.isArray(value) ? value.map(item => code(item)).join(', ') : String(value ?? '');
    });
  useEffect(() => { void perform(async () => show(await api<CustomerRequest>(`/customer-requests/${id}`))); }, [id]);

  async function call(path: string, body: unknown, method?: string) {
    await perform(async () => { show(await api<CustomerRequest>(`/customer-requests/${id}${path}`, body, method)); await onChanged(); });
  }
  const dirty = request ? draft !== request.response_draft : false;
  async function saveDraft() { await call('', { response_draft: draft }, 'PATCH'); }

  if (!request) return <section className="panel"><button className="ghost" onClick={onBack}><Icon name="back" />{t("Back")}</button></section>;
  const analysis = request.analysis;
  const step = flow.indexOf(request.status);
  const standardLineage = (standardId?: string | null) => analysis?.related_standards.find(item => item.id === standardId)?.lineage ?? standardId ?? '';

  return <section className="panel request-page">
    <div className="panel-top"><button className="ghost" onClick={onBack}><Icon name="back" />{t("Back")}</button>
      <div className="title-block"><h1>{request.priority === 'HIGH' && <span className="priority-dot" />}{request.subject}</h1>
        <small>{request.sender || code(request.channel)} · {new Date(request.received_at).toLocaleString(locale)}</small></div>
      <div className="inline push"><StatusBadge value={request.category} />{request.priority === 'HIGH' && <span className="badge fail">{code('HIGH')}</span>}</div></div>

    <ol className="stepper">{flow.map((value, index) => <li key={value} className={index < step || request.status === 'CLOSED' ? 'done' : index === step ? 'current' : ''}>
      <span>{index < step || request.status === 'CLOSED' ? <Icon name="check" size={14} /> : index + 1}</span>{code(value)}</li>)}</ol>

    <div className="inline wrap chips-row">
      {request.customer ? <button className="chip-button" onClick={() => onOpenCustomer(request.customer)}><Icon name="book" size={14} />{request.customer}</button> : <span className="chip">{t("No customer")}</span>}
      {request.part_numbers.map(part => <span className="chip" key={part}>{part}</span>)}
      <select aria-label={t("Category")} value={request.category} disabled={busy} onChange={event => void call('', { category: event.target.value }, 'PATCH')}>
        {categories.map(value => <option key={value} value={value}>{code(value)}</option>)}</select>
    </div>

    {analysis && <div className="request-grid">
      <div className="card-block"><h3><Icon name="spark" size={16} />{t("Summary")}</h3>
        <ul className="points">{analysis.key_points.map((point, index) => <li key={index}>{point}</li>)}</ul>
        {analysis.findings.length > 0 && <ul className="findings">{analysis.findings.map((finding, index) => <li key={index}>{typeof finding === 'string' ? finding : render(finding.template, finding.params, finding.text)}</li>)}</ul>}
        {analysis.missing.length > 0 && <p className="warning-text">{t("Missing information:")} {analysis.missing.map(item => code(item)).join(', ')}</p>}
        {analysis.notes.map((item, index) => <p className="muted" key={index}>{t(item)}</p>)}
        <small className="muted">{t("Interpreter:")} {analysis.interpreter === 'rules' ? t('Rule-based (offline)') : analysis.interpreter}</small>
      </div>
      <div className="card-block"><h3><Icon name="list" size={16} />{t("Linked records")}</h3>
        {analysis.related_standards.map(item => <button key={item.id} className="link-row" onClick={() => onOpenStandard(item.lineage)}>
          <Icon name="book" size={14} /><span>{item.part_number} · {item.document_number} {t('Rev')} {item.revision}</span><Icon name="next" size={14} /></button>)}
        {analysis.related_inspections.map(item => <button key={item.id} className="link-row" onClick={() => onOpenInspection(item.id)}>
          <Icon name="list" size={14} /><span>{item.lot} · {item.date}{item.matched ? '' : ` · ${t('recent')}`}</span><StatusBadge value={item.overall_result} /></button>)}
        {!analysis.related_standards.length && !analysis.related_inspections.length && <p className="muted">{t("No matching records. Set the customer and part.")}</p>}
      </div>
    </div>}

    {analysis && analysis.proposed_actions.length > 0 && <div className="card-block"><h3><Icon name="check" size={16} />{t("Proposed actions")}</h3>
      {analysis.proposed_actions.map((action, index) => <div className="action-row" key={index}>
        <Icon name={actionIcons[action.kind] ?? 'check'} size={16} /><span>{render(action.template, action.params, action.label)}</span>
        {action.kind === 'open_revision' && action.target_id && <button className="small primary" onClick={() => onOpenStandard(standardLineage(action.target_id), action.instruction ?? undefined)}>{t("Start revision")}</button>}
        {['review_inspection', 'reinspect', 'send_report'].includes(action.kind) && action.target_id && <button className="small" onClick={() => onOpenInspection(action.target_id!)}>{t("Open inspection")}</button>}
      </div>)}</div>}

    <div className="card-block reply">
      <div className="reply-head"><h3><Icon name="send" size={16} />{request.status === 'RESPONDED' || request.status === 'CLOSED' ? t("Sent response") : t("Response draft")}</h3>
        <div className="doc-language" role="group" aria-label={t("Reply language")}>{languages.map(item => <button type="button" key={item.code} disabled={busy}
          className={request.response_language === item.code ? 'selected' : ''} title={t("Regenerate in this language")}
          onClick={() => void call('/analyze', { response_language: item.code as ReplyLanguage })}>{item.short}</button>)}</div></div>
      <textarea rows={14} maxLength={20000} value={draft} aria-label={t("Response draft")} onChange={event => setDraft(event.target.value)} disabled={busy} />
      <div className="inline wrap">
        <button disabled={busy || !dirty} onClick={() => void saveDraft()}><Icon name="check" />{t("Save draft")}</button>
        <button disabled={!draft} onClick={() => { void navigator.clipboard?.writeText(draft); setCopied(true); window.setTimeout(() => setCopied(false), 1500); }}><Icon name="file" />{copied ? t("Copied") : t("Copy")}</button>
        <a className="button" href={`/api/customer-requests/${request.id}/response.eml`} onClick={event => { if (dirty) { event.preventDefault(); void saveDraft().then(() => { window.location.href = `/api/customer-requests/${request.id}/response.eml`; }); } }}><Icon name="download" />.eml</a>
        <button className="regen ghost" disabled={busy} onClick={() => void call('/analyze', { response_language: request.response_language })}><Icon name="refresh" />{t("Re-analyze")}</button>
      </div>
    </div>

    <details className="card-block original"><summary>{t("Original request")} · {request.attachments.length} {t("attachments")}</summary>
      <pre className="original-text">{request.body}</pre>
      <div className="inline wrap">{request.attachments.map(item => <a key={item.stored_name} className="button small" href={`/api/customer-requests/${request.id}/attachments/${item.stored_name}`}><Icon name="clip" size={14} />{item.filename}</a>)}</div>
    </details>

    <div className="card-block"><h3><Icon name="history" size={16} />{t("History")}</h3>
      <ol className="events">{[...request.events].reverse().map((event, index) => <li key={index}><strong>{code(`REQUEST_${event.kind}`)}</strong> · {new Date(event.at).toLocaleString(locale)} · {event.actor}
        {event.note && <span className="muted"> — {event.kind === 'ANALYZED' && event.note === 'rules' ? t('Rule-based (offline)') : event.note}</span>}
        {Object.entries(event.changes ?? {}).map(([field, change]) => <span key={field} className="muted"> · {code(field)}: {code(String(change.before ?? '-'))} → {code(String(change.after ?? '-'))}</span>)}</li>)}</ol>
      <div className="composer"><input value={note} maxLength={5000} placeholder={t("Add a note (call, meeting, decision)...")} aria-label={t("Note")} onChange={event => setNote(event.target.value)} />
        <button disabled={busy || !note.trim()} onClick={() => void call('/notes', { note }).then(() => setNote(''))}><Icon name="plus" />{t("Add note")}</button></div>
    </div>

    <div className="action-bar">
      <div className="inline">
        {request.status === 'OPEN' && <button disabled={busy} onClick={() => void call('', { status: 'IN_PROGRESS' }, 'PATCH')}><Icon name="play" />{t("Start handling")}</button>}
        {request.status !== 'CLOSED' ? <button className="ghost" disabled={busy} onClick={() => void call('', { status: 'CLOSED' }, 'PATCH')}><Icon name="x" />{t("Close request")}</button>
          : <button disabled={busy} onClick={() => void call('', { status: 'OPEN' }, 'PATCH')}><Icon name="undo" />{t("Reopen")}</button>}
      </div>
      {request.status !== 'CLOSED' && <button className="primary big" disabled={busy || !draft.trim()} onClick={() => void call('/respond', { final_response: draft })}><Icon name="send" />{t("Mark as responded")}</button>}
    </div>
  </section>;
}

export default function RequestsView({ selected, creatingFor, busy, perform, onSelect, onCreate, onOpenCustomer, onOpenInspection, onOpenStandard }: {
  selected: string | null; creatingFor: string | null; busy: boolean; perform: (task: () => Promise<void>) => Promise<void>;
  onSelect: (id: string | null) => void; onCreate: (customer: string | null) => void;
  onOpenCustomer: (name: string) => void; onOpenInspection: (id: string) => void; onOpenStandard: (lineage: string, instruction?: string) => void;
}) {
  const { t, locale } = useLanguage();
  const [items, setItems] = useState<RequestSummary[]>([]);
  const [customers, setCustomers] = useState<CustomerOverview[]>([]);
  const [filter, setFilter] = useState<'active' | 'RESPONDED' | 'CLOSED' | 'all'>('active');
  const [search, setSearch] = useState('');

  async function load() {
    const [next, known] = await Promise.all([api<RequestSummary[]>('/customer-requests'), api<CustomerOverview[]>('/customers')]);
    setItems(next); setCustomers(known);
  }
  useEffect(() => { void perform(load); }, [selected, creatingFor]);

  if (creatingFor !== null) return <RequestForm customer={creatingFor} customers={customers} busy={busy} perform={perform}
    onCreated={request => { onCreate(null); onSelect(request.id); }} onCancel={() => onCreate(null)} />;
  if (selected) return <RequestDetail id={selected} busy={busy} perform={perform} onBack={() => onSelect(null)} onChanged={load}
    onOpenCustomer={onOpenCustomer} onOpenInspection={onOpenInspection} onOpenStandard={onOpenStandard} />;

  const counts = {
    active: items.filter(item => item.status === 'OPEN' || item.status === 'IN_PROGRESS').length,
    RESPONDED: items.filter(item => item.status === 'RESPONDED').length,
    CLOSED: items.filter(item => item.status === 'CLOSED').length,
    all: items.length,
  };
  const visible = items
    .filter(item => filter === 'all' || (filter === 'active' ? item.status === 'OPEN' || item.status === 'IN_PROGRESS' : item.status === filter))
    .filter(item => `${item.subject} ${item.customer} ${item.part_numbers.join(' ')}`.toLowerCase().includes(search.toLowerCase()));

  return <>
    <header className="page-header"><h1>{t("Customer requests")}</h1>
      <button className="primary big" disabled={busy} onClick={() => onCreate('')}><Icon name="plus" />{t("New request")}</button></header>
    <div className="filter-tiles four" role="group" aria-label={t("Filter requests")}>
      {([['active', 'In progress'], ['RESPONDED', 'Responded'], ['CLOSED', 'Closed'], ['all', 'All']] as const).map(([value, label]) =>
        <button key={value} aria-pressed={filter === value} className={filter === value ? 'selected' : ''} onClick={() => setFilter(value)}><strong>{counts[value]}</strong><span>{t(label)}</span></button>)}
    </div>
    <section className="panel">
      <div className="toolbar"><input className="search" aria-label={t("Search requests")} placeholder={t("Search subject / customer / part")} value={search} onChange={event => setSearch(event.target.value)} /></div>
      {items.length === 0 ? <div className="empty"><button className="primary big" onClick={() => onCreate('')}><Icon name="plus" />{t("New request")}</button></div>
        : visible.length === 0 ? <p className="muted center">{t("No matching requests.")}</p>
          : <div className="inspection-list">{visible.map(item => <button key={item.id} className="inspection-row request-row" disabled={busy} onClick={() => onSelect(item.id)}>
            <div><strong>{item.priority === 'HIGH' && <span className="priority-dot" />}{item.subject}</strong>
              <small>{item.customer || t('No customer')}{item.part_numbers.length ? ` · ${item.part_numbers.join(', ')}` : ''} · {new Date(item.received_at).toLocaleDateString(locale)}{item.attachments ? ` · 📎${item.attachments}` : ''}</small></div>
            <StatusBadge value={item.category} /><StatusBadge value={item.status} /><Icon name="next" size={16} /></button>)}</div>}
    </section>
  </>;
}
