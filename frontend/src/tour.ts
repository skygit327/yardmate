/**
 * Auto demo: plays the whole YardMate walkthrough on the live page with a pointer, highlights
 * and typing, so a presenter can screen-record it or talk over it. It drives the real UI, so the
 * data really changes (a draft revision, an inspection, tasks). Press Esc to stop.
 */
import { api } from './api';
import { translate } from './translations';
import type { Language } from './translations';

type Program = { id: string; source: string; ai?: unknown; text?: string; notice: { title: string } };

// Minimum length of each scene in seconds, sized so the whole run takes about four minutes.
const SCENES: Record<string, number> = {
  h1: 9.4, h2: 7.9, h3: 8.7, h4: 7.4, h5: 4.5,
  f1: 9.2, f2: 7.1, f3: 10.7, f4: 7.7,
  s1: 8.8, s2: 10.6, s3: 4.4, s4: 5.1,
  i1: 7.2, i2: 7.6, i3: 8.4, i4: 7.7, i5: 7.6,
  p1: 9.5, p2: 6.8, p3: 7.9, p4: 10.8, p5: 5.8,
  t1: 8.4, t2: 4.1, t3: 8.2,
  a1: 5.3, a2: 7.7, a3: 6.5, a4: 5.8,
};
const ASSISTANT_PROMPT: Record<string, string> = {
  ko: '내일까지 감독관 회신 할 일 추가해줘',
  en: 'Add a task: reply to the surveyor by tomorrow',
  zh: '添加任务 明天前回复监造',
};
const MEASUREMENTS = ['0.62', '0.5', '2.1', '7.0', '0.8', '1.0'];
const LOT = 'E21-B10';

class Stopped extends Error {}

let running = false;

export function tourRunning() { return running; }

