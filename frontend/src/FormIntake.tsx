import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { useLanguage } from './language';
import { term } from './legacyDisplay';
import type { Language } from './translations';
import type { Condition, Rule, Standard } from './types';

type Confidence = 'high' | 'medium' | 'low';
interface Note { code: string; params: Record<string, unknown> }
interface ReadRule extends Rule { original: string; page: number | null; confidence: Confidence; notes: Note[] }
interface LayoutCell { rule_ids: string[]; variants: (string | null)[]; original: string; prefix: string }
interface Layout {
  kind: string; title: string; item_label: string; criteria_label: string;
  groups: { name: string; columns: string[] }[]; areas: string[]; rows: { item: string; cells: LayoutCell[] }[];
}
interface Sample {
  no: number; defect?: string; defect_en?: string; levels?: string[]; sets?: string; detect_process?: string; cause_process?: string;
  decision?: 'OK' | 'NG' | 'HOLD'; decision_reason?: string; cause?: string; countermeasures?: string[]; defect_rate?: string;
}
interface Warning { level: 'info' | 'warning'; code: string; params: Record<string, unknown> }
interface Draft { customer: string; part_number: string; product_name: string; document_number: string; revision: string; provenance: 'INTERNAL' | 'SYNTHETIC' }
interface Reading {
  id: string; document_type: 'INSPECTION_MATRIX' | 'LIMIT_APPROVAL' | 'AI_EXTRACTED' | 'UNKNOWN'; filename: string; pages: number;
  header: Record<string, string>; title: string; rules: ReadRule[]; conditions: Record<string, unknown>; layout: Layout | null;
  samples: Sample[]; warnings: Warning[]; interpreter: string; provenance: 'INTERNAL' | 'SYNTHETIC'; previous_id: string | null;
  ai?: { status: 'off' | 'not_needed' | 'blocked' | 'used' | 'failed'; model: string; count: number; error?: string };
  draft: Draft & { rules: Rule[] }; saved_standard_id?: string; saved_approval_id?: string;
}

const symbols = { lt: '<', lte: '≤', gt: '>', gte: '≥' };
const kinds: Rule['kind'][] = ['NUMERIC', 'PROHIBITED', 'NOT_APPLICABLE', 'UNRESOLVED'];
const kindLabels: Record<Rule['kind'], string> = {
  NUMERIC: 'Limit', PROHIBITED: 'Prohibited', NOT_APPLICABLE: 'Not applicable', UNRESOLVED: 'Needs interpretation',
};
const warningText: Record<string, string> = {
  UNKNOWN_FORMAT: 'This layout is not recognised yet. Connect an AI model in AI settings, or add its vocabulary to the form lexicon.',
  NEEDS_REVIEW: '{count} criteria need a check (yellow or red cells).',
  COLOR_MISMATCH: 'Colour mismatch: the table says {table} for {area}, but the drawing says {drawing}. Confirm with the customer.',
  MISSING_HEADER: 'Not found in the header: {field}',
  EXISTING_STANDARD: 'This document is already registered (revision {revision}). Saving creates its next version.',
  SAME_CRITERIA: 'The criteria are identical to {document_number} ({part_number}, revision {revision}).',
  LLM_BLOCKED: 'Customer documents are not sent to the external AI. The rule-based reader was used.',
  LLM_FAILED: 'AI reading failed: {error}',
  AI_EXTRACTED: '{count} criteria were extracted by AI. Check every item before saving.',
  LIMIT_HOLD: 'Samples on hold: No. {numbers}',
  LIMIT_UNDECIDED: 'Samples without a decision: No. {numbers}',
};
const aiText: Record<string, string> = {
  off: 'AI is off. The form was read by rules. Connect a model in AI settings.',
  not_needed: 'AI ({model}) was not needed: every cell was read by rules.',
  blocked: 'AI ({model}) was not used: this is a real customer document. Allow internal documents in AI settings, or upload it as a demo document.',
  used: 'AI ({model}) interpreted {count} cells. Check them before saving.',
  failed: 'AI ({model}) could not read the form: {error}',
};
const noteText: Record<string, string> = {
  RELATIVE_LIMIT: 'Also limited to {percent}% of the wall thickness',
  UNPARSED: 'Not interpreted: {text}',
  CONDITION_TEXT: '{text}',
  NO_PROTRUSION: 'Protrusion is not allowed',
  AI_INTERPRETED: 'Interpreted by AI ({model})',
};
const headerFields = [
  ['customer', 'Customer'], ['part_number', 'Part number'], ['product_name', 'Product name'], ['document_number', 'Document number'], ['revision', 'Revision'],
] as const;

