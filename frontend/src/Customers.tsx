import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { useLanguage } from './language';
import { languages } from './translations';
import type { Contact, CustomerDetail, CustomerOverview, CustomerRecord, ReplyLanguage } from './types';

interface TraceCase {
  id: string; kind: 'NCR' | 'DEFECT'; part_numbers: string[]; created_at: string;
  input?: { defect: string }; ncr?: { header: Record<string, string> };
  analysis: { flow: { name: string }; candidates: { step: string; label?: string | null; names?: Record<string, string> | null }[] };
}

interface LimitApproval {
  id: string; customer: string; model: string; part: string; filename: string; created_at: string; created_by: string;
  samples: { no: number; defect?: string; defect_en?: string; decision?: 'OK' | 'NG' | 'HOLD'; decision_reason?: string; detect_process?: string; cause_process?: string }[];
}

const emptyContact: Contact = { name: '', role: '', email: '', phone: '' };
const timelineIcons: Record<string, string> = { request: 'send', standard: 'book', inspection: 'list' };

export const codeLabels: Record<string, string> = {
  OPEN: 'Open', IN_PROGRESS: 'In progress', RESPONDED: 'Responded', CLOSED: 'Closed',
  COMPLAINT: 'Complaint', SPEC_CHANGE: 'Spec change', INQUIRY: 'Inquiry', DELIVERY: 'Delivery', REPORT_REQUEST: 'Report request', OTHER: 'Other',
  EMAIL: 'E-mail', DOCUMENT: 'Document', PHONE: 'Phone', MEETING: 'Meeting',
  HIGH: 'Urgent', NORMAL: 'Normal', LOW: 'Low',
  PASS: 'PASS', FAIL: 'FAIL', REVIEW: 'Review required', N_A: 'Not applicable',
  CREATED: 'Created', EVALUATED: 'Evaluating', NEEDS_INPUT: 'Input required', AWAITING_REVIEW: 'Awaiting inspection review',
  AWAITING_REPORT_APPROVAL: 'Awaiting report approval', APPROVED: 'Approved', CANCELLED: 'Cancelled', ERROR: 'Processing error',
  REQUEST_RECEIVED: 'Request received', REQUEST_ANALYZED: 'Analyzed', REQUEST_UPDATED: 'Updated', REQUEST_NOTE: 'Note',
  REQUEST_RESPONDED: 'Response sent', REQUEST_CLOSED: 'Request closed', REQUEST_REOPENED: 'Request reopened',
  STANDARD_CREATED: 'Standard registered', STANDARD_REVISED: 'Standard revised', STANDARD_ACTIVATED: 'Standard activated',
  STANDARD_SUPERSEDED: 'Standard superseded', INSPECTION: 'Inspection',
  missing_part: 'part number', missing_lot: 'lot number', missing_values: 'measured values or photos',
  status: 'Status', priority: 'Priority', category: 'Category', customer: 'Customer', part_numbers: 'Parts', subject: 'Subject',
  due_date: 'Due date', response_language: 'Reply language',
};

export function useCode() {
  const { t } = useLanguage();
  return (value: string) => t(codeLabels[value] ?? value);
}

export function StatusBadge({ value }: { value: string }) {
  const code = useCode();
  return <span className={`badge ${value.toLowerCase()}`}>{code(value)}</span>;
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase() || '?';
}

