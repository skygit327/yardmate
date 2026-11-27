import { useEffect, useRef, useState, type ReactNode } from 'react';
import { chinese, korean } from './translations';

// Small, dependency-free motion helpers. Everything respects prefers-reduced-motion.

// Buttons keep the size they have in Korean; in English or Chinese the label shrinks to fit.
const FIXED = [
  '.fit', '.topbar nav button', '.top-actions > button', '.status-tabs button', '.page-header button',
  '.tabs button', '.chip-button', '.card-actions button', '.program-verdict button', '.original-actions button',
  '.original-actions .button', '.segmented button', '.quick button', '.search-line > button', '.panel-top > button',
  '.task-actions button', '.report-box button', '.demo-banner button', '.assistant-fab',
].join(', ');
const toKorean = new Map<string, string>();
for (const [english, text] of Object.entries(korean)) toKorean.set(english, text);
for (const [english, text] of Object.entries(chinese)) if (korean[english]) toKorean.set(text, korean[english]);
let canvas: CanvasRenderingContext2D | null = null;

function textWidth(text: string, element: Element) {
  canvas ??= document.createElement('canvas').getContext('2d');
  if (!canvas) return 0;
  const style = getComputedStyle(element);
  canvas.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  return canvas.measureText(text).width;
}

function koreanText(text: string) {
  const trimmed = text.trim();
  return trimmed ? text.replace(trimmed, toKorean.get(trimmed) ?? trimmed) : text;
}

// These have a fixed width in CSS (measured in Korean); only their label shrinks.
const CSS_FIXED = '.topbar nav button, .top-actions > .sidebar-cta, .top-actions > .sidebar-note, .status-tabs button';

function fitToKorean(element: HTMLElement) {
  element.style.removeProperty('width');
  element.style.removeProperty('font-size');
  if (!element.offsetParent) return;
  const korean = document.documentElement.lang === 'ko';
  const parent = element.parentElement ? getComputedStyle(element.parentElement).display : '';
  const lockable = !korean && !parent.includes('grid') && !element.matches(CSS_FIXED);
  if (lockable) {
    let current = 0, target = 0;
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const host = node.parentElement;
      if (!host || !node.textContent?.trim() || host.closest('.sr-only')) continue;
      current += textWidth(node.textContent, host);
      target += textWidth(koreanText(node.textContent), host);
    }
    if (current > 0 && Math.abs(current - target) > 1) {
      const width = element.getBoundingClientRect().width - current + target;
      element.style.width = `${width.toFixed(1)}px`;
      if (current > target) {
        const base = parseFloat(getComputedStyle(element).fontSize);
        element.style.fontSize = `${Math.max(base * 0.78, base * target / current).toFixed(2)}px`;
      }
    }
  }
  if (element.scrollWidth > element.clientWidth + 0.5) {
    const base = parseFloat(getComputedStyle(element).fontSize);
    element.style.fontSize = `${Math.max(base * 0.72, base * element.clientWidth / element.scrollWidth).toFixed(2)}px`;
  }
}

export function prefersReducedMotion() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Number that counts up from 0 (or its previous value) to `value`. */
export function CountUp({ value, duration = 700 }: { value: number; duration?: number }) {
  const [shown, setShown] = useState(value);
  const previous = useRef(0);
  useEffect(() => {
    const from = previous.current;
    previous.current = value;
    if (prefersReducedMotion() || from === value) { setShown(value); return; }
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setShown(Math.round(from + (value - from) * eased));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, duration]);
  return <>{shown.toLocaleString()}</>;
}

/** Text that types itself out once; screen readers get the full text at once. */
export function Typewriter({ text, speed = 18, onDone }: { text: string; speed?: number; onDone?: () => void }) {
  const [count, setCount] = useState(() => (prefersReducedMotion() ? text.length : 0));
  useEffect(() => {
    if (count >= text.length) { onDone?.(); return; }
    const step = Math.max(1, Math.ceil(text.length / 120));
    const timer = window.setTimeout(() => setCount(value => Math.min(text.length, value + step)), speed);
    return () => window.clearTimeout(timer);
  }, [count, text, speed, onDone]);
  return <>
    <span aria-hidden="true">{text.slice(0, count)}{count < text.length && <span className="type-caret" />}</span>
    <span className="sr-only">{text}</span>
  </>;
}

