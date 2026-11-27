import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, requestId } from './api';
import { DocLanguagePicker, reportLanguages } from './docLanguage';
import { Icon } from './icons';
import { displayInspection, displayStandard, setGlossary } from './legacyDisplay';
import { LanguageToggle, useLanguage } from './language';
import AISettings from './AISettings';
import CustomersView from './Customers';
import FormIntake from './FormIntake';
import Home from './Home';
import Programs from './Programs';
import Tasks from './Tasks';
import type { TaskLink } from './Tasks';
import Traceback from './Traceback';
import type { TraceSeed } from './Traceback';
import RequestsView from './Requests';
import StandardWorkspace from './StandardWorkspace';
import type { Language } from './translations';
import type { Inspection, InspectionSummary, Observation, Rule, Standard, User } from './types';

const symbols = { lt: '<', lte: '≤', gt: '>', gte: '≥' };
const labels: Record<string, string> = {
  CREATED: 'Created', EVALUATED: 'Evaluating', NEEDS_INPUT: 'Input required', AWAITING_REVIEW: 'Awaiting inspection review',
  AWAITING_REPORT_APPROVAL: 'Awaiting report approval', APPROVED: 'Approved', CANCELLED: 'Cancelled', ERROR: 'Processing error',
  PASS: 'PASS', FAIL: 'FAIL', REVIEW: 'Review required', N_A: 'Not applicable',
};
const steps = ['Measure', 'Review', 'Report', 'Done'];
const stepOf: Record<string, number> = {
  CREATED: 0, EVALUATED: 0, NEEDS_INPUT: 0, ERROR: 0, AWAITING_REVIEW: 1, AWAITING_REPORT_APPROVAL: 2, APPROVED: 3, CANCELLED: 3,
};
const pendingStatuses = ['AWAITING_REVIEW', 'AWAITING_REPORT_APPROVAL', 'NEEDS_INPUT'];

function Badge({ value }: { value: string }) {
  const { t } = useLanguage();
  return <span className={`badge ${value.toLowerCase()}`}>{t(labels[value] ?? value)}</span>;
}

function Stepper({ status }: { status: string }) {
  const { t } = useLanguage();
  const current = stepOf[status] ?? 0;
  return <ol className="stepper" aria-label={t("Progress")}>{steps.map((step, index) => <li key={step}
    className={index < current || status === 'APPROVED' ? 'done' : index === current ? 'current' : ''}>
    <span>{index < current || status === 'APPROVED' ? <Icon name="check" size={14} /> : index + 1}</span>{t(step)}</li>)}</ol>;
}

function Segmented<T extends string>({ value, options, onChange, label, disabled }: {
  value: T | ''; options: { value: T; label: string; tone?: string }[]; onChange: (value: T) => void; label: string; disabled?: boolean;
}) {
  return <div className="segmented" role="group" aria-label={label}>{options.map(option => <button type="button" key={option.value}
    disabled={disabled} aria-pressed={value === option.value}
    className={`${value === option.value ? 'selected' : ''} ${option.tone ?? ''}`} onClick={() => onChange(option.value)}>{option.label}</button>)}</div>;
}

function RuleFields({ rules, values, onChange }: {
  rules: Rule[]; values: Record<string, Observation>; onChange: (values: Record<string, Observation>) => void;
}) {
  const { t } = useLanguage();
  return <div className="rule-grid">{rules.filter(rule => rule.kind !== 'NOT_APPLICABLE').map(rule => <section className="rule-card" key={rule.id}>
    <div className="rule-title"><span>{rule.area}</span><h3>{rule.item}</h3></div>
    {rule.kind === 'NUMERIC' && rule.conditions.map(condition => <label key={condition.field} className="measure">
      <span>{condition.label}<small>{symbols[condition.operator]} {condition.limit}</small></span>
      <div className="unit-field"><input inputMode="decimal" placeholder="0.00"
        aria-label={`${rule.item} ${condition.label}`}
        value={values[rule.id]?.measurements?.[condition.field] ?? ''}
        onChange={event => onChange({ ...values, [rule.id]: {
          ...values[rule.id],
          measurements: { ...values[rule.id]?.measurements, [condition.field]: event.target.value || null },
          units: { ...values[rule.id]?.units, [condition.field]: condition.unit },
        } })} /><span>{condition.unit}</span></div>
    </label>)}
    {rule.kind === 'PROHIBITED' && <Segmented label={`${rule.item} ${t('Defect present')}`}
      value={values[rule.id]?.defect_present == null ? '' : values[rule.id]?.defect_present ? 'yes' : 'no'}
      options={[{ value: 'no', label: t('No defect'), tone: 'good' }, { value: 'yes', label: t('Defect found'), tone: 'bad' }]}
      onChange={value => onChange({ ...values, [rule.id]: { defect_present: value === 'yes' } })} />}
    {rule.kind === 'UNRESOLVED' && <p className="warning-text">{t("Unresolved criteria · Approval unavailable")}</p>}
  </section>)}</div>;
}