function CustomerForm({ record, name, busy, onSave, onCancel }: {
  record: CustomerRecord | null; name: string; busy: boolean;
  onSave: (data: Omit<CustomerRecord, 'id' | 'created_at' | 'updated_at'>) => Promise<void>; onCancel: () => void;
}) {
  const { t } = useLanguage();
  const [form, setForm] = useState({
    name: record?.name ?? name, code: record?.code ?? '', country: record?.country ?? '',
    reply_language: (record?.reply_language ?? 'en') as ReplyLanguage,
    contacts: record?.contacts.length ? record.contacts : [{ ...emptyContact }], notes: record?.notes ?? '',
  });
  function setContact(index: number, field: keyof Contact, value: string) {
    setForm({ ...form, contacts: form.contacts.map((contact, position) => position === index ? { ...contact, [field]: value } : contact) });
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSave({ ...form, contacts: form.contacts.filter(contact => Object.values(contact).some(value => value.trim())) });
  }
  return <form className="customer-form" onSubmit={submit}><fieldset disabled={busy}>
    <div className="form-grid three">
      <label>{t("Customer name")}<input required maxLength={100} value={form.name} readOnly={Boolean(record || name)} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
      <label>{t("Code")}<input maxLength={40} value={form.code} onChange={event => setForm({ ...form, code: event.target.value })} /></label>
      <label>{t("Country / site")}<input maxLength={60} value={form.country} onChange={event => setForm({ ...form, country: event.target.value })} /></label>
    </div>
    <div className="field-row"><span className="field-label">{t("Reply language")}</span>
      <div className="doc-language">{languages.map(item => <button type="button" key={item.code} aria-pressed={form.reply_language === item.code}
        className={form.reply_language === item.code ? 'selected' : ''} onClick={() => setForm({ ...form, reply_language: item.code })}>{item.short}</button>)}</div></div>
    <div className="field-row column"><span className="field-label">{t("Contacts")}</span>
      {form.contacts.map((contact, index) => <div className="contact-row" key={index}>
        <input placeholder={t("Name")} aria-label={t("Name")} maxLength={100} value={contact.name} onChange={event => setContact(index, 'name', event.target.value)} />
        <input placeholder={t("Role")} aria-label={t("Role")} maxLength={100} value={contact.role} onChange={event => setContact(index, 'role', event.target.value)} />
        <input placeholder={t("E-mail")} aria-label={t("E-mail")} type="email" maxLength={200} value={contact.email} onChange={event => setContact(index, 'email', event.target.value)} />
        <input placeholder={t("Phone")} aria-label={t("Phone")} maxLength={60} value={contact.phone} onChange={event => setContact(index, 'phone', event.target.value)} />
        <button type="button" className="icon-button ghost" aria-label={t("Remove")} onClick={() => setForm({ ...form, contacts: form.contacts.filter((_, position) => position !== index) })}><Icon name="x" /></button>
      </div>)}
      <button type="button" className="small" onClick={() => setForm({ ...form, contacts: [...form.contacts, { ...emptyContact }] })}><Icon name="plus" size={14} />{t("Add contact")}</button></div>
    <label>{t("Notes")}<textarea rows={3} maxLength={5000} value={form.notes} onChange={event => setForm({ ...form, notes: event.target.value })} /></label>
    <div className="inline end"><button type="button" className="ghost" onClick={onCancel}>{t("Cancel")}</button><button className="primary" type="submit"><Icon name="check" />{t("Save")}</button></div>
  </fieldset></form>;
}

