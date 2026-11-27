import { useEffect, useState } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { useLanguage } from './language';
import type { Language } from './translations';

type Texts = Record<Language, string>;
type Overall = 'ELIGIBLE' | 'CHECK' | 'INELIGIBLE' | 'CLOSED';
type Group = 'eligibility' | 'exclusion' | 'preferred';

interface Condition {
  id: string; group: Group; field: string; label: Texts; source: string;
  status: 'pass' | 'fail' | 'unknown'; actual: Texts;
}
interface Match {
  conditions: Condition[]; overall: Overall; met: number; required: number;
  bonus: number; preferred: number; days_left: number | null;
}
interface Notice {
  title: string; agency: string; opens: string | null; deadline: string | null;
  amount: { text: string; manwon: number } | null; support: string[]; documents: string[]; synthetic: boolean; purpose?: string;
}
export interface Program {
  id: string; created_at: string; filename: string; source: string; notice: Notice; match: Match;
  url?: string | null; fit?: { score: number; reasons: Texts[] }; facets?: { categories: string[]; regions: string[] };
  tasks: { id: string; title: Texts; due: string }[];
}
interface Profile {
  name: string; region: string; company_type: string; industry: string;
  employees: number | null; revenue_eok: number | null; founded: number | null; export_usd: number | null;
  certifications: string[]; main_customers: string[]; received: string[];
  tax_arrears: boolean; closed: boolean; demo: boolean;
  choices: { company_types: string[]; certificates: string[]; categories?: Record<string, Texts>; regions?: string[] };
}

const overallLabel: Record<Overall, string> = {
  ELIGIBLE: 'Eligible', CHECK: 'Check needed', INELIGIBLE: 'Not eligible', CLOSED: 'Closed',
};
const overallTone: Record<Overall, string> = { ELIGIBLE: 'pass', CHECK: 'warning', INELIGIBLE: 'fail', CLOSED: 'superseded' };
const statusLabel = { pass: 'Met', fail: 'Not met', unknown: 'Check' } as const;
const statusTone = { pass: 'pass', fail: 'fail', unknown: 'warning' } as const;
const groupTitle: Record<Group, string> = {
  eligibility: 'Who can apply', exclusion: 'Exclusions', preferred: 'Preferred (bonus points)',
};

function dday(days: number | null) {
  if (days === null) return '';
  if (days < 0) return 'D+' + Math.abs(days);
  return days === 0 ? 'D-day' : `D-${days}`;
}

const listToText = (items: string[]) => items.join(', ');
const textToList = (text: string) => text.split(/[,\n]/).map(item => item.trim()).filter(Boolean);

interface FeedRun { mode: 'live' | 'sample'; fetched?: number; matched?: number; added?: number; error?: string; at: string }
interface FeedStatus { mode: 'live' | 'sample'; last: FeedRun | null }

// Province names as the notices write them (Busan, Ulsan, Gyeongnam), escaped to keep the source ASCII.
const regionNames: Record<string, Texts> = {
  '\uBD80\uC0B0': { en: 'Busan', ko: '\uBD80\uC0B0', zh: '\u91DC\u5C71' },
  '\uC6B8\uC0B0': { en: 'Ulsan', ko: '\uC6B8\uC0B0', zh: '\u851A\u5C71' },
  '\uACBD\uB0A8': { en: 'Gyeongnam', ko: '\uACBD\uB0A8', zh: '\u5E86\u5357' },
};

// "[Sample · synthetic notice] 2026 ..." -> "2026 ..."; the synthetic chip says it instead.
function plainTitle(title: string) {
  return title.replace(/^\s*\[[^\]]*\]\s*/, '');
}