export async function startTour() {
  if (running) return;
  running = true;
  let stopped = false;
  const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') stopped = true; };
  window.addEventListener('keydown', onKey, true);

  const layer = document.createElement('div');
  layer.className = 'tour-layer';
  layer.innerHTML = '<svg class="tour-cursor" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 2l7.5 19 2.6-7.6L21 10.8z"/></svg>'
    + '<div class="tour-ring"></div><div class="tour-highlight"></div><div class="tour-pdf"></div>';
  document.body.appendChild(layer);
  const cursor = layer.querySelector<SVGElement>('.tour-cursor')!;
  const ring = layer.querySelector<HTMLElement>('.tour-ring')!;
  const highlight = layer.querySelector<HTMLElement>('.tour-highlight')!;
  const pdf = layer.querySelector<HTMLElement>('.tour-pdf')!;
  let x = innerWidth / 2, y = innerHeight / 2;
  cursor.style.transform = `translate(${x}px, ${y}px)`;

  const wait = async (ms: number) => {
    const end = performance.now() + ms;
    while (performance.now() < end) {
      if (stopped) throw new Stopped();
      await new Promise(resolve => setTimeout(resolve, Math.min(100, end - performance.now())));
    }
  };
  const visible = (element: Element | null): element is HTMLElement => {
    if (!element) return false;
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const find = <T extends HTMLElement = HTMLElement>(selector: string, test?: (element: T) => boolean) =>
    [...document.querySelectorAll<T>(selector)].find(element => visible(element) && (!test || test(element))) ?? null;
  const waitFor = async <T extends HTMLElement = HTMLElement>(selector: string, test?: (element: T) => boolean, timeout = 30000) => {
    const end = performance.now() + timeout;
    for (;;) {
      const element = find<T>(selector, test);
      if (element) return element;
      if (performance.now() > end) return null;
      await wait(150);
    }
  };
  const busy = () => Boolean(document.querySelector('main .busy'));
  const settle = async (timeout = 30000) => {
    const end = performance.now() + timeout;
    await wait(250);
    while (busy() && performance.now() < end) await wait(150);
  };
  const reveal = async (element: HTMLElement) => {
    const box = element.getBoundingClientRect();
    if (box.top < 90 || box.bottom > innerHeight - 60) {
      element.scrollIntoView({ block: 'center', behavior: 'smooth' });
      await wait(700);
    }
    return element.getBoundingClientRect();
  };
  const move = async (element: HTMLElement) => {
    const box = await reveal(element);
    x = box.left + box.width / 2; y = box.top + box.height / 2;
    cursor.style.transform = `translate(${x - 4}px, ${y - 3}px)`;
    await wait(600);
    return box;
  };
  const press = () => {
    ring.style.left = `${x}px`; ring.style.top = `${y}px`;
    ring.classList.remove('go'); void ring.offsetWidth; ring.classList.add('go');
  };
  const click = async (element: HTMLElement | null, after = 500) => {
    if (!element) return;
    await move(element); press(); element.click(); await wait(after);
  };
  const point = async (element: HTMLElement | null, hold = 1000, pad = 8) => {
    if (!element) return;
    const box = await move(element);
    Object.assign(highlight.style, {
      left: `${box.left - pad}px`, top: `${box.top - pad}px`, width: `${box.width + pad * 2}px`, height: `${box.height + pad * 2}px`, opacity: '1',
    });
    await wait(hold);
  };
  const unpoint = () => { highlight.style.opacity = '0'; };
  const setValue = (element: HTMLInputElement | HTMLTextAreaElement, value: string) => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const type = async (element: HTMLInputElement | HTMLTextAreaElement | null, text: string, delay = 80) => {
    if (!element) return;
    await click(element, 150);
    element.focus();
    setValue(element, '');
    for (let index = 1; index <= text.length; index++) { setValue(element, text.slice(0, index)); await wait(delay); }
  };
  const nav = (tab: string) => find(`header.topbar [data-tour="nav-${tab}"]`);
  const scene = async (id: string, run: () => Promise<void>) => {
    const end = performance.now() + (SCENES[id] ?? 6) * 1000;
    try { await run(); } catch (error) { if (error instanceof Stopped) throw error; console.warn('Auto demo scene skipped', id, error); }
    unpoint();
    const rest = end - performance.now();
    if (rest > 0) await wait(rest);
  };
  const showReport = async (label: string) => {
    const link = find<HTMLAnchorElement>('.report-box a[href*="reports/pdf"]');
    if (!link) return;
    await move(link); press();
    const blob = await fetch(link.href, { credentials: 'same-origin' }).then(response => response.blob());
    const url = `${URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))}#toolbar=0&navpanes=0&view=FitH`;
    const figure = document.createElement('figure');
    const frame = document.createElement('iframe');
    frame.src = url; frame.title = label;
    const caption = document.createElement('figcaption');
    caption.textContent = label;
    figure.append(frame, caption);
    pdf.append(figure);
    pdf.classList.add('on');
  };

  const language = document.documentElement.lang || 'en';
  const top = () => document.querySelector('header.topbar');
  try {
    window.scrollTo({ top: 0 });
    await click(nav('home'), 1200);

    await scene('h1', async () => { await point(find('.marquee'), 2600, 4); });
    await scene('h2', async () => {
      for (const pin of [...document.querySelectorAll<HTMLElement>('.map-pin-label')].filter(visible).slice(0, 3)) await point(pin, 1000);
    });
    await scene('h3', async () => {
      await point(document.querySelectorAll<HTMLElement>('.yard-step')[1] ?? null, 1800);
      await point(find('.support-card'), 1800);
    });
    await scene('h4', async () => {
      const order = ['en', 'zh', 'ko'].filter(code => code !== language).concat(language);
      for (const code of order) await click(top()?.querySelector<HTMLElement>(`.language-toggle [lang="${code}"]`) ?? null, 1300);
    });
    await scene('h5', async () => {
      await click(top()?.querySelector<HTMLElement>('.theme-toggle') ?? null, 1400);
      await click(top()?.querySelector<HTMLElement>('.theme-toggle') ?? null, 500);
    });

    await scene('f1', async () => { await click(nav('forms'), 1000); await point(await waitFor('.intake-drop'), 1000); });
    await scene('f2', async () => { await point(find('.lock-note'), 1500); });
    await scene('f3', async () => {
      await click(await waitFor<HTMLElement>('.sample-item', element => /Rev D/.test(element.dataset.sampleEn ?? '')), 300);
      await settle();
      await point(await waitFor('.intake-warnings', undefined, 4000), 1500);
      unpoint();
      await point(find<HTMLElement>('.rule-chip', element => /0\.4/.test(element.textContent ?? '')), 1500);
    });
    await scene('f4', async () => {
      await click(await waitFor('[data-tour="save-draft"]'), 300);
      await settle();
      await point(await waitFor('.success-banner', undefined, 15000), 1200);
    });

    await scene('s1', async () => {
      await click(nav('standards'), 1000);
      const card = await waitFor('.standard-card', element => Boolean(element.querySelector('.badge.draft')));
      await click(card?.querySelector<HTMLElement>('.card-actions button.primary') ?? null, 1500);
    });
    await scene('s2', async () => {
      for (const row of [...document.querySelectorAll<HTMLElement>('.diff-row.changed, .diff-row.added, .diff-row.removed')].slice(0, 3)) await point(row, 1500, 4);
    });
    await scene('s3', async () => { await click(find('.version-detail button.primary.wide'), 300); await settle(); });
    await scene('s4', async () => {
      await click(find('.version-detail .doc-language [lang="zh"]'), 1700);
      await click(find(`.version-detail .doc-language [lang="${language}"]`), 500);
    });

    await scene('i1', async () => {
      await click(top()?.querySelector<HTMLElement>('.sidebar-cta') ?? null, 1200);
      const choice = await waitFor<HTMLElement>('.choice-grid .choice', element => element.dataset.doc === 'HB-WLD-201')
        ?? find('.choice-grid .choice');
      await click(choice, 1200);
    });
    await scene('i2', async () => {
      await type(find<HTMLInputElement>('form .form-grid input'), LOT, 70);
      const inputs = [...document.querySelectorAll<HTMLInputElement>('.rule-card input[placeholder="0.00"]')];
      await type(inputs[0] ?? null, MEASUREMENTS[0], 130);
      inputs.slice(1).forEach((input, index) => setValue(input, MEASUREMENTS[index + 1] ?? '0.1'));
      await wait(200);
      const clean = [...document.querySelectorAll<HTMLElement>('.rule-card .segmented button.good')];
      for (const button of clean) { button.click(); await wait(150); }
      if (clean.length) await move(clean[clean.length - 1]);
    });
    await scene('i3', async () => {
      await click(find('form .action-bar button[type="submit"]'), 300);
      await settle();
      window.scrollTo({ top: 0 });
      await point(await waitFor('.result-row.fail .explain', undefined, 15000), 2000);
    });
    await scene('i4', async () => {
      const primary = () => find('.panel .action-bar button.primary.big');
      await click(await waitFor('.panel .action-bar button.primary.big'), 300);
      await settle();
      await click(await waitFor('.panel .action-bar button.primary.big', undefined, 15000) ?? primary(), 300);
      await settle();
      await point(await waitFor('.report-box small.muted', undefined, 15000), 1200);
    });
    await scene('i5', async () => {
      await click(find('.report-box .doc-language [lang="en"]'), 500);
      await showReport(translate('English report (PDF)', language as Language));
      await wait(1800);
      pdf.classList.remove('on'); await wait(350);
      await click(find('.report-box .doc-language [lang="zh"]'), 500);
      await showReport(translate('Chinese report (PDF)', language as Language));
      await wait(2600);
    });
    pdf.classList.remove('on'); await wait(300); pdf.innerHTML = '';

    const programs = await api<Program[]>('/programs').catch(() => [] as Program[]);
    const pick = programs.find(item => item.ai && item.source !== 'sample')
      ?? programs.find(item => /조선|선박/.test(item.notice?.title ?? ''))
      ?? programs[0];
    const title = pick?.notice?.title ?? '';
    const keyword = (title.match(/[가-힣A-Za-z]{2,}/g) ?? []).sort((a, b) => b.length - a.length)[0] ?? '';
    await scene('p1', async () => {
      await click(nav('programs'), 800);
      await point(await waitFor('.program-row'), 1500);
    });
    await scene('p2', async () => { await type(find<HTMLInputElement>('.program-search input[type="search"]'), keyword, 160); await wait(900); });
    await scene('p3', async () => {
      const row = find<HTMLElement>('.program-row', element => (element.textContent ?? '').includes(title)) ?? find('.program-row');
      await click(row, 300);
      await settle();
      await point(await waitFor('.program-verdict'), 1500);
    });
    await scene('p4', async () => {
      await point(find('.original-strip'), 1600);
      await point(find('.ai-panel'), 2400);
    });
    await scene('p5', async () => {
      const register = find<HTMLButtonElement>('.condition-block .card-actions button.primary', element => !element.disabled);
      if (register) { await click(register, 300); await settle(); }
      await point(await waitFor('.condition-block .badge.pass', undefined, 8000), 1000);
    });

    await scene('t1', async () => {
      await click(nav('tasks'), 1200);
      await point(await waitFor('.task-column'), 1700);
    });
    await scene('t2', async () => { await click(find('.tabs [role="tab"]:nth-child(2)'), 1800); });
    await scene('t3', async () => {
      await click(find('.tabs [role="tab"]:nth-child(3)'), 1300);
      await point(await waitFor('main .panel', undefined, 5000), 2000);
    });

    await scene('a1', async () => {
      await click(find('.assistant-fab'), 900);
      const box = await waitFor<HTMLTextAreaElement>('.assistant-panel textarea');
      await type(box, ASSISTANT_PROMPT[language] ?? ASSISTANT_PROMPT.en, 60);
      const send = find('.assistant-panel button[type="submit"]');
      if (send) { await move(send); press(); send.click(); }
      await wait(2500);
    });
    await scene('a2', async () => {
      await click(find('.assistant-panel header button'), 400);
      await click(top()?.querySelector<HTMLElement>('.sidebar-note') ?? null, 1200);
      for (const option of [...document.querySelectorAll<HTMLElement>('main .choice-grid button')].filter(visible)) await point(option, 800);
    });
    await scene('a3', async () => {
      await click(top()?.querySelector<HTMLElement>('.user-name') ?? null, 1300);
      await point(document.querySelectorAll<HTMLElement>('.account-card')[1] ?? null, 1200);
    });
    await scene('a4', async () => { await click(nav('home'), 1200); });
    await wait(1000);
  } catch (error) {
    if (!(error instanceof Stopped)) console.warn('Auto demo stopped', error);
  } finally {
    window.removeEventListener('keydown', onKey, true);
    layer.remove();
    running = false;
  }
}