function fill(template: string, params: Record<string, unknown>) {
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    const value = params[key];
    return Array.isArray(value) ? value.join(', ') : String(value ?? '');
  });
}

export function criteriaText(rule: Rule, t: (text: string) => string, language?: Language) {
  if (rule.kind === 'NUMERIC') return rule.conditions.map(c => `${language ? term(c.label, language) : c.label} ${symbols[c.operator]} ${c.limit} ${c.unit}`).join(', ');
  if (rule.kind === 'NOT_APPLICABLE') return '—';
  return t(kindLabels[rule.kind]);
}

export default function FormIntake({ busy, perform, onOpenStandard, initialReading, onReadingUsed }: {
  busy: boolean; perform: (task: () => Promise<void>) => Promise<void>; onOpenStandard: (lineage: string) => void;
  initialReading?: string | null; onReadingUsed?: () => void;
}) {
  const { t, language } = useLanguage();
  const [provenance, setProvenance] = useState<'INTERNAL' | 'SYNTHETIC'>('INTERNAL');
  const [samples, setSamples] = useState<{ id: number; language: string; label: Record<string, string> }[]>([]);
  const [dragging, setDragging] = useState(false);
  const [reading, setReading] = useState<Reading | null>(null);
  const [rules, setRules] = useState<ReadRule[]>([]);
  const [header, setHeader] = useState<Draft | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [onlyReview, setOnlyReview] = useState(false);
  const [asVersion, setAsVersion] = useState(true);
  const [saved, setSaved] = useState<Standard | null>(null);
  const [customer, setCustomer] = useState('');
  const [customers, setCustomers] = useState<string[]>([]);
  const [approvalSaved, setApprovalSaved] = useState(false);

  useEffect(() => { api<typeof samples>('/forms/samples').then(setSamples).catch(() => setSamples([])); }, []);
  useEffect(() => { api<{ name: string }[]>('/customers').then(list => setCustomers(list.map(item => item.name))).catch(() => setCustomers([])); }, []);

  function show(next: Reading) {
    setReading(next);
    setRules(next.rules);
    setHeader({ ...next.draft });
    setSelected(null);
    setSaved(null);
    setApprovalSaved(false);
    setAsVersion(Boolean(next.previous_id));
    setCustomer('');
  }

  useEffect(() => {
    if (!initialReading) return;
    void perform(async () => show(await api<Reading>(`/forms/${initialReading}`)));
    onReadingUsed?.();
  }, [initialReading]);

  async function readSample(id: number) {
    await perform(async () => { show(await api<Reading>(`/forms/samples/${id}`, {})); });
  }

  async function read(file: File) {
    await perform(async () => {
      const form = new FormData();
      form.append('file', file);
      form.append('provenance', provenance);
      show(await api<Reading>('/forms', form));
    });
  }

  const byId = useMemo(() => Object.fromEntries(rules.map(rule => [rule.id, rule])), [rules]);
  const reviewCount = rules.filter(rule => rule.confidence !== 'high').length;
  const current = selected ? byId[selected] : null;

  function update(id: string, change: Partial<ReadRule>) {
    setRules(list => list.map(rule => rule.id === id ? { ...rule, ...change } : rule));
  }

  function setCondition(rule: ReadRule, index: number, change: Partial<Condition>) {
    update(rule.id, { conditions: rule.conditions.map((condition, i) => i === index ? { ...condition, ...change } : condition) });
  }

  async function saveStandard() {
    if (!reading || !header) return;
    await perform(async () => {
      const standard = {
        ...header,
        rules: rules.map(({ id, area, item, kind, conditions, source }) => ({
          id, area, item, kind, source,
          conditions: kind === 'NUMERIC' ? conditions.map(({ field, label, operator, limit, unit }) => ({ field, label, operator, limit, unit })) : [],
        })),
      };
      setSaved(await api<Standard>(`/forms/${reading.id}/standard`, { standard, previous_id: asVersion ? reading.previous_id : null }));
    });
  }

  async function saveApproval() {
    if (!reading) return;
    await perform(async () => {
      await api(`/forms/${reading.id}/limit-approval`, { customer });
      setApprovalSaved(true);
    });
  }

  if (!reading) return <>
    <header className="page-header"><h1>{t("Read specs")}</h1></header>
    <section className="intake-layout">
      <div className="intake-main">
        <label className={`dropzone big intake-drop ${busy ? 'disabled' : ''} ${dragging ? 'dragging' : ''}`}
          onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
          onDrop={event => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files?.[0]; if (file && !busy) void read(file); }}>
          <span className="intake-drop-icon"><Icon name="upload" size={34} /></span>
          <strong>{t("Choose a customer form (PDF)")}</strong>
          <small>{t("or drop the file here")}</small>
          <span className="flow-chips" aria-label={t("Korean, English or Chinese")}><span>KO</span><span>EN</span><span>{'\u4E2D'}</span></span>
          <input className="file-input" type="file" accept=".pdf,application/pdf" disabled={busy}
            onChange={event => { const file = event.target.files?.[0]; if (file) void read(file); event.target.value = ''; }} /></label>
        <div className="intake-options">
          <div className="segmented" role="group" aria-label={t("What kind of document is it?")}>
            <button type="button" aria-pressed={provenance === 'INTERNAL'} className={provenance === 'INTERNAL' ? 'selected' : ''} onClick={() => setProvenance('INTERNAL')}><Icon name="lock" size={15} />{t("Real customer document")}</button>
            <button type="button" aria-pressed={provenance === 'SYNTHETIC'} className={provenance === 'SYNTHETIC' ? 'selected' : ''} onClick={() => setProvenance('SYNTHETIC')}><Icon name="file" size={15} />{t("Demo or sample document")}</button>
          </div>
          <span className="muted lock-note" title={provenance === 'INTERNAL' ? t("Real customer documents are never sent to an external AI.") : t("Sample documents may be sent to the AI selected in AI settings.")}>
            <Icon name={provenance === 'INTERNAL' ? 'lock' : 'spark'} size={14} />{provenance === 'INTERNAL' ? t("No external AI") : t("AI may assist")}</span>
        </div>
      </div>
      <aside className="intake-side">
        <section className="panel intake-card">
          <h2>{t("How it works")}</h2>
          <ol className="intake-steps">
            <li><span>1</span><div><strong>{t("Upload the PDF")}</strong><small>{t("Shipyard, coating or owner forms in Korean, English or Chinese")}</small></div></li>
            <li><span>2</span><div><strong>{t("Check what was read")}</strong><small>{t("Limits and areas become rules; unclear cells are flagged")}</small></div></li>
            <li><span>3</span><div><strong>{t("Save as a standard")}</strong><small>{t("A new revision is compared with the previous one")}</small></div></li>
          </ol>
        </section>
        {samples.length > 0 && <section className="panel intake-card">
          <h2>{t("Try a sample form")}</h2>
          <div className="sample-list">{samples.map(sample => <button key={sample.id} type="button" className="sample-item" data-sample-en={sample.label.en} disabled={busy} onClick={() => void readSample(sample.id)}>
            <span className="sample-lang">{sample.language}</span><span className="sample-label">{sample.label[language] || sample.label.en}</span><Icon name="next" size={15} /></button>)}</div>
        </section>}
      </aside>
    </section>
  </>;

  const typeLabel = { INSPECTION_MATRIX: 'Inspection criteria table', LIMIT_APPROVAL: 'Limit-sample approval', AI_EXTRACTED: 'Read by AI', UNKNOWN: 'Unknown layout' }[reading.document_type];
  const layout = reading.layout?.kind === 'INSPECTION_MATRIX' ? reading.layout : null;

  return <>
    <header className="page-header">
      <div className="panel-top"><button className="ghost" onClick={() => setReading(null)}><Icon name="back" />{t("Read another form")}</button>
        <div className="title-block"><h1>{reading.title || reading.filename}</h1>
          <small>{reading.filename} · {reading.pages} {t("pages")} · <span className="chip">{t(typeLabel)}</span></small>
          {reading.ai && <p className={`ai-status ${reading.ai.status}`}><Icon name="spark" size={15} />{fill(t(aiText[reading.ai.status]), { ...reading.ai })}</p>}</div></div>
      <a className="button" href={`/api/forms/${reading.id}/file`} target="_blank" rel="noreferrer"><Icon name="file" />{t("Original")}</a>
    </header>

    {reading.warnings.length > 0 && <section className="intake-warnings">{reading.warnings.map((warning, index) =>
      <p key={index} className={warning.level === 'warning' ? 'warn' : 'info'}><Icon name={warning.level === 'warning' ? 'alert' : 'info'} size={16} />
        {fill(t(warningText[warning.code] ?? warning.code), { ...warning.params, field: warning.params.field ? t(String(warning.params.field)) : '' })}</p>)}</section>}

    {reading.document_type === 'LIMIT_APPROVAL' && <section className="panel">
      <div className="limit-summary">{(['OK', 'NG', 'HOLD'] as const).map(decision => <span key={decision} className={`stat ${decision.toLowerCase()}`}>
        <strong>{reading.samples.filter(sample => sample.decision === decision).length}</strong>{t(decision === 'HOLD' ? 'On hold' : decision)}</span>)}
        <span className="stat"><strong>{reading.samples.length}</strong>{t("Samples")}</span></div>
      <div className="table-scroll"><table className="limit-table"><thead><tr>
        <th>No.</th><th>{t("Defect")}</th><th>{t("Decision")}</th><th>{t("Reason")}</th><th>{t("Detected / caused at")}</th><th>{t("Cause")}</th><th>{t("Defect rate")}</th></tr></thead>
        <tbody>{reading.samples.map(sample => <tr key={sample.no}>
          <td>{sample.no}</td><td><strong>{sample.defect}</strong><small>{sample.defect_en}</small></td>
          <td>{sample.decision && <span className={`badge ${sample.decision === 'OK' ? 'pass' : sample.decision === 'NG' ? 'fail' : 'review'}`}>{t(sample.decision === 'HOLD' ? 'On hold' : sample.decision)}</span>}</td>
          <td className="pre">{sample.decision_reason}</td><td>{sample.detect_process} / {sample.cause_process}</td>
          <td className="pre">{sample.cause}{sample.countermeasures?.length ? <small>{sample.countermeasures.join(' · ')}</small> : null}</td><td>{sample.defect_rate}</td></tr>)}</tbody></table></div>
      <div className="action-bar">
        {approvalSaved || reading.saved_approval_id ? <div className="success-banner"><Icon name="check" />{t("Saved to the customer's history")}</div> : <>
          <label className="inline-field">{t("Customer")}<input list="intake-customers" maxLength={100} value={customer} onChange={event => setCustomer(event.target.value)} /></label>
          <datalist id="intake-customers">{customers.map(name => <option key={name} value={name} />)}</datalist>
          <button className="primary big" disabled={busy || !customer.trim()} onClick={() => void saveApproval()}><Icon name="check" />{t("Save limit samples")}</button></>}
      </div>
    </section>}

    {reading.document_type !== 'LIMIT_APPROVAL' && header && <>
      <section className="panel">
        <h2 className="step-title"><span>1</span>{t("Document information")}</h2>
        <div className="form-grid five">{headerFields.map(([key, label]) => <label key={key}>{t(label)}
          <input required maxLength={100} value={header[key]} onChange={event => setHeader({ ...header, [key]: event.target.value })} /></label>)}</div>
        {Object.keys(reading.conditions).length > 0 && <div className="quick conditions">
          {reading.conditions.distance_cm ? <span className="chip">{t("Viewing distance")} {(reading.conditions.distance_cm as string[]).join('–')} cm</span> : null}
          {reading.conditions.illuminance_lux ? <span className="chip">≥ {String(reading.conditions.illuminance_lux)} Lux</span> : null}
          {reading.conditions.customer_standard ? <span className="chip">{String(reading.conditions.customer_standard)}{reading.conditions.class ? ` Class ${String(reading.conditions.class)}` : ''}</span> : null}
          {reading.conditions.method ? <span className="chip">{t("Method")}: {String(reading.conditions.method)}</span> : null}
          {reading.conditions.frequency ? <span className="chip">{t("Frequency")}: {String(reading.conditions.frequency)}</span> : null}
        </div>}
      </section>

      <section className="panel">
        <div className="step-row"><h2 className="step-title"><span>2</span>{t("Check the converted criteria")}</h2>
          <div className="segmented small">
            <button type="button" className={!onlyReview ? 'selected' : ''} onClick={() => setOnlyReview(false)}>{t("All")} {rules.length}</button>
            <button type="button" className={onlyReview ? 'selected' : ''} onClick={() => setOnlyReview(true)}>{t("Needs a check")} {reviewCount}</button></div></div>
        <div className="legend"><span className="conf high" />{t("Read with confidence")}<span className="conf medium" />{t("Check the note")}<span className="conf low" />{t("Interpretation uncertain")}</div>
        {layout ? <div className="table-scroll"><table className="form-matrix">
          <thead><tr><th rowSpan={2}>{layout.item_label}</th>{layout.groups.map(group => <th key={group.name} colSpan={group.columns.length}>{group.name}</th>)}</tr>
            <tr>{layout.areas.map(area => <th key={area}>{area}</th>)}</tr></thead>
          <tbody>{layout.rows.map(row => {
            const cellRules = row.cells.map(cell => cell.rule_ids.map(id => byId[id]).filter(Boolean));
            if (onlyReview && !cellRules.flat().some(rule => rule.confidence !== 'high')) return null;
            return <tr key={row.item}><th>{row.item}</th>{cellRules.map((list, index) => <td key={index}>{list.map(rule =>
              <button type="button" key={rule.id} className={`rule-chip ${rule.confidence} ${rule.kind.toLowerCase()} ${selected === rule.id ? 'selected' : ''}`} onClick={() => setSelected(rule.id)}>
                {rule.item !== row.item && <small>{rule.item.replace(row.item, '').trim()}</small>}{criteriaText(rule, t, language)}</button>)}</td>)}</tr>;
          })}</tbody></table></div>
          : <div className="rule-list">{rules.filter(rule => !onlyReview || rule.confidence !== 'high').map(rule => <button type="button" key={rule.id}
            className={`rule-chip ${rule.confidence} ${rule.kind.toLowerCase()} ${selected === rule.id ? 'selected' : ''}`} onClick={() => setSelected(rule.id)}>
            <small>{rule.area} · {rule.item}</small>{criteriaText(rule, t, language)}</button>)}</div>}

        {current && <div className="rule-editor">
          <div className="rule-editor-head"><div><small>{current.area}</small><h3>{current.item}</h3></div>
            <button className="icon-button" aria-label={t("Close")} onClick={() => setSelected(null)}><Icon name="x" /></button></div>
          <blockquote><small>{t("Original text")}{current.page ? ` · p.${current.page}` : ''}</small>{current.original || '—'}</blockquote>
          {current.notes.length > 0 && <ul className="notes">{current.notes.map((note, index) => <li key={index}>{fill(t(noteText[note.code] ?? note.code), note.params)}</li>)}</ul>}
          <div className="segmented">{kinds.map(kind => <button type="button" key={kind} className={current.kind === kind ? 'selected' : ''}
            onClick={() => update(current.id, { kind, conditions: kind === 'NUMERIC' ? (current.conditions.length ? current.conditions : [{ field: 'value', label: t('Value'), operator: 'lt', limit: '0', unit: 'mm', warning_margin: null }]) : current.conditions })}>{t(kindLabels[kind])}</button>)}</div>
          {current.kind === 'NUMERIC' && <div className="condition-rows">{current.conditions.map((condition, index) => <div className="condition-row" key={index}>
            <input aria-label={t("Dimension")} maxLength={60} value={condition.label} onChange={event => setCondition(current, index, { label: event.target.value })} />
            <select aria-label={t("Operator")} value={condition.operator} onChange={event => setCondition(current, index, { operator: event.target.value as Condition['operator'] })}>
              {(Object.keys(symbols) as Condition['operator'][]).map(op => <option key={op} value={op}>{symbols[op]}</option>)}</select>
            <input aria-label={t("Limit")} inputMode="decimal" value={condition.limit} onChange={event => setCondition(current, index, { limit: event.target.value })} />
            <select aria-label={t("Unit")} value={condition.unit} onChange={event => setCondition(current, index, { unit: event.target.value as Condition['unit'] })}>
              <option value="mm">mm</option><option value="µm">µm</option></select>
            <button type="button" className="icon-button" aria-label={t("Remove")} disabled={current.conditions.length < 2}
              onClick={() => update(current.id, { conditions: current.conditions.filter((_, i) => i !== index) })}><Icon name="x" /></button></div>)}
            <button type="button" className="ghost" onClick={() => update(current.id, { conditions: [...current.conditions, { field: `value_${current.conditions.length + 1}`, label: t('Value'), operator: 'lt', limit: '0', unit: 'mm', warning_margin: null }] })}>
              <Icon name="plus" />{t("Add condition")}</button></div>}
          {current.confidence !== 'high' && <button className="primary" onClick={() => update(current.id, { confidence: 'high' })}><Icon name="check" />{t("Mark as checked")}</button>}
        </div>}
      </section>

      <section className="panel">
        <h2 className="step-title"><span>3</span>{t("Save as a standard")}</h2>
        {saved || reading.saved_standard_id ? <div className="saved-actions">
          <div className="success-banner"><Icon name="check" />{t("Saved as a draft standard. Activate it in the standards library.")}</div>
          {saved && <><button className="primary" onClick={() => onOpenStandard(saved.family_id ?? saved.id)}><Icon name="book" />{t("Open in standards library")}</button>
            <a className="button" href={`/api/standards/${saved.id}/customer-form/xlsx`}><Icon name="download" />{t("Customer form")} Excel</a>
            <a className="button" href={`/api/standards/${saved.id}/customer-form/pdf`} target="_blank" rel="noreferrer"><Icon name="download" />{t("Customer form")} PDF</a></>}
        </div> : <>
          {reading.previous_id && <div className="segmented">
            <button type="button" className={asVersion ? 'selected' : ''} onClick={() => setAsVersion(true)}>{t("Next version of the registered standard")}</button>
            <button type="button" className={!asVersion ? 'selected' : ''} onClick={() => setAsVersion(false)}>{t("New standard")}</button></div>}
          {reviewCount > 0 && <p className="warning-text">{fill(t("{count} criteria have not been checked yet."), { count: reviewCount })}</p>}
          <div className="action-bar"><span className="muted">{t("It is saved as a draft and does not apply to inspections until activated.")}</span>
            <button className="primary big" data-tour="save-draft" disabled={busy || !rules.length || headerFields.some(([key]) => !header[key].trim())} onClick={() => void saveStandard()}>
              <Icon name="check" />{t("Save as draft standard")}</button></div></>}
      </section>
    </>}
  </>;
}