/** Heading whose words rise out of a mask. */
export function MaskReveal({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <span className={`mask-reveal ${className}`}><span>{children}</span></span>;
}

/** Endless horizontal ticker. Content is rendered twice so the loop is seamless. */
export function Marquee({ children, label, seconds = 40 }: { children: ReactNode; label: string; seconds?: number }) {
  return <div className="marquee" role="region" aria-label={label}>
    <div className="marquee-track" style={{ animationDuration: `${seconds}s` }}>
      <div className="marquee-group">{children}</div>
      <div className="marquee-group" aria-hidden="true">{children}</div>
    </div>
  </div>;
}

/**
 * Page-wide behaviour, installed once:
 * - scroll reveal: elements with `.reveal` fade up when they enter the viewport
 * - magnetic buttons: elements with `data-magnetic` lean toward the pointer
 * - parallax: elements with `data-parallax="0.2"` drift with the scroll position
 * - text fitting: buttons with a fixed width shrink their label instead of growing
 */
export function useMotion(deps: unknown[] = []) {
  useEffect(() => {
    const root = document.documentElement;
    const reduced = prefersReducedMotion();
    root.classList.add('motion-ready');
    if (reduced) root.classList.add('motion-reduced');

    const observer = typeof IntersectionObserver === 'function'
      ? new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (entry.isIntersecting) { entry.target.classList.add('is-in'); observer?.unobserve(entry.target); }
        }
      }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 })
      : null;
    const watch = () => {
      document.querySelectorAll('.reveal:not([data-watched]), .condition-block:not([data-watched]), .standard-card:not([data-watched]), .card-block:not([data-watched]), .task-column:not([data-watched])').forEach(element => {
        element.setAttribute('data-watched', '');
        if (observer && !reduced) observer.observe(element); else element.classList.add('is-in');
      });
    };

    let fitFrame = 0;
    const fit = () => {
      cancelAnimationFrame(fitFrame);
      fitFrame = requestAnimationFrame(() => {
        document.querySelectorAll<HTMLElement>(FIXED).forEach(fitToKorean);
      });
    };

    const mutations = new MutationObserver(() => { watch(); fit(); });
    mutations.observe(document.body, { childList: true, subtree: true, characterData: true });
    watch(); fit();
    window.addEventListener('resize', fit);

    let pull: HTMLElement | null = null;
    const onMove = (event: PointerEvent) => {
      if (reduced || event.pointerType !== 'mouse') return;
      const target = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-magnetic]') ?? null;
      if (pull && pull !== target) { pull.style.removeProperty('--mx'); pull.style.removeProperty('--my'); }
      pull = target;
      if (!target) return;
      const box = target.getBoundingClientRect();
      const x = (event.clientX - (box.left + box.width / 2)) / (box.width / 2);
      const y = (event.clientY - (box.top + box.height / 2)) / (box.height / 2);
      target.style.setProperty('--mx', `${(x * 6).toFixed(1)}px`);
      target.style.setProperty('--my', `${(y * 4).toFixed(1)}px`);
    };
    const onLeave = () => { if (pull) { pull.style.removeProperty('--mx'); pull.style.removeProperty('--my'); pull = null; } };
    document.addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('pointerleave', onLeave);

    let scrollFrame = 0;
    const onScroll = () => {
      cancelAnimationFrame(scrollFrame);
      scrollFrame = requestAnimationFrame(() => {
        if (reduced) return;
        const y = window.scrollY;
        document.querySelectorAll<HTMLElement>('[data-parallax]').forEach(element => {
          element.style.setProperty('--parallax', `${(y * Number(element.dataset.parallax || 0.2)).toFixed(1)}px`);
        });
        root.classList.toggle('scrolled', y > 8);
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();

    return () => {
      observer?.disconnect(); mutations.disconnect();
      window.removeEventListener('resize', fit);
      window.removeEventListener('scroll', onScroll);
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerleave', onLeave);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
