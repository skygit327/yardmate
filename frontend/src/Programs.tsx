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
  amount: { text: string; manwon: number } | null; support: string[]; documents: string[]; synthetic: boolean;
}
export interface Program {
  id: string; created_at: string; filename: string; source: string; notice: Notice; match: Match;
  url?: string | null; fit?: { score: number; reasons: Texts[] };
  tasks: { id: string; title: Texts; due: string }[];
}
interface Profile {
  name: string; region: string; company_type: string; industry: string;
  employees: number | null; revenue_eok: number | null; founded: number | null; export_usd: number | null;
  certifications: string[]; main_customers: string[]; received: string[];
  tax_arrears: boolean; closed: boolean; demo: boolean;
  choices: { company_types: string[]; certificates: string[] };
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
const overallHint: Record<Overall, string> = {
  ELIGIBLE: 'Every required condition is met. Register the documents and deadline as tasks.',
  CHECK: 'No condition fails, but some could not be decided automatically. Read the highlighted sentences.',
  INELIGIBLE: 'At least one required condition is not met.',
  CLOSED: 'The application period has ended.',
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

export default function Programs({ busy, perform, initialProgram, onProgramUsed, onOpenTasks, initialView }: {
  busy: boolean; perform: (task: () => Promise<void>) => Promise<void>;
  initialProgram: string | null; onProgramUsed: () => void; onOpenTasks: () => void; initialView?: 'list' | 'profile';
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
  const counts = (kind: Overall | 'ALL') => kind === 'ALL' ? programs.length : programs.filter(p => p.match.overall === kind).length;
  const visible = programs.filter(p => filter === 'ALL' || p.match.overall === filter);
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
    <span><strong>{profile.name || t("Company profile")}</strong> · {profile.region || '-'} · {profile.industry || '-'}
      {profile.employees !== null && <> · {profile.employees} {t("employees")}</>}
      {profile.revenue_eok !== null && <> · {t("revenue")} {profile.revenue_eok} {t("×100M KRW")}</>}</span>
    {profile.demo && <span className="chip demo-chip">{t("Demo company")}</span>}
    <button className="small ghost" onClick={() => { setSelected(null); setView('profile'); }}><Icon name="edit" size={14} />{t("Edit profile")}</button>
  </div>;

  // --- detail -------------------------------------------------------------------------------
  if (selected) {
    const { notice, match } = selected;
    const groups: Group[] = ['eligibility', 'exclusion', 'preferred'];
    return <section className="panel program-detail">
      <div className="panel-top">
        <button className="ghost" onClick={() => setSelected(null)}><Icon name="back" />{t("Back")}</button>
        <span className={`badge ${overallTone[match.overall]}`}>{t(overallLabel[match.overall])}</span>
        {notice.synthetic && <span className="chip">{t("Synthetic sample notice")}</span>}
        {selected.source === 'bizinfo' && <span className="chip source-chip">{t("Collected from Bizinfo")}</span>}
      </div>
      <h1 className="program-title">{notice.title}</h1>
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
        <p>{t(overallHint[match.overall])}</p>
      </div>
      {profileLine}

      {groups.map(group => {
        const rows = match.conditions.filter(c => c.group === group);
        if (!rows.length) return null;
        return <div key={group} className="condition-block">
          <h2>{t(groupTitle[group])}</h2>
          <div className="table-scroll"><table className="program-table">
            <thead><tr><th>{t("Condition")}</th><th>{t("The notice says")}</th><th>{t("Our company")}</th><th>{t("Result")}</th></tr></thead>
            <tbody>{rows.map(row => <tr key={row.id} className={row.status}>
              <td><strong>{pick(row.label)}</strong></td>
              <td className="original-text">“{row.source}”</td>
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
    <header className="page-header"><div><h1>{t("Support programmes")}</h1>
      <p className="muted">{t("Notices from Busan, Ulsan and Gyeongnam agencies, judged for your company")}</p></div>
      <div className="card-actions">
        <button disabled={busy} onClick={() => perform(addSamples)}><Icon name="file" size={16} />{t("Load sample notices")}</button>
        <button className="primary" disabled={busy} onClick={() => setView('add')}><Icon name="plus" size={16} />{t("Add a notice")}</button>
      </div></header>
    {profileLine}
    <div className="feed-bar">
      <span className="feed-text"><Icon name="refresh" size={16} />
        <strong>{t("Automatic collection from Bizinfo")}</strong>
        <span className="muted">{feed?.mode === 'live' ? t("Every 12 hours with your API key") : t("No API key: a synthetic sample response is used")}</span>
        {feed?.last && (feed.last.error
          ? <span className="feed-error">{t("Collection failed")}: {feed.last.error}</span>
          : <span>{t("Last run")} {new Date(feed.last.at).toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · {t("{matched} of {fetched} fit your company, {added} new").replace('{matched}', String(feed.last.matched ?? 0)).replace('{fetched}', String(feed.last.fetched ?? 0)).replace('{added}', String(feed.last.added ?? 0))}</span>)}
      </span>
      <button disabled={busy} onClick={() => perform(collect)}><Icon name="download" size={16} />{t("Collect now")}</button>
    </div>
    <div className="filter-tiles four" role="group" aria-label={t("Filter programmes")}>
      {(['ELIGIBLE', 'CHECK', 'INELIGIBLE', 'ALL'] as const).map(kind => <button key={kind} aria-pressed={filter === kind}
        className={`${filter === kind ? 'selected' : ''} ${kind === 'INELIGIBLE' && counts(kind) ? 'bad' : ''}`} onClick={() => setFilter(kind)}>
        <strong>{counts(kind)}</strong><span>{t(kind === 'ALL' ? 'All notices' : overallLabel[kind])}</span></button>)}
    </div>
    <section className="panel">
      {programs.length === 0
        ? <div className="empty"><p>{t("No notices yet. Add one, drop a file into the support-notice work folder, or load the samples.")}</p>
          <button className="primary big" disabled={busy} onClick={() => perform(addSamples)}><Icon name="file" />{t("Load sample notices")}</button></div>
        : visible.length === 0 ? <p className="muted center">{t("No notices in this group.")}</p>
          : <div className="inspection-list">{visible.map(program => <button key={program.id} className="inspection-row program-row" disabled={busy} onClick={() => setSelected(program)}>
            <div><strong>{program.notice.title}</strong>
              <small>{program.source === 'bizinfo' && <span className="source-mark">{t("Bizinfo")} · </span>}{program.notice.agency || '-'} · {t("Deadline")} {fmtDate(program.notice.deadline)}
                {program.notice.amount && <> · {program.notice.amount.text}</>} · {t("Conditions met")} {program.match.met}/{program.match.required}
                {program.tasks.length > 0 && <> · {t("Tasks registered")}</>}</small></div>
            {program.match.days_left !== null && program.match.overall !== 'CLOSED'
              ? <span className={`chip ${program.match.days_left <= 7 ? 'soon' : ''}`}>{dday(program.match.days_left)}</span> : <span />}
            <span className={`badge ${overallTone[program.match.overall]}`}>{t(overallLabel[program.match.overall])}</span>
            <Icon name="next" size={16} />
          </button>)}</div>}
    </section>
  </>;
}
