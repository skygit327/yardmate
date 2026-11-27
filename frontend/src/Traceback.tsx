import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { useLanguage } from './language';
import type { Language } from './translations';

type Names = Record<Language, string>;
interface LibraryStep { names: Names; aliases: string[]; defects: Record<string, number>; checks: Names[] }
interface Library { steps: Record<string, LibraryStep>; defects: Record<string, { names: Names }>; templates: { id: string; names: Names; steps: FlowStep[] }[] }
interface FlowStep { key: string; label?: string | null }
interface Flow { id: string | null; name: string; template?: string | null; customer: string; part_numbers: string[]; steps: FlowStep[] }
interface Reason { code: string; params: Record<string, unknown> }
interface Candidate { step: string; rank: number; score: number; likelihood: 'high' | 'medium' | 'low'; label?: string | null; reasons: Reason[]; checks: Names[]; countermeasures: string[] }
interface Level { level: string; n: number; ng: number; mean: number }
interface Factor { name: string; step: string | null; levels: Level[]; eta2: number; verdict: 'common' | 'specific' | 'mixed' | 'insufficient'; worst: string }
interface Piece { value: number; ok: boolean; factors: Record<string, string> }
interface Case {
  id: string; kind: 'NCR' | 'DEFECT'; customer: string; part_numbers: string[]; created_at: string; saved_at?: string;
  input?: { defect: string; part_number: string | null };
  ncr?: { filename: string; header: Record<string, string>; spec: { characteristic: string; lower: number; upper: number; nominal: number; text: string } | null; actions: string[] };
  summary?: { n: number; ng: number; mean: number; min: number; max: number; std: number; center: number; shift: number; below: number; above: number };
  factors?: Factor[]; pieces?: Piece[];
  analysis: { flow: Flow; defect: string | null; detected_at: string | null; candidates: Candidate[]; findings: Reason[] };
}

const reasonText: Record<string, string> = {
  CAN_CAUSE: 'This process can create this defect type (strength {strength}/3).',
  FACTOR_SPECIFIC: 'Failures cluster on {factor} {level} ({ng}/{n} failed).',
  FACTOR_NOT_SPECIFIC: 'Every {factor} level fails alike ({levels} levels): not one specific equipment.',
  COMMON_SHIFT: '{ng}/{n} pieces fail with the same offset ({shift} mm): a shared setting of this process is suspected.',
  MEASUREMENT_CHECK: 'With a uniform offset, also rule out measurement differences.',
  HISTORY: 'Earlier limit samples traced the same defect to this process ({count}: No. {numbers}).',
};
const findingText: Record<string, string> = {
  COMMON_CAUSE: '{ng} of {n} pieces failed and every factor level fails alike. Look for a cause shared by all pieces rather than one mold, machine or die.',
  SYSTEMATIC_OFFSET: 'The values are consistently {direction} the tolerance centre by {shift} mm with little spread ({std} mm): a systematic offset, not random variation.',
  SPECIFIC_FACTOR: 'Failures concentrate on {factor} {level}. Check that equipment first.',
  UNKNOWN_DEFECT: 'The defect type was not recognised. Suggestions use process order only.',
};
const likelihoodLabel = { high: 'Likely', medium: 'Possible', low: 'Less likely' };
const verdictLabel = { common: 'Same for all', specific: 'Clustered', mixed: 'Mixed', insufficient: 'Too few' };

function fill(template: string, params: Record<string, unknown>) {
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    const value = params[key];
    return Array.isArray(value) ? value.join(', ') : String(value ?? '');
  });
}

function Plot({ pieces, spec }: { pieces: Piece[]; spec: { lower: number; upper: number } }) {
  const { t } = useLanguage();
  const values = pieces.map(piece => piece.value);
  const low = Math.min(spec.lower, ...values), high = Math.max(spec.upper, ...values);
  const pad = (high - low) * 0.15 || 0.01;
  const y = (value: number) => 150 - ((value - (low - pad)) / (high - low + 2 * pad)) * 130;
  const x = (index: number) => 50 + index * (520 / Math.max(1, pieces.length - 1));
  return <svg className="ncr-plot" viewBox="0 0 600 170" role="img" aria-label={t("Measured values against the tolerance")}>
    <rect x="40" y={y(spec.upper)} width="540" height={y(spec.lower) - y(spec.upper)} className="band" />
    <line x1="40" x2="580" y1={y(spec.upper)} y2={y(spec.upper)} className="limit" />
    <line x1="40" x2="580" y1={y(spec.lower)} y2={y(spec.lower)} className="limit" />
    <text x="36" y={y(spec.upper) + 4} textAnchor="end">{spec.upper}</text>
    <text x="36" y={y(spec.lower) + 4} textAnchor="end">{spec.lower}</text>
    {pieces.map((piece, index) => <circle key={index} cx={x(index)} cy={y(piece.value)} r="5" className={piece.ok ? 'ok' : 'ng'}>
      <title>{`${piece.factors.Product ?? ''} ${piece.value}`}</title></circle>)}
  </svg>;
}

