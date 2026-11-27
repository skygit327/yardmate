import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { useLanguage } from './language';
import type { Language } from './translations';

type Texts = Record<Language, string>;
export interface TaskLink { type: 'standard' | 'request' | 'form' | 'trace' | 'program'; id: string }
interface Task {
  id: string; title: Texts; detail: string; due: string; status: 'TODO' | 'DONE'; source: 'automation' | 'manual';
  kind: string; file?: string; customer?: string; link: TaskLink | null; job_id?: string;
}
interface Job {
  id: string; kind: 'customer_forms' | 'ncr' | 'requests'; filename: string; created_at: string; status: 'DONE' | 'NEEDS_REVIEW' | 'ERROR';
  read: Texts[]; created: { type: TaskLink['type']; id: string; label: Texts }[]; warnings: Texts[]; tasks: { id: string }[]; link?: TaskLink; error: string | null;
}
interface Status { enabled: boolean; watching: boolean; root: string | null; folders: Record<string, string>; last_scan: string | null; jobs: Job[] }

const kindLabel: Record<string, string> = { customer_forms: 'Customer form', ncr: 'NCR', requests: 'Customer request', manual: 'My task' };
const statusLabel = { DONE: 'Processed', NEEDS_REVIEW: 'Needs review', ERROR: 'Failed' };
const folderOrder = ['customer_forms', 'ncr', 'requests', 'reports', 'done', 'failed'];
const folderLabel: Record<string, string> = {
  customer_forms: 'Customer forms (PDF)', ncr: 'NCR (PDF)', requests: 'Customer requests (mail, documents)', reports: 'Reports', done: 'Processed originals', failed: 'Could not process',
};

