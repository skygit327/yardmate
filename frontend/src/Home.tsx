import { useEffect, useState } from 'react';
import { api } from './api';
import { Icon } from './icons';
import LiveMap from './LiveMap';
import { useLanguage } from './language';
import type { InspectionSummary, Standard } from './types';

interface Summary {
  tasks: { overdue: number; today: number; week: number; open: number };
  drafts: number; active_standards: number; pending_inspections: number; automation: boolean;
  programs: { eligible: number; check: number; closing: number; total: number } | null;
}

// Approximate positions on a stylised Busan–Ulsan–Gyeongnam coast (not to scale).
const places = [
  { name: 'Ulsan', x: 700, y: 132, kind: 'yard', note: 'Coating customer', side: 'left' },
  { name: 'Ulsan Dong-gu', x: 752, y: 178, kind: 'crisis', side: 'left' },
  { name: 'Busan port', x: 574, y: 398, kind: 'port', side: 'left' },
  { name: 'Changwon Jinhae', x: 404, y: 372, kind: 'crisis', side: 'right' },
  { name: 'Goseong', x: 176, y: 448, kind: 'crisis', side: 'right' },
  { name: 'Tongyeong', x: 250, y: 516, kind: 'crisis', note: 'Pipe shop', side: 'left' },
  { name: 'Geoje', x: 362, y: 494, kind: 'home', note: 'Our plant · hull customer', side: 'right' },
];

const land = 'M0 0H800V40C792 70 786 96 772 122C756 150 740 170 724 196C706 226 690 252 670 282C650 312 630 340 606 366C590 384 574 400 552 410C530 420 512 412 494 420C474 428 460 412 440 400C420 388 406 396 392 392C372 386 360 404 338 410C318 416 304 400 284 410C262 420 250 444 228 452C206 460 190 438 168 440C140 442 128 470 100 474C70 478 40 466 0 470Z';
const island = 'M330 462C352 448 386 452 400 474C412 494 404 520 386 534C366 548 340 544 330 526C322 512 336 498 326 486C320 476 322 468 330 462Z';
const peninsula = 'M226 488C240 476 262 476 276 488C288 500 282 524 266 532C248 540 230 532 222 516C216 504 218 496 226 488Z';

function MapLegend({ note }: { note: string }) {
  const { t } = useLanguage();
  return <figcaption>
    <span><i className="map-key home" />{t("Our plant")}</span>
    <span><i className="map-key yard" />{t("Customer shipyard")}</span>
    <span><i className="map-key crisis" />{t("Shipbuilding crisis area")}</span>
    <span className="muted">{t(note)}</span>
  </figcaption>;
}

function CoastMap() {
  const { t } = useLanguage();
  return <figure className="coast-map">
    <svg viewBox="0 0 800 560" role="img" aria-label={t("Shipbuilding suppliers on the Busan–Ulsan–Gyeongnam coast")}>
      <defs>
        <pattern id="sea-lines" width="800" height="14" patternUnits="userSpaceOnUse"><path d="M0 7H800" stroke="#b9d0de" strokeWidth="1" /></pattern>
      </defs>
      <rect width="800" height="560" fill="#d6e5ee" />
      <rect width="800" height="560" fill="url(#sea-lines)" opacity=".7" />
      <path d={land} className="map-land" />
      <path d={island} className="map-land" />
      <path d={peninsula} className="map-land" />
      <text x="250" y="230" className="map-region">{t("Gyeongnam")}</text>
      <text x="560" y="350" className="map-region" textAnchor="end">{t("Busan")}</text>
      <text x="640" y="80" className="map-region" textAnchor="end">{t("Ulsan")}</text>
      <text x="560" y="510" className="map-sea">{t("South Sea")}</text>
      {places.map(place => <g key={place.name} className={`map-place ${place.kind}`} transform={`translate(${place.x} ${place.y})`}>
        {place.kind === 'crisis' || place.kind === 'home' ? <circle r="22" className="crisis-ring" /> : null}
        {place.kind === 'home' && <circle r="9" className="home-dot" />}
        {place.kind === 'yard' && <rect x="-7" y="-7" width="14" height="14" className="yard-dot" />}
        {place.kind === 'port' && <path d="M0 -9V9M-7 3C-6 9 6 9 7 3M-4 -4H4" className="port-mark" />}
        {place.kind === 'crisis' && <circle r="5" className="crisis-dot" />}
        <text x={place.side === 'left' ? -28 : 28} y="5" textAnchor={place.side === 'left' ? 'end' : 'start'} className="map-label">{t(place.name)}</text>
        {place.note && <text x={place.side === 'left' ? -28 : 28} y="25" textAnchor={place.side === 'left' ? 'end' : 'start'} className="map-note">{t(place.note)}</text>}
      </g>)}
    </svg>
    <MapLegend note="Synthetic companies" />
  </figure>;
}

