import { useEffect, useRef, useState } from 'react';
import { api, apiBlobUrl, inr, newKey, when } from '../api.js';
import { useSession } from '../session.jsx';
import { Empty, ErrorNote, Layout, Loading, StateChip, useFocusHeading } from '../ui.jsx';

function Photo({ photoId, token }) {
  const [url, setUrl] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    let made = null;
    setUrl(null);
    setFailed(false);
    if (photoId) {
      apiBlobUrl(`/photos/${photoId}`, token)
        .then((u) => { made = u; if (alive) setUrl(u); })
        .catch(() => alive && setFailed(true));
    }
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [photoId, token]);
  if (!photoId) return <div className="placeholder">No customer photo for this item</div>;
  if (failed) return <div className="placeholder">The photo couldn't be loaded</div>;
  if (!url) return <Loading label="Loading photo…" />;
  return <img className="photo" src={url} alt="Photo the customer uploaded with the return" />;
}

export default function Finance() {
  const { session } = useSession();
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [last, setLast] = useState(null);           // result of the latest action on the selected item
  const [busy, setBusy] = useState(false);
  const keys = useRef({});                           // one idempotency key per item and approval: double clicks reuse it
  const heading = useFocusHeading(selected);

  const load = async () => {
    setError(null);
    try {
      const q = await api('/finance/queue', { token: session.token });
      setQueue(q);
      if (!selected && q.items.length) setSelected(q.items[0].item_id);
    } catch (err) { setError(err); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const item = (last && last.item_id === selected ? last : null) || queue?.items.find((i) => i.item_id === selected);

  function keyFor(it) {
    const k = `${it.item_id}:${it.refund_keys_used}`;
    if (!keys.current[k]) keys.current[k] = newKey();
    return keys.current[k];
  }

  async function act(path, body) {
    setBusy(true);
    setError(null);
    try {
      const r = await api(`/finance/items/${item.item_id}/${path}`, { method: 'POST', token: session.token, body });
      setLast({ ...item, ...r });
      await load();
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  return (
    <Layout width="wide" menu={<a href="/finance" aria-current="page" style={{ fontWeight: 600, color: 'var(--rr-color-text)', textDecoration: 'none', borderBottom: '2px solid var(--rr-color-primary)', padding: '10px 0' }}>Approvals</a>}>
      <div className="stack" style={{ gap: 4 }}>
        <h1>Refunds waiting for approval</h1>
        <p className="muted">Items whose refund is over ₹10,000 after grading, plus refunds the gateway hasn't settled. Nothing is paid until you approve.</p>
      </div>
      <ErrorNote error={!queue ? error : null} onRetry={load} />
      {!queue && !error && <Loading label="Loading the approval queue…" />}
      {queue && (
        <div className="split">
          <section className="side card tight" aria-labelledby="h-queue">
            <div className="row between">
              <h2 id="h-queue" style={{ fontSize: 16 }} className="muted">Approval queue · {queue.pending_count} pending</h2>
              <button type="button" className="btn plain" onClick={load}>Refresh</button>
            </div>
            {queue.items.length === 0 ? <Empty title="Nothing waiting">New items appear here after the warehouse grades them.</Empty> : (
              <ul className="list">
                {queue.items.map((q) => (
                  <li key={q.item_id}>
                    <button type="button" className="queue-btn" aria-current={q.item_id === selected}
                      onClick={() => { setSelected(q.item_id); setLast(null); setError(null); }}>
                      <span className="row between" style={{ width: '100%' }}>
                        <span style={{ fontWeight: 600 }}>{q.name}</span><span style={{ fontWeight: 600 }}>{inr(q.amount)}</span>
                      </span>
                      <span className="row between small muted" style={{ width: '100%' }}>
                        <span>{q.return_id} · grade {q.grade || '—'}{q.customer_flagged ? ' · flagged' : ''}</span>
                        <StateChip state={q.state} label={q.state_label} />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="detail card" aria-labelledby="h-detail">
            {!item ? <Empty title="Select an item from the queue" /> : (
              <>
                <div className="row between">
                  <h2 id="h-detail" ref={heading} tabIndex={-1} style={{ fontSize: 22 }}>{item.name}</h2>
                  <StateChip state={item.state} label={item.state_label} />
                </div>
                <dl className="facts">
                  <div><dt>Return</dt><dd>{item.return_id}</dd></div>
                  <div><dt>Customer</dt><dd>{item.customer_name || item.customer_id}</dd></div>
                  <div><dt>Paid · grade</dt><dd>{inr(item.price)} · {item.grade || '—'}</dd></div>
                  <div><dt>Refund to</dt><dd>{item.refund_method === 'store_credit' ? 'Store credit' : 'Original payment'}</dd></div>
                  <div><dt>Refund amount</dt><dd style={{ fontSize: 20 }}>{inr(item.amount)}</dd></div>
                  <div><dt>Reason</dt><dd>{item.reason_label || '—'}</dd></div>
                  <div><dt>Requested</dt><dd>{when(item.requested_at)}</dd></div>
                  <div><dt>Idempotency key</dt><dd className="mono">{item.refund_key || (item.state === 'pending_finance' ? keyFor(item) : '—')}</dd></div>
                </dl>
                {item.customer_flagged && (
                  <p className="alert warn small">Flagged customer: {item.customer_returns_30d} returns in the last 30 days. Shown for information; it does not block approval.</p>
                )}
                <Photo photoId={item.photo_id} token={session.token} />
                <ErrorNote error={error} />

                <div aria-live="polite" className="stack">
                  {item.state === 'pending_finance' && (
                    <div className="row">
                      <button type="button" className="btn primary lg" disabled={busy}
                        onClick={() => act('approve', { idempotency_key: keyFor(item) })}>
                        {busy ? 'Paying…' : `Approve and pay ${inr(item.amount)}`}
                      </button>
                      <button type="button" className="btn danger lg" disabled={busy} onClick={() => act('reject')}>Reject refund</button>
                    </div>
                  )}
                  {item.state === 'refund_pending' && (
                    <div className="alert neutral stack" role="status">
                      <strong>The gateway didn't answer in time. The refund stays Refund pending.</strong>
                      <span className="small muted">A retry uses the same key, so it can't pay twice. The reconcile job also settles it automatically.</span>
                      <div><button type="button" className="btn primary" disabled={busy} onClick={() => act('retry')}>{busy ? 'Retrying…' : 'Retry with same key'}</button></div>
                    </div>
                  )}
                  {item.state === 'refund_failed' && (
                    <div className="alert warn stack" role="status">
                      <strong>The gateway declined this refund. Nothing was paid.</strong>
                      {item.refund_keys_used < 2 ? (
                        <>
                          <span className="small">You can try once more; this uses a new key.</span>
                          <div><button type="button" className="btn primary" disabled={busy}
                            onClick={() => act('approve', { idempotency_key: keyFor(item) })}>Try the refund again</button></div>
                        </>
                      ) : <span className="small">The retry was declined too. This needs manual follow-up with the payment provider.</span>}
                    </div>
                  )}
                  {item.state === 'refunded' && (
                    <div className="alert info stack" role="status">
                      <strong>Refunded{item.gateway_ref ? ` · gateway ref ${item.gateway_ref}` : ' · store credit'} · {item.ledger_entries ?? 1} ledger entry</strong>
                      <span className="small">Approving again with the same key returns this result; it never pays twice.</span>
                    </div>
                  )}
                  {item.state === 'rejected' && (
                    <p className="alert warn" role="status">Refund rejected. The item moved to Rejected and the customer can see why.</p>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </Layout>
  );
}