export default function CustomersView({ selected, busy, perform, onSelect, onOpenRequest, onNewRequest, onOpenInspection, onOpenStandard }: {
  selected: string | null; busy: boolean; perform: (task: () => Promise<void>) => Promise<void>;
  onSelect: (name: string | null) => void; onOpenRequest: (id: string) => void; onNewRequest: (customer: string) => void;
  onOpenInspection: (id: string) => void; onOpenStandard: (lineage: string) => void;
}) {
  const { t, locale, language } = useLanguage();
  const [customers, setCustomers] = useState<CustomerOverview[]>([]);
  const [detail, setDetail] = useState<CustomerDetail | null>(null);
  const [section, setSection] = useState<'overview' | 'parts' | 'requests' | 'inspections' | 'limits' | 'cases' | 'history'>('overview');
  const [cases, setCases] = useState<TraceCase[]>([]);
  const [approvals, setApprovals] = useState<LimitApproval[]>([]);
  const [part, setPart] = useState<string | null>(null);
  const [editing, setEditing] = useState<'new' | 'edit' | null>(null);
  const [search, setSearch] = useState('');

  async function loadList() { setCustomers(await api<CustomerOverview[]>('/customers')); }
  async function loadDetail(name: string, filter: string | null) {
    const query = new URLSearchParams({ name, ...(filter ? { part: filter } : {}) });
    setDetail(await api<CustomerDetail>(`/customers/detail?${query}`));
    setApprovals(await api<LimitApproval[]>(`/limit-approvals?${new URLSearchParams({ customer: name })}`).catch(() => []));
    setCases(await api<TraceCase[]>(`/traceback/cases?${new URLSearchParams({ customer: name })}`).catch(() => []));
  }

  useEffect(() => { loadList().catch(() => undefined); }, []);
  useEffect(() => {
    setDetail(null); setPart(null); setSection('overview'); setEditing(null);
    if (selected) void perform(() => loadDetail(selected, null));
  }, [selected]);

  async function save(data: Omit<CustomerRecord, 'id' | 'created_at' | 'updated_at'>) {
    await perform(async () => {
      const record = detail?.record;
      if (record) await api(`/customers/${record.id}`, data, 'PUT');
      else await api('/customers', data);
      setEditing(null);
      await loadList();
      if (selected) await loadDetail(selected, part);
      else onSelect(data.name);
    });
  }

  function filterPart(next: string | null) {
    setPart(next);
    if (selected) void perform(() => loadDetail(selected, next));
  }

  if (!selected) {
    const visible = customers.filter(item => item.name.toLowerCase().includes(search.toLowerCase()) || item.record?.code.toLowerCase().includes(search.toLowerCase()));
    return <>
      <header className="page-header"><h1>{t("Customers")}</h1>
        <button className="primary big" disabled={busy} onClick={() => setEditing('new')}><Icon name="plus" />{t("Add customer")}</button></header>
      {editing === 'new' && <section className="panel"><h2 className="step-title">{t("Add customer")}</h2>
        <CustomerForm record={null} name="" busy={busy} onSave={save} onCancel={() => setEditing(null)} /></section>}
      {customers.length > 3 && <div className="toolbar"><input className="search" aria-label={t("Search customers")} placeholder={t("Search customers")} value={search} onChange={event => setSearch(event.target.value)} /></div>}
      {customers.length === 0 ? <section className="panel empty"><button className="primary big" onClick={() => setEditing('new')}><Icon name="plus" />{t("Add customer")}</button></section>
        : <div className="customer-grid">{visible.map(item => <button key={item.name} className="customer-card" disabled={busy} onClick={() => onSelect(item.name)}>
          <div className="customer-head"><span className="avatar">{initials(item.name)}</span>
            <div><strong>{item.name}</strong><small>{[item.record?.code, item.record?.country].filter(Boolean).join(' · ') || (item.registered ? '' : t('Not registered'))}</small></div>
            {item.open_requests > 0 && <span className="badge warning">{item.open_requests} {t("open")}</span>}</div>
          <div className="customer-stats">
            <span><strong>{item.parts.length}</strong>{t("Parts")}</span>
            <span><strong>{item.requests}</strong>{t("Requests")}</span>
            <span><strong>{item.inspections}</strong>{t("Inspections")}</span>
            <span className={item.failed_inspections ? 'bad' : ''}><strong>{item.failed_inspections}</strong>{t("Failed")}</span>
          </div>
          <div className="customer-foot">{item.parts.slice(0, 3).map(entry => <span className="chip" key={entry.part_number}>{entry.part_number}</span>)}
            {item.parts.length > 3 && <span className="chip">+{item.parts.length - 3}</span>}
            <small>{item.last_activity ? new Date(item.last_activity).toLocaleDateString(locale) : ''}</small></div>
        </button>)}</div>}
    </>;
  }

  if (!detail) return <section className="panel"><button className="ghost" onClick={() => onSelect(null)}><Icon name="back" />{t("Back")}</button></section>;
  const record = detail.record;
  const sections = [
    ['overview', 'Overview'], ['parts', 'Parts & standards'], ['requests', 'Requests'], ['inspections', 'Inspections'], ['limits', 'Limit samples'], ['cases', 'Root-cause analyses'], ['history', 'History'],
  ] as const;

  return <section className="panel customer-page">
    <div className="panel-top"><button className="ghost" onClick={() => onSelect(null)}><Icon name="back" />{t("Back")}</button>
      <span className="avatar large">{initials(detail.name)}</span>
      <div className="title-block"><h1>{detail.name}</h1><small>{[record?.code, record?.country, record ? languages.find(item => item.code === record.reply_language)?.label : t('Not registered')].filter(Boolean).join(' · ')}</small></div>
      <div className="inline push"><button disabled={busy} onClick={() => setEditing('edit')}><Icon name="edit" />{record ? t("Edit") : t("Register")}</button>
        <button className="primary" disabled={busy} onClick={() => onNewRequest(detail.name)}><Icon name="plus" />{t("New request")}</button></div></div>

    {editing === 'edit' && <div className="inset"><CustomerForm record={record} name={detail.name} busy={busy} onSave={save} onCancel={() => setEditing(null)} /></div>}

    <div className="filter-tiles four">
      {([['parts', detail.parts.length, 'Parts'], ['requests', detail.open_requests, 'Open requests'], ['inspections', detail.inspections, 'Inspections'], ['inspections', detail.failed_inspections, 'Failed']] as const).map(([target, value, label], index) =>
        <button key={index} className={label === 'Failed' && value ? 'bad' : ''} onClick={() => setSection(target)}><strong>{value}</strong><span>{t(label)}</span></button>)}
    </div>

    <div className="tabs" role="tablist">{sections.map(([value, label]) => <button key={value} role="tab" aria-selected={section === value}
      className={section === value ? 'selected' : ''} onClick={() => setSection(value)}>{t(label)}</button>)}</div>

    {(section === 'history' || section === 'requests' || section === 'inspections') && detail.parts.length > 1 && <div className="quick">
      <button className={part === null ? 'selected' : ''} onClick={() => filterPart(null)}>{t("All parts")}</button>
      {detail.parts.map(entry => <button key={entry.part_number} className={part === entry.part_number ? 'selected' : ''} onClick={() => filterPart(entry.part_number)}>{entry.part_number}</button>)}</div>}

    {section === 'overview' && <div className="overview-grid">
      <div className="card-block"><h3>{t("Contacts")}</h3>
        {record?.contacts.length ? record.contacts.map((contact, index) => <div className="contact" key={index}><strong>{contact.name || '-'}</strong><small>{contact.role}</small>
          {contact.email && <a href={`mailto:${contact.email}`}>{contact.email}</a>}{contact.phone && <span>{contact.phone}</span>}</div>)
          : <button className="small" onClick={() => setEditing('edit')}><Icon name="plus" size={14} />{t("Add contact")}</button>}
        {record?.notes && <p className="muted pre">{record.notes}</p>}</div>
      <div className="card-block"><h3>{t("Recent activity")}</h3><Timeline entries={detail.timeline.slice(0, 6)} onOpenRequest={onOpenRequest} onOpenInspection={onOpenInspection} onOpenStandard={onOpenStandard} />
        {detail.timeline.length > 6 && <button className="small" onClick={() => setSection('history')}>{t("Show all")}</button>}</div>
    </div>}

    {section === 'parts' && <div className="part-grid">{detail.parts.map(entry => <article className="part-card" key={entry.part_number}>
      <div className="card-head"><strong>{entry.part_number}</strong>{entry.last_result && <StatusBadge value={entry.last_result} />}</div>
      <p className="muted">{entry.product_name || '-'}</p>
      <div className="inline">{entry.standard ? <span className="chip">{entry.standard.document_number} · {t('Rev')} {entry.standard.revision}</span> : <span className="chip">{t("No active standard")}</span>}
        {entry.draft_revision && <span className="badge draft">{t('Draft')} {entry.draft_revision}</span>}</div>
      <p className="meta">{entry.versions} {t("versions")} · {entry.inspections} {t("inspections")} · {entry.open_requests} {t("open requests")}</p>
      <div className="card-actions">{entry.standard && <button className="primary" onClick={() => onOpenStandard(entry.standard!.lineage)}><Icon name="book" />{t("Standard")}</button>}
        <button onClick={() => { setSection('history'); filterPart(entry.part_number); }}><Icon name="history" />{t("History")}</button></div>
    </article>)}</div>}

    {section === 'requests' && (detail.request_list.length ? <div className="inspection-list">{detail.request_list.map(item => <button key={item.id} className="inspection-row" onClick={() => onOpenRequest(item.id)}>
      <div><strong>{item.priority === 'HIGH' && <span className="priority-dot" />}{item.subject}</strong><small>{item.part_numbers.join(', ') || '-'} · {new Date(item.received_at).toLocaleDateString(locale)}</small></div>
      <StatusBadge value={item.category} /><StatusBadge value={item.status} /><Icon name="next" size={16} /></button>)}</div>
      : <div className="empty"><button className="primary" onClick={() => onNewRequest(detail.name)}><Icon name="plus" />{t("New request")}</button></div>)}

    {section === 'inspections' && (detail.inspection_list.length ? <div className="inspection-list">{detail.inspection_list.map(item => <button key={item.id} className="inspection-row" onClick={() => onOpenInspection(item.id)}>
      <div><strong>{item.lot}</strong><small>{item.part_number} · {t('Rev')} {item.revision} · {new Date(item.created_at).toLocaleDateString(locale)}</small></div>
      <StatusBadge value={item.overall_result} /><StatusBadge value={item.status} /><Icon name="next" size={16} /></button>)}</div>
      : <p className="muted center">{t("No inspections yet.")}</p>)}

    {section === 'limits' && (approvals.length ? approvals.map(approval => <article className="panel inner" key={approval.id}>
      <div className="step-row"><h3>{approval.model} · {approval.part}</h3><small className="muted">{approval.filename} · {new Date(approval.created_at).toLocaleDateString(locale)} · {approval.created_by}</small></div>
      <div className="limit-summary">{(['OK', 'NG', 'HOLD'] as const).map(decision => <span key={decision} className={`stat ${decision.toLowerCase()}`}>
        <strong>{approval.samples.filter(sample => sample.decision === decision).length}</strong>{t(decision === 'HOLD' ? 'On hold' : decision)}</span>)}</div>
      <div className="table-scroll"><table className="limit-table"><thead><tr><th>No.</th><th>{t("Defect")}</th><th>{t("Decision")}</th><th>{t("Reason")}</th><th>{t("Detected / caused at")}</th></tr></thead>
        <tbody>{approval.samples.map(sample => <tr key={sample.no}><td>{sample.no}</td><td><strong>{sample.defect}</strong><small>{sample.defect_en}</small></td>
          <td>{sample.decision && <span className={`badge ${sample.decision === 'OK' ? 'pass' : sample.decision === 'NG' ? 'fail' : 'review'}`}>{t(sample.decision === 'HOLD' ? 'On hold' : sample.decision)}</span>}</td>
          <td className="pre">{sample.decision_reason}</td><td>{sample.detect_process} / {sample.cause_process}</td></tr>)}</tbody></table></div>
    </article>) : <p className="muted center">{t("No limit samples yet. Read a limit-sample approval in Customer forms.")}</p>)}

    {section === 'cases' && (cases.length ? <div className="inspection-list">{cases.map(item => <div key={item.id} className="inspection-row static">
      <div><strong>{item.kind === 'NCR' ? `${item.ncr?.header.ncr_no ?? 'NCR'} · ${item.ncr?.header.problem ?? ''}` : item.input?.defect}</strong>
        <small>{item.part_numbers.join(', ') || '-'} · {new Date(item.created_at).toLocaleDateString(locale)} · {item.analysis.flow.name}</small></div>
      <span className="chip">1. {item.analysis.candidates[0]?.label || item.analysis.candidates[0]?.names?.[language] || item.analysis.candidates[0]?.step || '-'}</span></div>)}</div>
      : <p className="muted center">{t("No saved root-cause analyses yet.")}</p>)}

    {section === 'history' && <Timeline entries={detail.timeline} onOpenRequest={onOpenRequest} onOpenInspection={onOpenInspection} onOpenStandard={onOpenStandard} />}
  </section>;
}

function Timeline({ entries, onOpenRequest, onOpenInspection, onOpenStandard }: {
  entries: CustomerDetail['timeline']; onOpenRequest: (id: string) => void; onOpenInspection: (id: string) => void; onOpenStandard: (lineage: string) => void;
}) {
  const { t, locale } = useLanguage();
  const code = useCode();
  if (!entries.length) return <p className="muted">{t("No activity yet.")}</p>;
  return <ol className="timeline">{entries.map((entry, index) => <li key={index} className={entry.ref.type}>
    <span className="timeline-icon"><Icon name={timelineIcons[entry.ref.type]} size={14} /></span>
    <button className="timeline-body" onClick={() => entry.ref.type === 'request' ? onOpenRequest(entry.ref.id) : entry.ref.type === 'inspection' ? onOpenInspection(entry.ref.id) : onOpenStandard(entry.ref.id)}>
      <span className="timeline-top"><strong>{code(entry.kind)}</strong><small>{new Date(entry.at).toLocaleString(locale)}</small></span>
      <span>{entry.title}</span>
      <small>{entry.detail.split(' · ').map(part => code(part)).join(' · ')}{entry.actor ? ` · ${entry.actor}` : ''}</small>
    </button></li>)}</ol>;
}