export default function Home({ userName, onGo, busy, perform, onLoaded }: {
  userName: string; onGo: (page: string, params?: Record<string, string>) => void; onAsk?: (message: string) => void;
  busy: boolean; perform: (task: () => Promise<void>) => Promise<void>; onLoaded: () => void;
}) {
  const { t, locale } = useLanguage();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [standards, setStandards] = useState<Standard[]>([]);
  const [inspections, setInspections] = useState<InspectionSummary[]>([]);
  const [version, setVersion] = useState(0);
  const [mapFailed, setMapFailed] = useState(false);

  useEffect(() => {
    api<Summary>('/home').then(setSummary).catch(() => setSummary(null));
    api<Standard[]>('/standards').then(setStandards).catch(() => setStandards([]));
    api<InspectionSummary[]>('/inspections').then(setInspections).catch(() => setInspections([]));
  }, [version]);

  const yardStandards = standards.filter(standard => standard.status === 'ACTIVE' && !standard.id.startsWith('demo-cover'));
  const failed = inspections.filter(item => item.overall_result === 'FAIL' && item.status !== 'APPROVED' && item.status !== 'CANCELLED').length;
  const approved = inspections.filter(item => item.status === 'APPROVED').length;
  const programs = summary?.programs;

  const steps = [
    { page: 'forms', title: 'Read specs', chips: ['KO', 'EN', '\u4E2D'], value: yardStandards.length, unit: 'standards in use', tone: '' },
    { page: 'inspections', title: 'Inspect a lot', chips: null, value: failed, unit: 'failed lots to review', tone: failed ? 'bad' : '' },
    { page: 'inspections', title: 'Send the report', chips: ['EN', 'KO', '\u4E2D'], value: approved, unit: 'approved reports', tone: approved ? 'good' : '' },
  ];

  async function loadDemo() {
    await perform(async () => {
      await api('/demo/shipyard', {});
      onLoaded();
      setVersion(value => value + 1);
    });
  }

  return <div className="home">
    <header className="home-hero">
      <div><small>{new Date().toLocaleDateString(locale, { month: 'long', day: 'numeric', weekday: 'long' })}</small>
        <h1>{t("Hello")}, {userName}</h1></div>
    </header>

    {summary && yardStandards.length === 0 && <div className="demo-banner">
      <strong>{t("No shipyard specs yet")}</strong>
      <button className="primary" disabled={busy} onClick={() => void loadDemo()}><Icon name="upload" />{t("Load the shipyard demo")}</button>
    </div>}

    <section className="home-board">
      {!mapFailed
        ? <figure className="coast-map live">
          <LiveMap onRegion={region => onGo('programs', { region })} onFail={() => setMapFailed(true)} />
          <MapLegend note="Synthetic companies" />
        </figure>
        : <CoastMap />}
      <div className="home-flow">
        <ol className="yard-steps">{steps.map((step, index) => <li key={step.title}>
          <button className="yard-step" onClick={() => onGo(step.page)}>
            <span className="flow-index" aria-hidden="true">{index + 1}</span>
            <span className="flow-text"><strong>{t(step.title)}</strong>{step.chips && <span className="flow-chips">{step.chips.map(chip => <span key={chip}>{chip}</span>)}</span>}</span>
            <span className={`flow-stat ${step.tone}`}><b>{step.value}</b><small>{t(step.unit)}</small></span>
          </button></li>)}</ol>
        <button className="support-card" onClick={() => onGo('programs')}>
          <span className="support-head"><Icon name="grant" size={20} /><strong>{t("Support for shipbuilding crisis areas")}</strong><Icon name="next" size={16} /></span>
          {programs && <span className="support-stats">
            <span className="good"><b>{programs.eligible}</b>{t("can apply")}</span>
            <span className="warn"><b>{programs.check}</b>{t("to check")}</span>
            <span><b>{programs.closing}</b>{t("closing in 7 days")}</span>
          </span>}
        </button>
        {summary && <button className="tasks-line" onClick={() => onGo('tasks')}>
          <Icon name="calendar" size={18} />
          <span>{t("Open tasks")}: <b>{summary.tasks.open}</b></span>
          <span className={summary.tasks.overdue ? 'bad' : ''}>{t("Overdue")}: <b>{summary.tasks.overdue}</b></span>
          <span>{t("Due this week")}: <b>{summary.tasks.today + summary.tasks.week}</b></span>
          <Icon name="next" size={16} />
        </button>}
      </div>
    </section>
  </div>;
}