function isoDate(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

export default function Tasks({ busy, perform, onOpen, initialView = 'board' }: {
  busy: boolean; perform: (task: () => Promise<void>) => Promise<void>; onOpen: (link: TaskLink) => void;
  initialView?: 'board' | 'calendar' | 'automation';
}) {
  const { t, language, locale } = useLanguage();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [view, setView] = useState<'board' | 'calendar' | 'automation'>(initialView);
  const [month, setMonth] = useState(() => { const now = new Date(); return new Date(now.getFullYear(), now.getMonth(), 1); });
  const [day, setDay] = useState<string>(isoDate(new Date()));
  const [newTitle, setNewTitle] = useState('');
  const [showDone, setShowDone] = useState(false);

  async function load() {
    const [nextTasks, nextStatus] = await Promise.all([api<Task[]>('/tasks'), api<Status>('/automation')]);
    setTasks(nextTasks);
    setStatus(nextStatus);
  }
  useEffect(() => {
    void perform(load);
    const timer = window.setInterval(() => { load().catch(() => undefined); }, 15000);
    return () => window.clearInterval(timer);
  }, []);

  const today = isoDate(new Date());
  const weekEnd = isoDate(new Date(Date.now() + 6 * 864e5));
  const title = (task: Task) => task.title[language] || task.title.en;
  const open = tasks.filter(task => task.status === 'TODO');
  const columns = [
    { key: 'overdue', label: 'Overdue', items: open.filter(task => task.due < today) },
    { key: 'today', label: 'Today', items: open.filter(task => task.due === today) },
    { key: 'week', label: 'This week', items: open.filter(task => task.due > today && task.due <= weekEnd) },
    { key: 'later', label: 'Later', items: open.filter(task => task.due > weekEnd) },
  ];
  const done = tasks.filter(task => task.status === 'DONE');

  async function update(task: Task, change: Partial<{ status: 'TODO' | 'DONE'; due: string }>) {
    await perform(async () => { await api(`/tasks/${task.id}`, change, 'PATCH'); await load(); });
  }

  async function add(due: string) {
    if (!newTitle.trim()) return;
    await perform(async () => { await api('/tasks', { title: newTitle.trim(), due }); setNewTitle(''); await load(); });
  }

  function TaskCard({ task }: { task: Task }) {
    return <article className={`task-card ${task.status === 'DONE' ? 'done' : ''} ${task.due < today && task.status === 'TODO' ? 'late' : ''}`}>
      <label className="task-check"><input type="checkbox" aria-label={t("Done")} checked={task.status === 'DONE'} disabled={busy}
        onChange={event => void update(task, { status: event.target.checked ? 'DONE' : 'TODO' })} /></label>
      <div className="task-body">
        <strong>{title(task)}</strong>
        <small><span className={`chip kind-${task.kind}`}>{t(kindLabel[task.kind] ?? task.kind)}</span>
          {task.customer ? ` ${task.customer} ·` : ''} {new Date(`${task.due}T00:00`).toLocaleDateString(locale, { month: 'short', day: 'numeric', weekday: 'short' })}</small>
      </div>
      <div className="task-actions">
        <input type="date" aria-label={t("Due date")} value={task.due} disabled={busy} onChange={event => event.target.value && void update(task, { due: event.target.value })} />
        {task.link && <button className="small" onClick={() => onOpen(task.link!)}><Icon name="next" size={14} />{t("Open item")}</button>}
        {task.job_id && <a className="button small" href={`/api/automation/jobs/${task.job_id}/report`} target="_blank" rel="noreferrer" title={t("Processing report")}><Icon name="file" size={14} /></a>}
      </div>
    </article>;
  }

  const calendar = useMemo(() => {
    const first = new Date(month);
    const start = new Date(first); start.setDate(1 - ((first.getDay() + 6) % 7));
    return Array.from({ length: 42 }, (_, index) => { const value = new Date(start); value.setDate(start.getDate() + index); return value; });
  }, [month]);
  const byDay = useMemo(() => {
    const map: Record<string, Task[]> = {};
    for (const task of tasks) (map[task.due] ??= []).push(task);
    return map;
  }, [tasks]);
  const weekdays = Array.from({ length: 7 }, (_, index) => new Date(2026, 0, 5 + index).toLocaleDateString(locale, { weekday: 'short' }));
  const needsReview = status?.jobs.filter(job => job.status !== 'DONE').length ?? 0;

  return <>
    <header className="page-header"><div><h1>{t("Tasks & schedule")}</h1>
      <p className="muted">{t("Files dropped into the work folders are processed automatically; their follow-up tasks appear here.")}</p></div>
      <button className="button" disabled={busy} onClick={() => void perform(async () => { await api('/automation/scan', {}); await load(); })}><Icon name="refresh" />{t("Check folders now")}</button></header>

    <div className="tabs" role="tablist">
      {([['board', 'Board'], ['calendar', 'Calendar'], ['automation', 'Automation']] as const).map(([value, label]) => <button key={value} role="tab" aria-selected={view === value}
        className={view === value ? 'selected' : ''} onClick={() => setView(value)}>{t(label)}{value === 'automation' && needsReview ? <span className="count-dot">{needsReview}</span> : null}</button>)}
    </div>

    {view === 'board' && <>
      <section className="panel quick-add">
        <input maxLength={300} placeholder={t("Add a task for today…")} value={newTitle} onChange={event => setNewTitle(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void add(today); }} />
        <button className="primary" disabled={busy || !newTitle.trim()} onClick={() => void add(today)}><Icon name="plus" />{t("Add")}</button>
      </section>
      <div className="task-board">{columns.map(column => <section key={column.key} className={`task-column ${column.key}`}>
        <h2>{t(column.label)} <span className="chip">{column.items.length}</span></h2>
        {column.items.length ? column.items.map(task => <TaskCard key={task.id} task={task} />) : <p className="muted center">{t("Nothing here")}</p>}
      </section>)}</div>
      {done.length > 0 && <details className="history" open={showDone} onToggle={event => setShowDone((event.target as HTMLDetailsElement).open)}>
        <summary><Icon name="check" size={16} />{t("Completed")} · {done.length}</summary>
        {showDone && done.map(task => <TaskCard key={task.id} task={task} />)}</details>}
    </>}

    {view === 'calendar' && <div className="calendar-layout">
      <section className="panel calendar">
        <div className="calendar-head">
          <button className="icon-button" aria-label={t("Previous month")} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><Icon name="back" /></button>
          <h2>{month.toLocaleDateString(locale, { year: 'numeric', month: 'long' })}</h2>
          <button className="icon-button" aria-label={t("Next month")} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><Icon name="next" /></button>
          <button className="small" onClick={() => { const now = new Date(); setMonth(new Date(now.getFullYear(), now.getMonth(), 1)); setDay(today); }}>{t("Today")}</button>
        </div>
        <div className="calendar-grid">
          {weekdays.map(name => <span key={name} className="weekday">{name}</span>)}
          {calendar.map(value => {
            const key = isoDate(value);
            const items = byDay[key] ?? [];
            const openItems = items.filter(task => task.status === 'TODO');
            return <button key={key} className={`calendar-day ${value.getMonth() !== month.getMonth() ? 'other' : ''} ${key === today ? 'today' : ''} ${key === day ? 'selected' : ''}`} onClick={() => setDay(key)}>
              <span className="date">{value.getDate()}</span>
              {items.slice(0, 3).map(task => <span key={task.id} className={`calendar-task ${task.status === 'DONE' ? 'done' : task.due < today ? 'late' : `kind-${task.kind}`}`}>{title(task)}</span>)}
              {items.length > 3 && <small>+{items.length - 3}</small>}
              {openItems.length > 0 && <span className="sr-only">{openItems.length}</span>}
            </button>;
          })}
        </div>
      </section>
      <section className="panel day-panel">
        <h2>{new Date(`${day}T00:00`).toLocaleDateString(locale, { month: 'long', day: 'numeric', weekday: 'long' })}</h2>
        <div className="quick-add"><input maxLength={300} placeholder={t("Add a task on this day…")} value={newTitle} onChange={event => setNewTitle(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void add(day); }} />
          <button className="primary icon-button" aria-label={t("Add")} disabled={busy || !newTitle.trim()} onClick={() => void add(day)}><Icon name="plus" /></button></div>
        {(byDay[day] ?? []).length ? (byDay[day] ?? []).map(task => <TaskCard key={task.id} task={task} />) : <p className="muted center">{t("Nothing here")}</p>}
      </section>
    </div>}

    {view === 'automation' && status && <>
      <section className="panel">
        <div className="step-row"><h2 className="step-title">{t("Work folders")}</h2>
          <span className={`badge ${status.watching ? 'pass' : 'superseded'}`}>{status.watching ? t("Watching for new files") : status.enabled ? t("Not watching") : t("Automation is off")}</span></div>
        {status.enabled ? <ul className="folder-list">{folderOrder.filter(key => status.folders[key]).map(key => <li key={key}>
          <Icon name={['customer_forms', 'ncr', 'requests'].includes(key) ? 'upload' : 'file'} size={16} /><strong>{t(folderLabel[key])}</strong><code>{status.folders[key]}</code></li>)}</ul>
          : <p className="muted">{t("Set SPEC_AGENT_AUTOMATION=true to watch the work folders.")}</p>}
        <p className="muted">{t("Drafts and reports are created automatically. Activating standards, approving reports and sending replies stay with a person.")}</p>
        {status.last_scan && <small className="muted">{t("Last check")}: {new Date(status.last_scan).toLocaleString(locale)}</small>}
      </section>
      <section className="panel">
        <h2 className="step-title">{t("Processed files")}</h2>
        {status.jobs.length === 0 ? <p className="muted center">{t("No files processed yet. Drop a file into a work folder.")}</p>
          : <div className="job-list">{status.jobs.map(job => <article key={job.id} className={`job ${job.status.toLowerCase()}`}>
            <div className="job-head"><span className={`badge ${job.status === 'DONE' ? 'pass' : job.status === 'ERROR' ? 'fail' : 'review'}`}>{t(statusLabel[job.status])}</span>
              <strong>{job.filename}</strong><span className="chip">{t(kindLabel[job.kind])}</span><small>{new Date(job.created_at).toLocaleString(locale)}</small></div>
            <ul>{job.read.map((line, index) => <li key={index}>{line[language] || line.en}</li>)}
              {job.warnings.map((line, index) => <li key={`w${index}`} className="warn">{line[language] || line.en}</li>)}</ul>
            <div className="inline">
              {job.created.map(item => <button key={`${item.type}-${item.id}`} className="small" onClick={() => onOpen({ type: item.type, id: item.id })}><Icon name="next" size={14} />{item.label[language] || item.label.en}</button>)}
              <a className="button small" href={`/api/automation/jobs/${job.id}/report`} target="_blank" rel="noreferrer"><Icon name="file" size={14} />{t("Processing report")}</a>
              <span className="muted">{t("Tasks")}: {job.tasks.length}</span></div>
          </article>)}</div>}
      </section>
    </>}
  </>;
}