export default function Programs({ busy, perform, initialProgram, onProgramUsed, onOpenTasks, initialView, initialRegion }: {
  busy: boolean; perform: (task: () => Promise<void>) => Promise<void>;
  initialProgram: string | null; onProgramUsed: () => void; onOpenTasks: () => void; initialView?: 'list' | 'profile'; initialRegion?: string;
}) {
  const { t, language, locale } = useLanguage();
  const [programs, setPrograms] = useState<Program[]>([]);
  const [selected, setSelected] = useState<Program | null>(null);
  const [view, setView] = useState<'list' | 'add' | 'profile'>(initialView ?? 'list');
  const [filter, setFilter] = useState<Overall | 'ALL'>('ALL');
  const [profile, setProfile] = useState<Profile | null>(null);
  const [draft, setDraft] = useState<Profile | null>(null);
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [saved, setSaved] = useState('');
  const [feed, setFeed] = useState<FeedStatus | null>(null);
  const [query, setQuery] = useState('');
  const [fields, setFields] = useState<Set<string>>(new Set());
  const [region, setRegion] = useState(initialRegion ?? '');
  const [due, setDue] = useState<'all' | '7' | '30'>('all');
  const [origin, setOrigin] = useState<'all' | 'bizinfo' | 'manual' | 'sample'>('all');
  const [sort, setSort] = useState<'deadline' | 'newest' | 'met'>('deadline');
  const [detailed, setDetailed] = useState(Boolean(initialRegion));
  const [showWording, setShowWording] = useState(false);

  async function load() {
    const [list, company, status] = await Promise.all([api<Program[]>('/programs'), api<Profile>('/company-profile'), api<FeedStatus>('/programs/feed').catch(() => null)]);
    setFeed(status);
    setPrograms(list);
    setProfile(company);
    setDraft(company);
  }

  useEffect(() => { void perform(load); }, []);
  useEffect(() => {
    if (!initialProgram) return;
    void perform(async () => { setSelected(await api<Program>(`/programs/${initialProgram}`)); onProgramUsed(); });
  }, [initialProgram]);

  const pick = (texts: Texts) => texts[language] || texts.en;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const searched = programs.filter(p => {
    const haystack = [p.notice.title, p.notice.agency, p.notice.purpose ?? '', ...(p.notice.support ?? [])].join(' ').toLowerCase();
    if (!words.every(word => haystack.includes(word))) return false;
    if (fields.size && !p.facets?.categories.some(key => fields.has(key))) return false;
    if (region && !(p.facets?.regions.includes(region) || p.facets?.regions.includes('*'))) return false;
    if (due !== 'all' && (p.match.days_left === null || p.match.days_left < 0 || p.match.days_left > Number(due))) return false;
    if (origin !== 'all' && (origin === 'manual' ? ['bizinfo', 'sample'].includes(p.source) : p.source !== origin)) return false;
    return true;
  });
  const counts = (kind: Overall | 'ALL') => kind === 'ALL' ? searched.length : searched.filter(p => p.match.overall === kind).length;
  const ordered = [...searched].sort((a, b) => sort === 'newest' ? b.created_at.localeCompare(a.created_at)
    : sort === 'met' ? (b.match.met / Math.max(b.match.required, 1)) - (a.match.met / Math.max(a.match.required, 1))
      : programs.indexOf(a) - programs.indexOf(b));
  const visible = ordered.filter(p => filter === 'ALL' || p.match.overall === filter);
  const filtering = words.length > 0 || fields.size > 0 || region !== '' || due !== 'all' || origin !== 'all';
  const categories = Object.entries(profile?.choices.categories ?? {});
  const toggleField = (key: string) => setFields(current => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const resetFilters = () => { setQuery(''); setFields(new Set()); setRegion(''); setDue('all'); setOrigin('all'); };
  const fmtDate = (value: string | null) => value ? new Date(value + 'T00:00:00').toLocaleDateString(locale, { month: 'short', day: 'numeric', weekday: 'short' }) : '-';

  async function collect() {
    const last = await api<FeedRun>('/programs/feed', {});
    await load();
    setFeed({ mode: last.mode, last });
  }

  async function addSamples() {
    await api<Program[]>('/programs/samples', {});
    await load();
  }

  async function submitNotice() {
    let program: Program;
    if (file) {
      const form = new FormData();
      form.append('file', file);
      program = await api<Program>('/programs/file', form);
    } else {
      program = await api<Program>('/programs', { text });
    }
    setText(''); setFile(null); setView('list');
    await load();
    setSelected(program);
  }

  async function registerTasks(program: Program) {
    setSelected(await api<Program>(`/programs/${program.id}/tasks`, {}));
    await load();
  }

  async function remove(program: Program) {
    await api(`/programs/${program.id}`, undefined, 'DELETE');
    setSelected(null);
    await load();
  }

  async function saveProfile() {
    if (!draft) return;
    const { demo: _demo, choices: _choices, ...body } = draft;
    const result = await api<Profile>('/company-profile', body, 'PUT');
    setProfile(result); setDraft(result); setSaved(t("Saved. Every notice was judged again for this profile."));
    setPrograms(await api<Program[]>('/programs'));
    if (selected) setSelected(await api<Program>(`/programs/${selected.id}`));
  }

  const profileLine = profile && <div className="profile-strip">
    <Icon name="building" size={18} />
    <span><strong>{profile.name || t("Company profile")}</strong> · {profile.region || '-'}</span>
    <button className="small ghost" onClick={() => { setSelected(null); setView('profile'); }} aria-label={t("Edit profile")} title={t("Edit profile")}><Icon name="edit" size={14} /></button>
  </div>;

  // --- detail -------------------------------------------------------------------------------
  if (selected) {
    const { notice, match } = selected;
    const groups: Group[] = ['eligibility', 'exclusion', 'preferred'];
    return <section className="panel program-detail">
      <div className="panel-top">
        <button className="ghost" onClick={() => setSelected(null)}><Icon name="back" />{t("Back")}</button>
        <span className={`badge ${overallTone[match.overall]}`}>{t(overallLabel[match.overall])}</span>
        {notice.synthetic && <span className="chip synthetic">{t("Synthetic")}</span>}
        {selected.source === 'bizinfo' && <span className="chip source-chip">{t("Bizinfo")}</span>}
      </div>
      <h1 className="program-title">{plainTitle(notice.title)}</h1>
      {selected.fit && <p className="fit-line">{selected.fit.reasons.map(pick).join(' · ')}
        {selected.url && <> · <a href={selected.url} target="_blank" rel="noopener noreferrer">{t("Open the original notice")}</a></>}</p>}
      <div className="program-meta">
        <span><Icon name="building" size={15} />{notice.agency || t("Agency not stated")}</span>
        <span><Icon name="calendar" size={15} />{notice.opens ? `${fmtDate(notice.opens)} ~ ` : ''}{fmtDate(notice.deadline)}
          {match.days_left !== null && <strong className={match.days_left <= 7 ? 'soon' : ''}>{dday(match.days_left)}</strong>}</span>
        {notice.amount && <span><Icon name="grant" size={15} />{notice.amount.text}</span>}
      </div>

      <div className={`program-verdict ${overallTone[match.overall]}`}>
        <div><strong>{match.met}/{match.required}</strong><span>{t("required conditions met")}</span></div>
        {match.preferred > 0 && <div><strong>{match.bonus}/{match.preferred}</strong><span>{t("preferred conditions met")}</span></div>}
        <button type="button" className="ghost small wording-toggle" aria-pressed={showWording} onClick={() => setShowWording(value => !value)}>
          <Icon name="file" size={14} />{t(showWording ? "Hide notice wording" : "Show notice wording")}</button>
      </div>

      {groups.map(group => {
        const rows = match.conditions.filter(c => c.group === group);
        if (!rows.length) return null;
        return <div key={group} className="condition-block">
          <h2>{t(groupTitle[group])}</h2>
          <div className="table-scroll"><table className="program-table">
            <thead><tr><th>{t("Condition")}</th>{showWording && <th>{t("The notice says")}</th>}<th>{t("Our company")}</th><th>{t("Result")}</th></tr></thead>
            <tbody>{rows.map(row => <tr key={row.id} className={row.status} title={showWording ? undefined : row.source}>
              <td><strong>{pick(row.label)}</strong></td>
              {showWording && <td className="original-text">“{row.source}”</td>}
              <td>{pick(row.actual)}</td>
              <td><span className={`badge ${statusTone[row.status]}`}>{t(statusLabel[row.status])}</span></td>
            </tr>)}</tbody>
          </table></div>
        </div>;
      })}

      <div className="condition-block">
        <h2>{t("Documents to submit")}</h2>
        {notice.documents.length ? <ul className="doc-list">{notice.documents.map(doc => <li key={doc}><Icon name="file" size={15} />{doc}</li>)}</ul>
          : <p className="muted">{t("No document list was found in the notice.")}</p>}
        <div className="card-actions">
          {selected.tasks.length
            ? <><span className="badge pass"><Icon name="check" size={14} />{t("Tasks registered")}: {selected.tasks.length}</span>
              <button onClick={onOpenTasks}><Icon name="calendar" size={16} />{t("Open tasks & schedule")}</button></>
            : <button className="primary" disabled={busy || match.overall === 'INELIGIBLE' || match.overall === 'CLOSED'}
              onClick={() => perform(() => registerTasks(selected))}><Icon name="calendar" size={16} />{t("Register documents and deadline as tasks")}</button>}
          <button className="ghost danger-text" disabled={busy} onClick={() => perform(() => remove(selected))}><Icon name="trash" size={16} />{t("Delete")}</button>
        </div>
        {!selected.tasks.length && <p className="muted">{t("Documents are due five days before the deadline, the application one day before.")}</p>}
      </div>
    </section>;
  }

  // --- add a notice ---------------------------------------------------------------------------
  if (view === 'add') return <section className="panel">
    <div className="panel-top"><button className="ghost" onClick={() => setView('list')}><Icon name="back" />{t("Back")}</button><h1>{t("Add a support programme notice")}</h1></div>
    <p className="muted">{t("Paste the notice text or upload its PDF. Each agency writes notices differently; the agent reads them into the same conditions.")}</p>
    <div className="form-grid">
      <label>{t("Notice text")}<textarea rows={14} value={text} disabled={!!file} placeholder={t("Paste the whole notice: who can apply, exclusions, period, documents...")}
        onChange={event => setText(event.target.value)} /></label>
      <label>{t("Or upload a PDF / text file")}<input type="file" accept=".pdf,.txt,.md" onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
    </div>
    <p className="muted"><Icon name="info" size={14} /> {t("Read by rules on this PC. The notice and your company data are not sent to an external AI.")}</p>
    <div className="card-actions"><button className="primary" disabled={busy || (!file && text.trim().length < 20)} onClick={() => perform(submitNotice)}>
      <Icon name="scan" size={16} />{t("Read and judge")}</button></div>
  </section>;

  // --- company profile ------------------------------------------------------------------------
  if (view === 'profile' && draft) {
    const set = (patch: Partial<Profile>) => { setDraft({ ...draft, ...patch }); setSaved(''); };
    const num = (value: string) => value === '' ? null : Number(value);
    return <section className="panel">
      <div className="panel-top"><button className="ghost" onClick={() => setView('list')}><Icon name="back" />{t("Back")}</button><h1>{t("Company profile")}</h1>
        {draft.demo && <span className="chip demo-chip">{t("Demo company")}</span>}</div>
      <p className="muted">{t("Enter it once. Every support notice is judged against this profile, and it stays on this PC.")}</p>
      <div className="form-grid three">
        <label>{t("Company name")}<input value={draft.name} onChange={e => set({ name: e.target.value })} /></label>
        <label>{t("Location (province, city)")}<input value={draft.region} placeholder="Gyeongnam Changwon" onChange={e => set({ region: e.target.value })} /></label>
        <label>{t("Company type")}<select value={draft.company_type} onChange={e => set({ company_type: e.target.value })}>
          {draft.choices.company_types.map(kind => <option key={kind} value={kind}>{kind}</option>)}</select></label>
        <label>{t("Industry")}<input value={draft.industry} onChange={e => set({ industry: e.target.value })} /></label>
        <label>{t("Employees")}<input type="number" min={0} value={draft.employees ?? ''} onChange={e => set({ employees: num(e.target.value) })} /></label>
        <label>{t("Annual revenue (×100M KRW)")}<input type="number" min={0} step="0.1" value={draft.revenue_eok ?? ''} onChange={e => set({ revenue_eok: num(e.target.value) })} /></label>
        <label>{t("Year founded")}<input type="number" min={1900} max={2100} value={draft.founded ?? ''} onChange={e => set({ founded: num(e.target.value) })} /></label>
        <label>{t("Exports last year (USD)")}<input type="number" min={0} value={draft.export_usd ?? ''} placeholder={t("Leave empty if unknown")} onChange={e => set({ export_usd: num(e.target.value) })} /></label>
        <label>{t("Main customers (prime contractors)")}<input value={listToText(draft.main_customers)} onChange={e => set({ main_customers: textToList(e.target.value) })} /></label>
      </div>
      <div className="field-label" style={{ marginTop: 14 }}>{t("Certifications")}</div>
      <div className="chips-row">{draft.choices.certificates.map(name => {
        const on = draft.certifications.includes(name);
        return <button key={name} type="button" className={`chip-button ${on ? 'selected' : ''}`} aria-pressed={on}
          onClick={() => set({ certifications: on ? draft.certifications.filter(item => item !== name) : [...draft.certifications, name] })}>
          {on && <Icon name="check" size={13} />}{name}</button>;
      })}</div>
      <div className="form-grid two" style={{ marginTop: 14 }}>
        <label>{t("Programmes received in the last 3 years")}<input value={listToText(draft.received)} placeholder={t("Comma separated")} onChange={e => set({ received: textToList(e.target.value) })} /></label>
      </div>
      <div className="profile-flags">
        <label><input type="checkbox" checked={draft.tax_arrears} onChange={e => set({ tax_arrears: e.target.checked })} />{t("Unpaid national or local taxes")}</label>
        <label><input type="checkbox" checked={draft.closed} onChange={e => set({ closed: e.target.checked })} />{t("Business suspended or closed")}</label>
      </div>
      {saved && <p className="success-banner" role="status">{saved}</p>}
      <div className="card-actions"><button className="primary" disabled={busy} onClick={() => perform(saveProfile)}><Icon name="check" size={16} />{t("Save and judge again")}</button></div>
    </section>;
  }

  // --- list -----------------------------------------------------------------------------------
  return <>
    <header className="page-header"><h1>{t("Support")}</h1>
      <div className="card-actions">
        <button className="icon-button" disabled={busy} onClick={() => setView('add')} aria-label={t("Add a notice")} title={t("Add a notice")}><Icon name="plus" size={18} /></button>
        <button className="primary" disabled={busy} onClick={() => perform(collect)} title={feed?.mode === 'live' ? t("Every 12 hours with your API key") : t("No API key: a synthetic sample response is used")}><Icon name="download" size={16} />{t("Collect now")}</button>
      </div></header>
    <div className="context-line">
      {profileLine}
      {feed?.last && <span className={`feed-status ${feed.last.error ? 'bad' : ''}`} title={feed.last.error ?? undefined}>
        <Icon name="refresh" size={14} />{t("Bizinfo")}{feed.mode === 'sample' && <span className="chip">{t("Sample")}</span>}
        {feed.last.error ? t("Collection failed") : <>{new Date(feed.last.at).toLocaleString(locale, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · +{feed.last.added ?? 0}</>}</span>}
    </div>
    <div className="program-search" role="search">
      <div className="search-line">
        <label className="search-box"><Icon name="search" size={16} /><span className="sr-only">{t("Search notices")}</span>
          <input type="search" value={query} maxLength={100} placeholder={t("Search title, agency or content")} onChange={event => setQuery(event.target.value)} /></label>
        <button type="button" className={detailed ? 'selected' : ''} aria-expanded={detailed} onClick={() => setDetailed(value => !value)}><Icon name="filter" size={16} />{t("Detailed search")}</button>
        <select aria-label={t("Sort")} value={sort} onChange={event => setSort(event.target.value as typeof sort)}>
          <option value="deadline">{t("Closing soonest")}</option>
          <option value="newest">{t("Newest first")}</option>
          <option value="met">{t("Most conditions met")}</option>
        </select>
      </div>
      <div className="field-chips" role="group" aria-label={t("Support field")}>
        {categories.map(([key, label]) => <button type="button" key={key} aria-pressed={fields.has(key)} className={`chip-button ${fields.has(key) ? 'selected' : ''}`} onClick={() => toggleField(key)}>{pick(label)}</button>)}
      </div>
      {detailed && <div className="detail-filters">
        <label>{t("Region")}<select value={region} onChange={event => setRegion(event.target.value)}>
          <option value="">{t("All regions")}</option>
          {(profile?.choices.regions ?? []).map(name => <option key={name} value={name}>{regionNames[name] ? pick(regionNames[name]) : name}</option>)}
        </select><small>{t("Nationwide notices are included")}</small></label>
        <label>{t("Deadline")}<select value={due} onChange={event => setDue(event.target.value as typeof due)}>
          <option value="all">{t("Any time")}</option>
          <option value="7">{t("Within 7 days")}</option>
          <option value="30">{t("Within 30 days")}</option>
        </select></label>
        <label>{t("Notice source")}<select value={origin} onChange={event => setOrigin(event.target.value as typeof origin)}>
          <option value="all">{t("All sources")}</option>
          <option value="bizinfo">{t("Bizinfo")}</option>
          <option value="manual">{t("Added by us")}</option>
          <option value="sample">{t("Synthetic samples")}</option>
        </select></label>
      </div>}
      {filtering && <p className="search-result">{t("{count} notices match").replace('{count}', String(searched.length))}
        <button type="button" className="ghost small" onClick={resetFilters}><Icon name="x" size={14} />{t("Clear filters")}</button></p>}
    </div>
    <div className="status-tabs" role="group" aria-label={t("Filter programmes")}>
      {(['ALL', 'ELIGIBLE', 'CHECK', 'INELIGIBLE'] as const).map(kind => <button key={kind} aria-pressed={filter === kind}
        className={`${filter === kind ? 'selected' : ''} ${kind === 'ALL' ? '' : overallTone[kind]}`} onClick={() => setFilter(kind)}>
        {t(kind === 'ALL' ? 'All' : overallLabel[kind])}<b>{counts(kind)}</b></button>)}
    </div>
    <section className="program-list">
      {programs.length === 0
        ? <div className="empty"><p>{t("No notices yet. Add one, drop a file into the support-notice work folder, or load the samples.")}</p>
          <button className="primary big" disabled={busy} onClick={() => perform(addSamples)}><Icon name="file" />{t("Load sample notices")}</button></div>
        : visible.length === 0 ? <div className="empty"><p className="muted center">{filtering ? t("No notices match these filters.") : t("No notices in this group.")}</p>
          {filtering && <button type="button" onClick={resetFilters}>{t("Clear filters")}</button>}</div>
          : <div className="inspection-list">{visible.map(program => <button key={program.id} className="inspection-row program-row" disabled={busy} onClick={() => setSelected(program)}>
            <div><strong>{program.notice.synthetic && <span className="chip synthetic">{t("Synthetic")}</span>}{plainTitle(program.notice.title)}</strong>
              {program.facets && <span className="row-tags">{program.facets.categories.map(key => <span key={key} className="tag">{pick(profile?.choices.categories?.[key] ?? { en: key, ko: key, zh: key })}</span>)}
                {program.facets.regions.map(name => <span key={name} className="tag region">{name === '*' ? t("Nationwide") : regionNames[name] ? pick(regionNames[name]) : name}</span>)}</span>}
              <small>{program.source === 'bizinfo' && <span className="source-mark">{t("Bizinfo")} · </span>}{program.notice.agency || '-'}
                {program.notice.amount && <> · {program.notice.amount.text}</>}{program.tasks.length > 0 && <> · <Icon name="calendar" size={12} /></>}</small></div>
            <span className="met" title={t("Conditions met")}><b>{program.match.met}</b>/{program.match.required}</span>
            {program.match.days_left !== null && program.match.overall !== 'CLOSED'
              ? <span className={`chip ${program.match.days_left <= 7 ? 'soon' : ''}`}>{dday(program.match.days_left)}</span> : <span />}
            <span className={`badge ${overallTone[program.match.overall]}`}>{t(overallLabel[program.match.overall])}</span>
            <Icon name="next" size={16} />
          </button>)}</div>}
    </section>
  </>;
}
