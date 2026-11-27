import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { Icon } from './icons';
import { useLanguage } from './language';
import type { TaskLink } from './Tasks';

export interface Navigate { page: string; params: Record<string, string> }
interface Card { kind: 'task' | 'standard' | 'trace' | 'request' | 'help'; title?: string; detail?: string; late?: boolean; link?: TaskLink | null }
interface Reply { reply: string; cards: Card[]; navigate: Navigate | null; interpreter: string; notes: string[] }
interface Turn { role: 'user' | 'assistant'; text: string; cards?: Card[]; navigate?: Navigate | null; interpreter?: string; notes?: string[] }

export const examples = [
  'Show my tasks for today',
  'Add a task: write the 8D report by tomorrow',
  'Which process causes cracks?',
  'Tighten the burr height of DEMO-COVER to 0.15',
  'I want to read a customer form',
  'Start a new inspection for DEMO-COVER',
];

const pageLabel: Record<string, string> = {
  home: 'Home', tasks: 'Tasks & schedule', calendar: 'Calendar', automation: 'Automation', inspections: 'Inspection workspace',
  inspection_new: 'New inspection', standards: 'Standards library', standard: 'Standard revision', forms: 'Customer forms',
  trace: 'Root-cause tracing', customers: 'Customers', customer: 'Customers', requests: 'Customer requests', request_new: 'Customer requests', ai: 'AI settings',
};

export default function Assistant({ open, onOpenChange, pending, onPendingUsed, onNavigate, onOpenLink }: {
  open: boolean; onOpenChange: (open: boolean) => void; pending: string | null; onPendingUsed: () => void;
  onNavigate: (target: Navigate) => void; onOpenLink: (link: TaskLink) => void;
}) {
  const { t, language } = useLanguage();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { list.current?.scrollTo({ top: list.current.scrollHeight }); }, [turns, open]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  useEffect(() => {
    if (pending) { onOpenChange(true); void send(pending); onPendingUsed(); }
  }, [pending]);

  async function send(message: string) {
    const value = message.trim();
    if (!value || sending) return;
    const history = turns.slice(-6).map(turn => ({ role: turn.role, text: turn.text }));
    setTurns(current => [...current, { role: 'user', text: value }]);
    setText('');
    setSending(true);
    try {
      const reply = await api<Reply>('/assistant', { message: value, language, history });
      setTurns(current => [...current, { role: 'assistant', text: reply.reply, cards: reply.cards, navigate: reply.navigate, interpreter: reply.interpreter, notes: reply.notes }]);
      if (reply.navigate) onNavigate(reply.navigate);
    } catch (error) {
      setTurns(current => [...current, { role: 'assistant', text: error instanceof Error ? t(error.message) : t('The operation failed.') }]);
    } finally {
      setSending(false);
    }
  }

  return <>
    {!open && <button className="assistant-fab" aria-label={t("Ask the assistant")} onClick={() => onOpenChange(true)}>
      <Icon name="chat" size={26} /><span>{t("Ask AI")}</span></button>}
    {open && <section className="assistant-panel" role="dialog" aria-label={t("Assistant")}>
      <header><div><strong>{t("What would you like to do?")}</strong><small>{t("Say it in your own words. I open the page or do it for you.")}</small></div>
        <button className="icon-button" aria-label={t("Close")} onClick={() => onOpenChange(false)}><Icon name="x" /></button></header>
      <div className="assistant-list" ref={list}>
        {turns.length === 0 && <div className="assistant-examples"><small>{t("Try for example")}</small>
          {examples.map(example => <button key={example} type="button" onClick={() => void send(t(example))}>{t(example)}</button>)}</div>}
        {turns.map((turn, index) => <div key={index} className={`bubble ${turn.role}`}>
          <p>{turn.text}</p>
          {turn.notes?.map(note => <small key={note} className="note">{note}</small>)}
          {turn.cards?.filter(card => card.kind !== 'help').map((card, cardIndex) => <div key={cardIndex} className={`assistant-card ${card.kind} ${card.late ? 'late' : ''}`}>
            <div><strong>{card.title}</strong>{card.detail && <small>{card.detail}</small>}</div>
            {card.link && <button className="small" onClick={() => onOpenLink(card.link!)}><Icon name="next" size={14} />{t("Open item")}</button>}
          </div>)}
          {turn.cards?.some(card => card.kind === 'help') && <div className="assistant-examples inline">{examples.map(example =>
            <button key={example} type="button" onClick={() => void send(t(example))}>{t(example)}</button>)}</div>}
          {turn.navigate && <button className="link-chip" onClick={() => onNavigate(turn.navigate!)}><Icon name="next" size={14} />{t(pageLabel[turn.navigate.page] ?? turn.navigate.page)}</button>}
          {turn.role === 'assistant' && turn.interpreter && <small className="interpreter">{turn.interpreter.startsWith('llm') ? t("Understood by AI") : t("Understood with rules")}</small>}
        </div>)}
        {sending && <div className="bubble assistant typing"><span /><span /><span /></div>}
      </div>
      <form className="assistant-input" onSubmit={event => { event.preventDefault(); void send(text); }}>
        <textarea ref={input} rows={2} maxLength={8000} value={text} placeholder={t("Type a request, or paste a customer mail…")}
          onChange={event => setText(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(text); } }} />
        <button className="primary icon-button" type="submit" aria-label={t("Send")} disabled={sending || !text.trim()}><Icon name="send" /></button>
      </form>
    </section>}
  </>;
}
