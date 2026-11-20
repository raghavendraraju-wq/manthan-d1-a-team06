import { useEffect, useRef, useState } from 'react';
import { api, inr, newKey } from '../api.js';
import { useSession } from '../session.jsx';
import { Empty, ErrorNote, Layout, Loading, StateChip, useFocusHeading } from '../ui.jsx';

const GRADEABLE = ['received', 'inspected', 'pending_finance'];

function outcome(item, grade, decision) {
  if (decision === 'reject') return { text: 'Rejected · no refund for this item', tone: 'orange' };
  const amount = grade === 'C' ? Number(item.price) / 2 : Number(item.price);
  if (amount > 10000) return { text: `Refund ${inr(amount)} · over ₹10,000, goes to finance for approval`, tone: 'orange' };
  return { text: `Refund ${inr(amount)}${grade === 'C' ? ' (grade C, 50%)' : ''} · goes straight to refund`, tone: 'blue' };
}

export default function Agent() {
  const { session } = useSession();
  const [code, setCode] = useState('');
  const [view, setView] = useState(null);
  const [grades, setGrades] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [queue, setQueue] = useState(null);
  const [queueError, setQueueError] = useState(null);
  const keySeed = useRef(newKey());
  const input = useRef(null);
  const heading = useFocusHeading(view?.return_id + String(saved));

  const loadQueue = async () => {
    setQueueError(null);
    try { setQueue(await api('/agent/queue', { token: session.token })); } catch (err) { setQueueError(err); }
  };
  useEffect(() => { loadQueue(); input.current?.focus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function scan(e, value) {
    e?.preventDefault();
    const c = (value ?? code).trim();
    if (!c) return;
    setCode(c);
    setBusy(true);
    setError(null);
    setSaved(false);
    setView(null);
    try {
      const v = await api('/agent/scan', { method: 'POST', token: session.token, body: { code: c } });
      keySeed.current = newKey();      // one key seed per inspection form: a double submit reuses it
      setGrades(Object.fromEntries(v.items.filter((i) => GRADEABLE.includes(i.state))
        .map((i) => [i.item_id, { grade: i.grade || 'A', decision: 'accept' }])));
      setView(v);
      loadQueue();
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const v = await api(`/agent/returns/${view.return_id}/inspect`, {
        method: 'POST', token: session.token,
        body: { key_seed: keySeed.current, items: Object.entries(grades).map(([item_id, g]) => ({ item_id, ...g })) },
      });
      setView(v);
      setSaved(true);
      loadQueue();
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  function reset() {
    setView(null); setSaved(false); setCode(''); setError(null);
    input.current?.focus();
  }

  const gradeable = view ? view.items.filter((i) => grades[i.item_id]) : [];
  const total = gradeable.reduce((sum, i) => {
    const g = grades[i.item_id];
    return g.decision === 'accept' ? sum + (g.grade === 'C' ? Number(i.price) / 2 : Number(i.price)) : sum;
  }, 0);

  return (
    <Layout width="medium" menu={<a href="/agent" aria-current="page" style={{ fontWeight: 600, color: 'var(--rr-color-text)', textDecoration: 'none', borderBottom: '2px solid var(--rr-color-primary)', padding: '10px 0' }}>Scan &amp; grade</a>}>
      <form className="card" onSubmit={scan} aria-labelledby="h-scan">
        <h1 id="h-scan">Scan or enter a return QR</h1>
        <div className="row" style={{ alignItems: 'flex-end' }}>
          <label className="field" style={{ flex: '1 1 240px' }}>Return code
            <input ref={input} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" style={{ fontSize: 18 }}
              placeholder="RTN-20481" aria-describedby="scan-msg" />
          </label>
          <button type="submit" className="btn primary lg" disabled={busy || !code.trim()}>{busy && !view ? 'Looking up…' : 'Look up'}</button>
        </div>
        <p className="small muted">A USB or phone scanner types the code and presses Enter for you.</p>
        <div id="scan-msg"><ErrorNote error={!view ? error : null} /></div>
      </form>

      {view && !saved && (
        <section className="card" aria-labelledby="h-grade">
          <div className="row between">
            <h2 id="h-grade" ref={heading} tabIndex={-1}>{view.return_id} · {view.customer_name} · {view.items.length} item(s)</h2>
            <span className="small muted">
              {view.scanned?.length ? `${view.scanned.length} marked Received on scan` : 'Already received'} · refund to {view.refund_method === 'store_credit' ? 'store credit' : 'original payment'}
            </span>
          </div>
          {view.customer_flagged && (
            <p className="alert neutral small">Flagged customer: {view.customer_returns_30d} returns in 30 days. For information only; it doesn't block the return.</p>
          )}
          {gradeable.length > 0 && <p className="small muted">Tab to a group, then use the arrow keys to change the grade or decision.</p>}
          {view.items.map((item) => {
            const g = grades[item.item_id];
            const o = g && outcome(item, g.grade, g.decision);
            return (
              <div key={item.item_id} className="subcard">
                <div className="row between">
                  <h3>{item.name}</h3>
                  <span className="muted small">Paid {inr(item.price)} · reason: {item.reason_label}</span>
                </div>
                {!g && <p className="small"><StateChip state={item.state} label={item.state_label} /> Not for grading in this state.</p>}
                {g && (
                  <>
                    <div className="row" style={{ gap: 24 }}>
                      <fieldset className="segmented">
                        <legend>Grade</legend>
                        {['A', 'B', 'C'].map((x) => (
                          <label key={x}>
                            <input type="radio" name={`grade-${item.item_id}`} value={x} checked={g.grade === x}
                              onChange={() => setGrades((s) => ({ ...s, [item.item_id]: { ...g, grade: x } }))} />
                            <span>{x}</span>
                          </label>
                        ))}
                      </fieldset>
                      <fieldset className="segmented">
                        <legend>Decision</legend>
                        {[['accept', 'Accept'], ['reject', 'Reject']].map(([v, t]) => (
                          <label key={v}>
                            <input type="radio" className={v} name={`decision-${item.item_id}`} value={v} checked={g.decision === v}
                              onChange={() => setGrades((s) => ({ ...s, [item.item_id]: { ...g, decision: v } }))} />
                            <span>{t}</span>
                          </label>
                        ))}
                      </fieldset>
                    </div>
                    <span aria-live="polite" style={{ fontWeight: 500, color: o.tone === 'orange' ? 'var(--rr-color-warning)' : 'var(--rr-color-primary-strong)' }}>{o.text}</span>
                    {item.photo_id && <p className="small muted">Customer added a photo (visible to finance).</p>}
                  </>
                )}
              </div>
            );
          })}
          <ErrorNote error={error} />
          {gradeable.length > 0 ? (
            <div className="row between">
              <strong>Refund for accepted items: {inr(total)}</strong>
              <button type="button" className="btn primary lg" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save inspection'}</button>
            </div>
          ) : (
            <div className="row end"><button type="button" className="btn plain" onClick={reset}>Scan next return</button></div>
          )}
        </section>
      )}

      {view && saved && (
        <section className="card" aria-labelledby="h-saved">
          <h2 id="h-saved" ref={heading} tabIndex={-1} role="status">Inspection saved</h2>
          {view.items.map((item) => {
            const r = view.results?.find((x) => x.item_id === item.item_id);
            return (
              <div key={item.item_id} className="row between" style={{ borderBottom: '1px solid var(--rr-color-border)', paddingBottom: 10 }}>
                <span style={{ fontWeight: 600 }}>{item.name}{item.amount && item.state !== 'rejected' ? ` · ${inr(item.amount)}` : ''}</span>
                <span className="row"><StateChip state={item.state} label={item.state_label} />
                  {r?.message && <span className="small" style={{ color: 'var(--rr-color-warning)' }}>{r.message}</span>}</span>
              </div>
            );
          })}
          <p className="small muted">Each state change was written to the audit log. Re-grading is allowed until the refund starts.</p>
          <div className="row">
            <button type="button" className="btn plain" onClick={reset}>Scan next return</button>
            <button type="button" className="btn secondary" onClick={() => scan(null, view.return_id)}>Re-grade this return</button>
          </div>
        </section>
      )}

      <section className="card" aria-labelledby="h-queue">
        <div className="row between">
          <h2 id="h-queue">Warehouse queue</h2>
          <button type="button" className="btn plain" onClick={loadQueue}>Refresh</button>
        </div>
        <ErrorNote error={queueError} onRetry={loadQueue} />
        {!queue && !queueError && <Loading label="Loading the queue…" />}
        {queue && (
          <div className="grid-panels">
            {[['to_grade', 'Received, waiting for grading'], ['in_transit', 'In transit to the warehouse'],
              ['awaiting_pickup', 'Approved, waiting for the courier']].map(([k, title]) => (
              <div key={k} className="stack">
                <h3>{title} ({queue[k].length})</h3>
                {queue[k].length === 0 ? <Empty title="Nothing here" /> : (
                  <ul className="list">
                    {queue[k].slice(0, 8).map((r) => (
                      <li key={r.return_id}>
                        <button type="button" className="queue-btn" onClick={() => scan(null, r.return_id)}>
                          <span style={{ fontWeight: 600 }}>{r.return_id}</span>
                          <span className="small muted">{r.items} item(s) · {Object.entries(r.states).map(([s, n]) => `${n} ${s}`).join(', ')}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </Layout>
  );
}