export default function App({ user, onLogout, loggingOut }: {
  user: User; onLogout: () => Promise<void>; loggingOut: boolean;
}) {
  const { t, language, locale } = useLanguage();
  const [tab, setTab] = useState<'home' | 'tasks' | 'inspections' | 'standards' | 'forms' | 'trace' | 'customers' | 'requests' | 'ai' | 'programs'>('home');
  const [tasksView, setTasksView] = useState<'board' | 'calendar' | 'automation'>('board');
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantMessage, setAssistantMessage] = useState<string | null>(null);
  const [homeKey, setHomeKey] = useState(0);
  const [formReading, setFormReading] = useState<string | null>(null);
  const [traceCase, setTraceCase] = useState<string | null>(null);
  const [programSelected, setProgramSelected] = useState<string | null>(null);
  const [programsView, setProgramsView] = useState<'list' | 'profile'>('list');
  const [programsKey, setProgramsKey] = useState(0);
  const [programsRegion, setProgramsRegion] = useState('');
  const [traceSeed, setTraceSeed] = useState<TraceSeed | null>(null);
  const [customerName, setCustomerName] = useState<string | null>(null);
  const [requestSelected, setRequestSelected] = useState<string | null>(null);
  const [requestCreating, setRequestCreating] = useState<string | null>(null);
  const [familyInstruction, setFamilyInstruction] = useState('');
  const [rawStandards, setStandards] = useState<Standard[]>([]);
  const [inspections, setInspections] = useState<InspectionSummary[]>([]);
  const [rawSelected, setSelected] = useState<Inspection | null>(null);
  const standards = rawStandards.map(standard => displayStandard(standard, language));
  const selected = rawSelected ? displayInspection(rawSelected, language) : null;
  const [creating, setCreating] = useState(false);
  const [standardId, setStandardId] = useState('demo-cover-en-v1');
  const [lot, setLot] = useState('');
  const [inspector, setInspector] = useState(user.display_name);
  const [values, setValues] = useState<Record<string, Observation>>({});
  const actor = user.display_name;
  const [reason, setReason] = useState('');
  const [closing, setClosing] = useState<'reject' | 'cancel' | null>(null);
  const [decisions, setDecisions] = useState<Record<string, { result: string; reason: string }>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'pending' | 'approved'>('all');
  const [family, setFamily] = useState<string | null>(null);
  const [docLanguage, setDocLanguage] = useState<Language | null>(null);
  const selectedStandard = standards.find(standard => standard.id === standardId);
  const activeStandards = standards.filter(standard => standard.status === 'ACTIVE');
  const lineageOf = (standard: Standard) => standard.family_id ?? standard.id;
  const families = Object.values(standards.reduce<Record<string, Standard[]>>((groups, standard) => {
    (groups[lineageOf(standard)] ??= []).push(standard);
    return groups;
  }, {})).map(group => ({
    versions: group,
    head: group.find(standard => standard.status === 'DRAFT') ?? group.find(standard => standard.status === 'ACTIVE')
      ?? [...group].sort((a, b) => (b.version ?? 1) - (a.version ?? 1))[0],
    active: group.find(standard => standard.status === 'ACTIVE'),
  }));

  const [formFamilies, setFormFamilies] = useState<Set<string>>(new Set());

  async function refresh() {
    const [nextStandards, nextInspections, profiles] = await Promise.all([
      api<Standard[]>('/standards'), api<InspectionSummary[]>('/inspections'), api<{ family_id: string }[]>('/form-profiles').catch(() => []),
    ]);
    setFormFamilies(new Set(profiles.map(profile => profile.family_id)));
    setStandards(nextStandards);
    setInspections(nextInspections);
    setStandardId(current => nextStandards.some(standard => standard.id === current && standard.status === 'ACTIVE')
      ? current : nextStandards.find(standard => standard.status === 'ACTIVE')?.id ?? current);
  }

  const [, setGlossaryVersion] = useState(0);
  useEffect(() => { api<Record<string, Record<string, string>>>('/glossary').then(value => { setGlossary(value); setGlossaryVersion(v => v + 1); }).catch(() => undefined); }, []);
  useEffect(() => { refresh().catch(error => setError(error instanceof Error ? error.message : 'The operation failed.')); }, []);
  const [llm, setLlm] = useState<{ provider: string; external: boolean; allow_internal: boolean } | null>(null);
  const loadHealth = () => { api<{ llm: { provider: string; external: boolean; allow_internal: boolean } }>('/health').then(health => setLlm(health.llm)).catch(() => setLlm(null)); };
  useEffect(loadHealth, []);
  const providerName: Record<string, string> = { openai: 'OpenAI', anthropic: 'Claude' };

  async function perform(task: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try { await task(); }
    catch (error) { setError(error instanceof Error ? error.message : 'The operation failed.'); }
    finally { busyRef.current = false; setBusy(false); }
  }

  function show(state: Inspection) {
    setSelected(state);
    setValues(state.inputs.observations);
    setDecisions({});
    setReason('');
    setClosing(null);
    setCreating(false);
  }

  async function open(identifier: string) {
    show(await api<Inspection>(`/inspections/${identifier}`));
  }

  function startInspection() {
    setTab('inspections');
    setCreating(true);
    setSelected(null);
    setValues({});
    setLot('');
    setInspector(user.display_name);
    setError('');
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    await perform(async () => {
      const state = await api<Inspection>('/inspections', {
        standard_id: standardId, lot, inspector, observations: values,
      });
      show(state);
      await refresh();
    });
  }

  async function action(name: string) {
    if (!selected?.pending) return;
    const identifier = selected.inspection_id;
    await perform(async () => {
      try {
        show(await api<Inspection>(`/inspections/${identifier}/actions`, {
          request_id: requestId(), expected_revision: selected.revision,
          interrupt_id: selected.pending!.id, action: name, reason,
          observations: name === 'correct' ? values : null,
          decisions: name === 'confirm' ? decisions : {},
        }));
      } catch (error) {
        show(await api<Inspection>(`/inspections/${identifier}`));
        throw error;
      } finally { await refresh(); }
    });
  }

  function preset(kind: string) {
    setValues({
      burr: { measurements: { height: kind === 'fail' ? '0.25' : kind === 'warning' ? '0.195' : '0.10' }, units: { height: 'mm' } },
      pore: { measurements: { depth: '0.20', diameter: '0.50' }, units: { depth: 'mm', diameter: 'mm' } },
      crack: { defect_present: false },
    });
  }

  async function importStandard(file: File) {
    await perform(async () => {
      await api('/standards', JSON.parse(await file.text()));
      await refresh();
    });
  }

  function openInspection(identifier: string) {
    setTab('inspections');
    setCreating(false);
    void perform(() => open(identifier));
  }

  function openStandard(lineage: string, instruction?: string) {
    setTab('standards');
    setFamily(lineage);
    setFamilyInstruction(instruction ?? '');
  }

  function openRequest(identifier: string) {
    setTab('requests');
    setRequestCreating(null);
    setRequestSelected(identifier);
  }

  function openLink(link: TaskLink) {
    if (link.type === 'standard') openStandard(link.id);
    else if (link.type === 'request') openRequest(link.id);
    else if (link.type === 'form') { setFormReading(link.id); setTab('forms'); }
    else if (link.type === 'program') { setProgramSelected(link.id); setProgramsView('list'); setTab('programs'); }
    else { setTraceCase(link.id); setTab('trace'); }
  }

  function go(page: string, params: Record<string, string> = {}) {
    if (page === 'home') { setHomeKey(key => key + 1); setTab('home'); }
    else if (page === 'tasks' || page === 'calendar' || page === 'automation') { setTasksView(page === 'tasks' ? 'board' : page); setTab('tasks'); }
    else if (page === 'inspections') { setTab('inspections'); setCreating(false); setSelected(null); }
    else if (page === 'inspection_new') { startInspection(); if (params.standard_id) { setStandardId(params.standard_id); setValues({}); } }
    else if (page === 'standards') { setTab('standards'); setFamily(null); }
    else if (page === 'standard') openStandard(params.id, params.instruction);
    else if (page === 'forms') setTab('forms');
    else if (page === 'trace') setTab('trace');
    else if (page === 'customers') { setTab('customers'); setCustomerName(null); }
    else if (page === 'customer') openCustomer(params.name);
    else if (page === 'requests') { setTab('requests'); setRequestSelected(null); setRequestCreating(null); }
    else if (page === 'request_new') newRequest(params.customer ?? '');
    else if (page === 'ai') setTab('ai');
    else if (page === 'programs' || page === 'profile') { setProgramsView(page === 'profile' ? 'profile' : 'list'); setProgramsRegion(params.region ?? ''); setProgramsKey(key => key + 1); setTab('programs'); }
    void refresh().catch(() => undefined);
  }

  function openCustomer(name: string) {
    setTab('customers');
    setCustomerName(name);
  }

  function newRequest(customer: string) {
    setTab('requests');
    setRequestSelected(null);
    setRequestCreating(customer);
  }

  const counts = {
    all: inspections.length,
    pending: inspections.filter(item => pendingStatuses.includes(item.status)).length,
    approved: inspections.filter(item => item.status === 'APPROVED').length,
  };
  const visible = inspections
    .filter(item => filter === 'all' || (filter === 'pending' ? pendingStatuses.includes(item.status) : item.status === 'APPROVED'))
    .filter(item => `${item.inputs.lot} ${item.standard.part_number}`.toLowerCase().includes(search.toLowerCase()));
  const primary = selected?.pending
    ? selected.pending.kind === 'correction' ? { name: 'correct', label: 'Correct and reevaluate', icon: 'refresh' }
      : selected.pending.kind === 'inspection_review' ? { name: 'confirm', label: 'Confirm inspection', icon: 'check' }
        : { name: 'approve', label: 'Approve report', icon: 'check' }
    : null;

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label={t("YardMate home")}><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 40 40" width="40" height="40"><path d="M6 22h28l-4 9H10z" fill="#c4532f" /><path d="M6 22h28" stroke="#fff" strokeWidth="2" /><path d="M12 22V12h9v10M23 22v-6h6v6" fill="none" stroke="#dbe7ef" strokeWidth="2.4" strokeLinejoin="round" /></svg></span><div>YardMate<small>{t("Shipyard supplier workspace")}</small></div></a>
      <nav>
        <button className={tab === 'home' ? 'active' : ''} onClick={() => go('home')}><Icon name="home" />{t("Home")}</button>
        <p className="nav-group">{t("Shipyard specs")}</p>
        <button className={tab === 'forms' ? 'active' : ''} onClick={() => go('forms')} title={t("Shipyard, coating and owner forms in Korean, English or Chinese → one standard")}><Icon name="scan" />{t("Read a spec form")}</button>
        <button className={tab === 'standards' ? 'active' : ''} onClick={() => go('standards')}><Icon name="book" />{t("Standards library")}</button>
        <button className={tab === 'inspections' ? 'active' : ''} onClick={() => go('inspections')}><Icon name="list" />{t("Inspections & reports")}</button>
        <p className="nav-group">{t("Local support")}</p>
        <button className={tab === 'programs' ? 'active' : ''} onClick={() => go('programs')} title={t("Support notices judged for your company, deadlines as tasks")}><Icon name="grant" />{t("Support programmes")}</button>
        <button className={tab === 'tasks' ? 'active' : ''} onClick={() => go('tasks')}><Icon name="calendar" />{t("Tasks & schedule")}</button></nav>
      <button className="sidebar-cta primary" disabled={busy} onClick={startInspection}><Icon name="plus" />{t("New inspection")}</button>
      <button className={`sidebar-note ${tab === 'ai' ? 'active' : ''}`} onClick={() => setTab('ai')} title={llm && llm.provider !== 'none' && llm.external && !llm.allow_internal ? t("Internal standards and customer requests use the rule-based analysis.") : undefined}>
        <span className={`live-dot ${llm && llm.provider !== 'none' ? 'ai' : ''}`} />
        {llm && llm.provider !== 'none'
          ? `${t("AI connected")}: ${providerName[llm.provider] ?? llm.provider}${llm.external ? '' : ` (${t("local")})`}`
          : t("Local · No external AI")}
        {llm && llm.provider !== 'none' && llm.external && !llm.allow_internal && <small>{t("Internal data stays rule-based")}</small>}
        <small className="settings-link"><Icon name="gear" size={12} /> {t("AI settings")}</small>
      </button>
    </aside>
    <main>
      <div className="account-bar"><span className="chip demo-chip">{t("Demo data")}</span><LanguageToggle /><span className="user-name">{user.display_name}</span>
        <button className="icon-button" aria-label={t("Sign out")} title={t("Sign out")} disabled={busy || loggingOut} onClick={() => void onLogout()}><Icon name="logout" /></button></div>
      {error && <div role="alert" className="error-banner">{t(error)}<button onClick={() => setError('')} aria-label={t("Dismiss error")}><Icon name="x" /></button></div>}
      {busy && <div className="busy" role="status"><span className="spinner" />{t("Processing...")}</div>}

      {tab === 'inspections' && !creating && !selected && <>
        <header className="page-header"><h1>{t("Inspection workspace")}</h1>
          <button className="primary big" disabled={busy} onClick={startInspection}><Icon name="plus" />{t("New inspection")}</button></header>
        <div className="filter-tiles" role="group" aria-label={t("Filter inspections")}>
          {(['all', 'pending', 'approved'] as const).map(key => <button key={key} aria-pressed={filter === key} className={filter === key ? 'selected' : ''} onClick={() => setFilter(key)}>
            <strong>{counts[key]}</strong><span>{t(key === 'all' ? 'All' : key === 'pending' ? 'Pending review' : 'Approved')}</span></button>)}
        </div>
        <section className="panel">
          <div className="toolbar"><input className="search" aria-label={t("Search inspections")} placeholder={t("Search lot / part")} value={search} onChange={event => setSearch(event.target.value)} />
            <button className="icon-button" aria-label={t("Refresh")} title={t("Refresh")} disabled={busy} onClick={() => perform(refresh)}><Icon name="refresh" /></button></div>
          {inspections.length === 0 ? <div className="empty"><button className="primary big" disabled={busy} onClick={startInspection}><Icon name="plus" />{t("Start your first inspection")}</button></div>
            : visible.length === 0 ? <p className="muted center">{t("No matching inspections.")}</p>
              : <div className="inspection-list">{visible.map(item => <button className="inspection-row" key={item.inspection_id} disabled={busy} onClick={() => perform(() => open(item.inspection_id))}>
                <div><strong>{item.inputs.lot}</strong><small>{item.standard.part_number} · {new Date(item.created_at).toLocaleDateString(locale)}</small></div>
                <Badge value={item.overall_result} /><Badge value={item.status} /><Icon name="next" size={16} />
              </button>)}</div>}
        </section>
      </>}

      {tab === 'inspections' && creating && <section className="panel">
        <div className="panel-top"><button className="ghost" onClick={() => setCreating(false)}><Icon name="back" />{t("Back")}</button><h1>{t("New inspection")}</h1></div>
        <form onSubmit={create}><fieldset disabled={busy}>
          <h2 className="step-title"><span>1</span>{t("Choose a standard")}</h2>
          <div className="choice-grid">{activeStandards.map(standard => <button type="button" key={standard.id} aria-pressed={standardId === standard.id}
            className={`choice ${standardId === standard.id ? 'selected' : ''}`} onClick={() => { setStandardId(standard.id); setValues({}); }}>
            <strong>{standard.product_name}</strong><small>{standard.customer} · {standard.part_number}</small><span className="chip">{t('Rev')} {standard.revision}</span></button>)}</div>
          <h2 className="step-title"><span>2</span>{t("Lot and inspector")}</h2>
          <div className="form-grid two"><label>{t("Lot")}<input required maxLength={100} placeholder="DEMO-2026-001" value={lot} onChange={event => setLot(event.target.value)} /></label>
            <label>{t("Inspector name")}<input required maxLength={80} value={inspector} onChange={event => setInspector(event.target.value)} /></label></div>
          <div className="step-row"><h2 className="step-title"><span>3</span>{t("Enter measurements")}</h2>
            {selectedStandard && lineageOf(selectedStandard) === 'demo-cover-en-v1' && <div className="preset"><span>{t("Demo")}</span>
              <button type="button" onClick={() => preset('pass')}>{t("Pass")}</button><button type="button" onClick={() => preset('warning')}>{t("Warning")}</button><button type="button" onClick={() => preset('fail')}>{t("Fail")}</button></div>}</div>
          {selectedStandard && <RuleFields rules={selectedStandard.rules} values={values} onChange={setValues} />}
          <div className="action-bar"><button className="primary big" type="submit"><Icon name="play" />{t("Evaluate")}</button></div>
        </fieldset></form></section>}

      {tab === 'inspections' && !creating && selected && <section className="panel">
        <div className="panel-top"><button className="ghost" onClick={() => setSelected(null)}><Icon name="back" />{t("Back")}</button>
          <div className="title-block"><h1>{selected.inputs.lot}</h1><small>{selected.standard.customer} · {selected.standard.part_number} · {t('Rev')} {selected.standard.revision} · {selected.inputs.inspector}</small></div>
          <div className={`verdict ${selected.overall_result.toLowerCase()}`}>{t(labels[selected.overall_result] ?? selected.overall_result)}</div></div>
        <Stepper status={selected.status} />
        {selected.status === 'CANCELLED' && <p className="warning-text center">{t("Cancelled")}</p>}

        <div className="results">{selected.item_results.map(result => <article className={`result-row ${result.auto_result.toLowerCase()} ${result.warning ? 'warn' : ''}`} key={result.rule_id}>
          <div className="result-main"><div><small>{result.area}</small><strong>{result.item}</strong></div>
            <div className="numbers"><span>{t(result.measured)}</span><small>{t("Criteria")} {t(result.criteria)}</small></div>
            <div className="inline">{result.warning && <span className="badge warning">{t("Warning")}</span>}<Badge value={result.final_result ?? result.auto_result} /></div></div>
          {(result.auto_result === 'FAIL' || result.auto_result === 'REVIEW' || result.warning) && <p className="explain">
            {result.auto_result === 'PASS' && result.warning ? t("Close to the limit. Choose the final decision.") : t(result.explanation)}<small>{t("Source")}: {result.source}</small>
</p>}
          {selected.pending?.kind === 'inspection_review' && result.warning && <div className="decide">
            <Segmented label={t("Final decision for warning")} disabled={busy} value={(decisions[result.rule_id]?.result ?? '') as 'PASS' | 'FAIL' | ''}
              options={[...(result.auto_result === 'PASS' ? [{ value: 'PASS' as const, label: t('PASS'), tone: 'good' }] : []), { value: 'FAIL' as const, label: t('FAIL'), tone: 'bad' }]}
              onChange={value => setDecisions({ ...decisions, [result.rule_id]: { result: value, reason: decisions[result.rule_id]?.reason ?? '' } })} />
            <input maxLength={1000} disabled={busy} value={decisions[result.rule_id]?.reason ?? ''} placeholder={t("Reason (required)")} aria-label={t("Review reason")}
              onChange={event => setDecisions({ ...decisions, [result.rule_id]: { result: decisions[result.rule_id]?.result ?? '', reason: event.target.value } })} /></div>}
        </article>)}</div>

        {selected.pending?.kind === 'correction' && <div className="correction"><h2 className="step-title"><span>!</span>{t("Fill in the missing values")}</h2><fieldset disabled={busy}><RuleFields rules={selected.standard.rules} values={values} onChange={setValues} /></fieldset></div>}

        {selected.report && ['AWAITING_REPORT_APPROVAL', 'APPROVED'].includes(selected.status) && <div className="report-box">
          <span className="chip">{selected.report.approved ? t("Approved report") : t("Draft report")}</span>
          {(() => {
            const available = reportLanguages(selected.report.files);
            const chosen = available.includes(docLanguage ?? language) ? docLanguage ?? language : 'en';
            const query = chosen === 'en' ? '' : `?lang=${chosen}`;
            return <>
              {available.length > 1 && <DocLanguagePicker value={chosen} onChange={setDocLanguage} available={available} />}
              <a className="button big" href={`/api/inspections/${selected.inspection_id}/reports/pdf${query}`} target="_blank" rel="noreferrer"><Icon name="download" />PDF</a>
              <a className="button big" href={`/api/inspections/${selected.inspection_id}/reports/xlsx${query}`}><Icon name="download" />Excel</a>
            </>;
          })()}
          {selected.report.approved && <small className="muted">{t("Approver:")} {selected.report.approval?.name ?? selected.approver}</small>}</div>}

        {selected.error && <div role="alert" className="error-banner">{t(selected.error)}<button disabled={busy} onClick={() => perform(async () => { show(await api<Inspection>(`/inspections/${selected.inspection_id}/retry`, {})); await refresh(); })}><Icon name="refresh" />{t("Retry")}</button></div>}

        {selected.pending && !selected.error && primary && <div className="action-bar">
          {closing ? <div className="closing"><input autoFocus maxLength={1000} disabled={busy} value={reason} placeholder={t(closing === 'reject' ? 'Why reject? (required)' : 'Why cancel? (required)')} aria-label={t("Reason")} onChange={event => setReason(event.target.value)} />
            <button disabled={busy} onClick={() => { setClosing(null); setReason(''); }}>{t("Back")}</button>
            <button className="danger" disabled={busy || !reason.trim()} onClick={() => action(closing)}>{t(closing === 'reject' ? 'Reject' : 'Cancel inspection')}</button></div>
            : <><div className="inline"><button className="ghost" disabled={busy} onClick={() => setClosing('cancel')}><Icon name="x" />{t("Cancel inspection")}</button>
              {selected.pending.kind !== 'correction' && <button disabled={busy} onClick={() => setClosing('reject')}><Icon name="undo" />{t("Reject")}</button>}</div>
              <button className="primary big" disabled={busy || !actor.trim()} onClick={() => action(primary.name)}><Icon name={primary.icon} />{t(primary.label)}</button></>}
        </div>}
        {selected.events.length > 0 && <details className="history"><summary><Icon name="history" size={16} />{t("History")} · {selected.events.length}</summary>{selected.events.map((event, index) => <p key={index}>{new Date(event.at).toLocaleString(locale)} · {event.actor} · {t(event.action)}{event.reason ? ` · ${event.reason}` : ''}</p>)}</details>}
      </section>}

      {tab === 'home' && <Home key={homeKey} userName={user.display_name} onGo={(page, params) => go(page, params)} onAsk={message => setAssistantMessage(message)} busy={busy} perform={perform} onLoaded={() => void refresh()} />}
      {tab === 'tasks' && <Tasks key={tasksView} initialView={tasksView} busy={busy} perform={perform} onOpen={openLink} />}
      {tab === 'forms' && <FormIntake initialReading={formReading} onReadingUsed={() => setFormReading(null)} busy={busy} perform={perform} onOpenStandard={lineage => { void refresh(); openStandard(lineage); }} />}
      {tab === 'trace' && <Traceback busy={busy} perform={perform} seed={traceSeed} onSeedUsed={() => setTraceSeed(null)} initialCase={traceCase} onCaseUsed={() => setTraceCase(null)} />}
      {tab === 'programs' && <Programs key={programsKey} initialView={programsView} initialRegion={programsRegion} busy={busy} perform={perform}
        initialProgram={programSelected} onProgramUsed={() => setProgramSelected(null)} onOpenTasks={() => go('tasks')} />}
      {tab === 'ai' && <AISettings busy={busy} perform={perform} onChanged={loadHealth} />}
      {tab === 'customers' && <CustomersView selected={customerName} busy={busy} perform={perform} onSelect={setCustomerName}
        onOpenRequest={openRequest} onNewRequest={newRequest} onOpenInspection={openInspection} onOpenStandard={lineage => openStandard(lineage)} />}
      {tab === 'requests' && <RequestsView selected={requestSelected} creatingFor={requestCreating} busy={busy} perform={perform}
        onSelect={setRequestSelected} onCreate={setRequestCreating} onOpenCustomer={openCustomer} onOpenInspection={openInspection} onOpenStandard={openStandard} />}
      {tab === 'standards' && family && <StandardWorkspace key={`${family}-${familyInstruction}`} lineage={family} initialInstruction={familyInstruction} busy={busy} perform={perform} onClose={() => { setFamily(null); setFamilyInstruction(''); }} onChanged={refresh} />}
      {tab === 'standards' && !family && <>
        <header className="page-header"><h1>{t("Standards library")}</h1>
          <label className="button"><Icon name="upload" />{t("Import JSON")}<input className="file-input" type="file" accept=".json" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) importStandard(file); event.target.value = ''; }} /></label></header>
        <div className="standard-grid">{families.map(({ head: standard, versions, active }) => <article className="standard-card" key={lineageOf(standard)}>
          <div className="card-head"><span className={`badge ${standard.status.toLowerCase()}`}>{standard.status === 'ACTIVE' ? t("Active") : standard.status === 'DRAFT' ? t("Draft") : t("Superseded")}</span><span className="chip">{t('Rev')} {standard.revision}</span></div>
          <h3>{standard.product_name}</h3>
          <p className="muted">{standard.customer} · {standard.part_number}</p>
          <p className="meta">{standard.rules.length} {t("items")} · {versions.length} {t("versions")}{standard.status === 'DRAFT' && active ? ` · ${t('Current revision')} ${active.revision}` : ''}{standard.provenance === 'SYNTHETIC' ? ` · ${t('Demo')}` : ''}</p>
          <div className="card-actions">
            <button className="primary" disabled={busy} onClick={() => setFamily(lineageOf(standard))}><Icon name="edit" />{t("Revise / versions")}</button>
            <a className="button icon-button" title={t("Standard PDF")} aria-label={t("Standard PDF")} href={`/api/standards/${standard.id}/document/pdf${language === 'en' ? '' : `?lang=${language}`}`} target="_blank" rel="noreferrer"><Icon name="file" /></a>
            {formFamilies.has(lineageOf(standard)) && <a className="button" title={t("Customer form")} href={`/api/standards/${standard.id}/customer-form/xlsx`}><Icon name="scan" />{t("Customer form")}</a>}
            {standard.source_file && <a className="button icon-button" title={t("Download source")} aria-label={t("Download source")} href={`/api/standards/${standard.id}/source`}><Icon name="download" /></a>}
          </div>
          {standard.status === 'DRAFT' && <div className="card-actions">
            <label className="button"><Icon name="clip" />{t("Attach source")}<input className="file-input" type="file" accept=".pdf,.xlsx" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) perform(async () => { const form = new FormData(); form.append('file', file); await api(`/standards/${standard.id}/source`, form); await refresh(); }); event.target.value = ''; }} /></label>
            <button className="primary" disabled={busy || !actor.trim()} onClick={() => perform(async () => { await api(`/standards/${standard.id}/activate`, {}); await refresh(); })}><Icon name="check" />{t("Activate")}</button></div>}
        </article>)}</div>
      </>}
    </main>
  </div>;
}