export interface TraceSeed { defect: string; part: string; customer: string }

export default function Traceback({ busy, perform, seed, onSeedUsed, initialCase, onCaseUsed }: {
  busy: boolean; perform: (task: () => Promise<void>) => Promise<void>; seed: TraceSeed | null; onSeedUsed: () => void;
  initialCase?: string | null; onCaseUsed?: () => void;
}) {
  const { t, language, locale } = useLanguage();
  const [library, setLibrary] = useState<Library | null>(null);
  const [flows, setFlows] = useState<Flow[]>([]);
  const [flowId, setFlowId] = useState('');
  const [defect, setDefect] = useState('');
  const [part, setPart] = useState('');
  const [customer, setCustomer] = useState('');
  const [detectedAt, setDetectedAt] = useState('');
  const [result, setResult] = useState<Case | null>(null);
  const [editing, setEditing] = useState<Flow | null>(null);
  const [saveCustomer, setSaveCustomer] = useState('');
  const [saved, setSaved] = useState(false);
  const [done, setDone] = useState<Record<string, boolean>>({});

  async function load() {
    const [nextLibrary, nextFlows] = await Promise.all([api<Library>('/process-library'), api<Flow[]>('/process-flows')]);
    setLibrary(nextLibrary);
    setFlows(nextFlows);
  }
  useEffect(() => { load().catch(() => undefined); }, []);
  useEffect(() => {
    if (!seed) return;
    setDefect(seed.defect); setPart(seed.part); setCustomer(seed.customer); setResult(null); setEditing(null);
    onSeedUsed();
  }, [seed]);

  useEffect(() => {
    if (!initialCase) return;
    void perform(async () => show(await api<Case>(`/traceback/${initialCase}`)));
    onCaseUsed?.();
  }, [initialCase]);

  const stepName = (step: FlowStep | string) => {
    const key = typeof step === 'string' ? step : step.key;
    const label = typeof step === 'string' ? null : step.label;
    return label || library?.steps[key]?.names[language] || key;
  };
  const currentFlow: Flow | null = useMemo(() => flows.find(flow => flow.id === flowId)
    ?? (library ? { id: null, name: library.templates[0].names[language], customer: '', part_numbers: [], steps: library.templates[0].steps } : null), [flows, flowId, library, language]);

  function show(next: Case) {
    setResult(next); setSaved(Boolean(next.saved_at)); setSaveCustomer(next.customer || customer); setDone({});
  }

  async function analyseDefect() {
    await perform(async () => show(await api<Case>('/traceback', {
      defect, part_number: part || null, detected_at: detectedAt || null, flow_id: flowId || null, customer: customer || null,
    })));
  }

  async function analyseNcr(file: File) {
    await perform(async () => {
      const form = new FormData();
      form.append('file', file);
      if (flowId) form.append('flow_id', flowId);
      show(await api<Case>('/traceback/ncr', form));
    });
  }

  async function saveFlow(flow: Flow) {
    await perform(async () => {
      const body = { name: flow.name, template: flow.template ?? null, customer: flow.customer, part_numbers: flow.part_numbers, steps: flow.steps.map(({ key, label }) => ({ key, label: label || null })) };
      const savedFlow = flow.id ? await api<Flow>(`/process-flows/${flow.id}`, body, 'PUT') : await api<Flow>('/process-flows', body);
      await load();
      setFlowId(savedFlow.id ?? '');
      setEditing(null);
    });
  }

  async function saveCase() {
    if (!result) return;
    await perform(async () => { await api(`/traceback/${result.id}/save`, { customer: saveCustomer }); setSaved(true); });
  }

  if (!library) return null;

  if (editing) {
    const move = (index: number, delta: number) => {
      const steps = [...editing.steps];
      const [item] = steps.splice(index, 1);
      steps.splice(index + delta, 0, item);
      setEditing({ ...editing, steps });
    };
    const unused = Object.keys(library.steps).filter(key => !editing.steps.some(step => step.key === key));
    return <>
      <header className="page-header"><div className="panel-top"><button className="ghost" onClick={() => setEditing(null)}><Icon name="back" />{t("Back")}</button>
        <h1>{t("Process flow")}</h1></div></header>
      <section className="panel flow-editor">
        <div className="form-grid two"><label>{t("Flow name")}<input required maxLength={100} value={editing.name} onChange={event => setEditing({ ...editing, name: event.target.value })} /></label>
          <label>{t("Part numbers (comma separated)")}<input maxLength={500} value={editing.part_numbers.join(', ')} onChange={event => setEditing({ ...editing, part_numbers: event.target.value.split(',').map(item => item.trim()) })} /></label></div>
        <ol className="flow-steps">{editing.steps.map((step, index) => <li key={step.key}>
          <span className="step-no">{index + 1}</span>
          <div><strong>{library.steps[step.key]?.names[language] ?? step.key}</strong>
            <input placeholder={t("Name used on site (optional)")} maxLength={60} value={step.label ?? ''} onChange={event => setEditing({ ...editing, steps: editing.steps.map((item, i) => i === index ? { ...item, label: event.target.value } : item) })} /></div>
          <div className="inline">
            <button className="icon-button" aria-label={t("Move up")} disabled={index === 0} onClick={() => move(index, -1)}><Icon name="back" /></button>
            <button className="icon-button" aria-label={t("Move down")} disabled={index === editing.steps.length - 1} onClick={() => move(index, 1)}><Icon name="next" /></button>
            <button className="icon-button" aria-label={t("Remove")} disabled={editing.steps.length < 2} onClick={() => setEditing({ ...editing, steps: editing.steps.filter((_, i) => i !== index) })}><Icon name="x" /></button></div>
        </li>)}</ol>
        {unused.length > 0 && <div className="quick"><span className="muted">{t("Add a process")}:</span>{unused.map(key => <button key={key} type="button" onClick={() => setEditing({ ...editing, steps: [...editing.steps, { key }] })}><Icon name="plus" size={14} />{library.steps[key].names[language]}</button>)}</div>}
        <div className="action-bar">
          {editing.id ? <button className="ghost danger-text" onClick={() => void perform(async () => { await api(`/process-flows/${editing.id}`, undefined, 'DELETE'); await load(); setFlowId(''); setEditing(null); })}><Icon name="trash" />{t("Delete")}</button> : <span />}
          <button className="primary big" disabled={busy || !editing.name.trim()} onClick={() => void saveFlow(editing)}><Icon name="check" />{t("Save process flow")}</button></div>
      </section>
    </>;
  }

  const flowPicker = <div className="flow-picker">
    <label>{t("Process flow")}<select value={flowId} onChange={event => setFlowId(event.target.value)}>
      <option value="">{t("Default")}: {library.templates[0].names[language]}</option>
      {flows.map(flow => <option key={flow.id ?? ''} value={flow.id ?? ''}>{flow.name}{flow.part_numbers.length ? ` (${flow.part_numbers.join(', ')})` : ''}</option>)}</select></label>
    <button className="ghost" onClick={() => setEditing(currentFlow && currentFlow.id ? { ...currentFlow } : { id: null, name: t('New process flow'), template: library.templates[0].id, customer: '', part_numbers: [], steps: [...library.templates[0].steps] })}><Icon name="edit" />{currentFlow?.id ? t("Edit flow") : t("Create a flow for a part")}</button>
    {!flowId && <div className="quick">{library.templates.slice(1).map(template => <button key={template.id} type="button"
      onClick={() => setEditing({ id: null, name: template.names[language], template: template.id, customer: '', part_numbers: [], steps: [...template.steps] })}><Icon name="plus" size={14} />{template.names[language]}</button>)}</div>}
  </div>;

  if (!result) return <>
    <header className="page-header"><div><h1>{t("Root-cause tracing")}</h1>
      <p className="muted">{t("Suggests which process most likely caused a defect, following the process order. Results are candidates to verify, not conclusions.")}</p></div></header>
    <section className="panel">{flowPicker}
      {currentFlow && <ol className="flow-bar compact">{currentFlow.steps.map((step, index) => <li key={step.key}><span className="step-no">{index + 1}</span>{stepName(step)}</li>)}</ol>}
    </section>
    <div className="trace-start">
      <section className="panel">
        <h2 className="step-title"><Icon name="file" />{t("Dimension failure report (NCR)")}</h2>
        <p className="muted">{t("Reads the measurement table and compares every mold, machine, die, lot and position.")}</p>
        <label className={`dropzone ${busy ? 'disabled' : ''}`}><Icon name="upload" size={26} /><strong>{t("Upload an NCR (PDF)")}</strong>
          <input className="file-input" type="file" accept=".pdf,application/pdf" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) void analyseNcr(file); event.target.value = ''; }} /></label>
      </section>
      <section className="panel">
        <h2 className="step-title"><Icon name="alert" />{t("Find by defect type")}</h2>
        <div className="form-grid">
          <label>{t("Defect")}<input list="trace-defects" required maxLength={300} value={defect} placeholder={library.defects.crack.names[language]} onChange={event => setDefect(event.target.value)} /></label>
          <datalist id="trace-defects">{Object.values(library.defects).map(entry => <option key={entry.names.en} value={entry.names[language]} />)}</datalist>
          <div className="quick">{['crack', 'porosity', 'burr', 'scratch', 'stain', 'dimension'].map(key => <button key={key} type="button" className={defect === library.defects[key].names[language] ? 'selected' : ''}
            onClick={() => setDefect(library.defects[key].names[language])}>{library.defects[key].names[language]}</button>)}</div>
          <div className="form-grid two"><label>{t("Part number")}<input maxLength={100} value={part} onChange={event => setPart(event.target.value)} /></label>
            <label>{t("Found at")}<select value={detectedAt} onChange={event => setDetectedAt(event.target.value)}>
              <option value="">{t("Inspection")}</option>
              {currentFlow?.steps.map(step => <option key={step.key} value={step.key}>{stepName(step)}</option>)}</select></label></div>
        </div>
        <div className="action-bar"><span /><button className="primary big" disabled={busy || !defect.trim()} onClick={() => void analyseDefect()}><Icon name="spark" />{t("Find likely processes")}</button></div>
      </section>
    </div>
  </>;

  const analysis = result.analysis;
  const byStep = Object.fromEntries(analysis.candidates.map(candidate => [candidate.step, candidate]));
  const detectedIndex = analysis.detected_at ? analysis.flow.steps.findIndex(step => step.key === analysis.detected_at) : -1;
  const title = result.kind === 'NCR' ? `${result.ncr?.header.ncr_no ?? 'NCR'} · ${result.ncr?.header.problem ?? ''}` : (result.input?.defect ?? '');
  const flowName = analysis.flow.id ? analysis.flow.name : (library.templates.find(item => item.id === analysis.flow.template)?.names[language] ?? analysis.flow.name);
  const defectName = analysis.defect ? library.defects[analysis.defect]?.names[language] : t('Unknown defect type');

  return <>
    <header className="page-header"><div className="panel-top"><button className="ghost" onClick={() => setResult(null)}><Icon name="back" />{t("New analysis")}</button>
      <div className="title-block"><h1>{title}</h1><small>{defectName} · {flowName} · {new Date(result.created_at).toLocaleString(locale)}</small></div></div></header>

    {analysis.findings.length > 0 && <section className="intake-warnings">{analysis.findings.map((finding, index) =>
      <p key={index} className={finding.code === 'UNKNOWN_DEFECT' ? 'warn' : 'info'}><Icon name="info" size={16} />
        {fill(t(findingText[finding.code] ?? finding.code), { ...finding.params, direction: finding.params.direction ? t(String(finding.params.direction)) : '' })}</p>)}</section>}

    <section className="panel">
      <h2 className="step-title">{t("Where in the process?")}</h2>
      <ol className="flow-bar">{analysis.flow.steps.map((step, index) => {
        const candidate = byStep[step.key];
        return <li key={step.key} className={`${candidate ? candidate.likelihood : 'none'} ${index > detectedIndex && detectedIndex >= 0 ? 'after' : ''}`}>
          <a href={candidate ? `#cand-${step.key}` : undefined}>
            <span className="step-no">{index + 1}</span>
            <strong>{stepName(step)}</strong>
            {candidate && <small>{fill(t("Rank {rank}"), { rank: candidate.rank })} · {t(likelihoodLabel[candidate.likelihood])}</small>}
          </a>
          {index === detectedIndex && <span className="detected-pin">{t("Found here")}</span>}
        </li>;
      })}{detectedIndex < 0 && <li className="customer"><span className="detected-pin">{t("Found by the customer")}</span></li>}</ol>
      <div className="legend"><span className="lk high" />{t("Likely")}<span className="lk medium" />{t("Possible")}<span className="lk low" />{t("Less likely")}<span className="lk none" />{t("Not linked to this defect")}</div>
    </section>

    {result.kind === 'NCR' && result.summary && result.ncr?.spec && <section className="panel">
      <div className="step-row"><h2 className="step-title">{t("Measurements")}</h2><span className="chip">{result.ncr.spec.text}</span></div>
      <div className="limit-summary">
        <span className="stat ng"><strong>{result.summary.ng}/{result.summary.n}</strong>{t("Out of tolerance")}</span>
        <span className="stat"><strong>{result.summary.mean}</strong>{t("Average")}</span>
        <span className="stat"><strong>{result.summary.shift > 0 ? '+' : ''}{result.summary.shift}</strong>{t("Offset from centre (mm)")}</span>
        <span className="stat"><strong>{result.summary.std}</strong>{t("Spread (std, mm)")}</span></div>
      {result.pieces && <Plot pieces={result.pieces} spec={result.ncr.spec} />}
      {result.factors && <div className="table-scroll"><table className="limit-table factor-table"><thead><tr><th>{t("Factor")}</th><th>{t("Process")}</th><th>{t("Pattern")}</th><th>{t("Failures per level")}</th></tr></thead>
        <tbody>{result.factors.map(factor => <tr key={factor.name}><td><strong>{factor.name}</strong></td><td>{factor.step ? stepName(factor.step) : '—'}</td>
          <td><span className={`badge ${factor.verdict === 'specific' ? 'fail' : factor.verdict === 'common' ? 'review' : 'superseded'}`}>{t(verdictLabel[factor.verdict])}</span></td>
          <td><div className="levels">{factor.levels.map(level => <span key={level.level} className={`level ${level.ng === level.n ? 'all' : level.ng ? 'some' : 'none'}`} title={`${level.level}: ${level.mean}`}>{level.level} <b>{level.ng}/{level.n}</b></span>)}</div></td></tr>)}</tbody></table></div>}
    </section>}

    <section className="panel">
      <h2 className="step-title">{t("Process candidates")}</h2>
      {analysis.candidates.length === 0 ? <p className="muted">{t("No process in this flow is linked to this defect type. Edit the flow or describe the defect differently.")}</p>
        : <div className="candidate-list">{analysis.candidates.map(candidate => <article id={`cand-${candidate.step}`} key={candidate.step} className={`candidate ${candidate.likelihood}`}>
          <div className="candidate-head"><span className="rank">{candidate.rank}</span><div><h3>{stepName({ key: candidate.step, label: candidate.label })}</h3>
            <span className={`badge ${candidate.likelihood === 'high' ? 'fail' : candidate.likelihood === 'medium' ? 'review' : 'superseded'}`}>{t(likelihoodLabel[candidate.likelihood])}</span></div></div>
          <ul className="reasons">{candidate.reasons.map((reason, index) => <li key={index}>{fill(t(reasonText[reason.code] ?? reason.code), reason.params)}</li>)}</ul>
          {candidate.checks.length > 0 && <div className="checks"><strong>{t("Check first")}</strong>{candidate.checks.map((check, index) => {
            const key = `${candidate.step}-${index}`;
            return <label key={key} className="check"><input type="checkbox" checked={Boolean(done[key])} onChange={event => setDone({ ...done, [key]: event.target.checked })} />{check[language]}</label>;
          })}</div>}
          {candidate.countermeasures.length > 0 && <div className="checks"><strong>{t("Countermeasures used before")}</strong><ul>{candidate.countermeasures.map(item => <li key={item}>{item}</li>)}</ul></div>}
        </article>)}</div>}
    </section>

    {result.ncr && result.ncr.actions.length > 0 && <section className="panel">
      <h2 className="step-title">{t("Actions requested by the customer")}</h2>
      <div className="checks">{result.ncr.actions.map((action, index) => <label key={action} className="check"><input type="checkbox" checked={Boolean(done[`a${index}`])} onChange={event => setDone({ ...done, [`a${index}`]: event.target.checked })} />{action}</label>)}</div>
    </section>}

    <section className="panel">
      <div className="action-bar">
        {saved ? <div className="success-banner"><Icon name="check" />{t("Saved to the customer's history")}</div> : <>
          <label className="inline-field">{t("Customer")}<input maxLength={100} value={saveCustomer} onChange={event => setSaveCustomer(event.target.value)} /></label>
          <button className="primary big" disabled={busy || !saveCustomer.trim()} onClick={() => void saveCase()}><Icon name="check" />{t("Save to customer history")}</button></>}
      </div>
    </section>
  </>;
}
