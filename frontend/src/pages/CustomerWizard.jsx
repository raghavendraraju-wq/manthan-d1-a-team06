import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { api, day, inr } from '../api.js';
import { useSession } from '../session.jsx';
import { ErrorNote, Layout, Loading, useFocusHeading } from '../ui.jsx';
import ReturnStatus from './ReturnStatus.jsx';

const STEPS = ['Find order', 'Verify code', 'Items', 'Reason & photo', 'Refund method', 'Review', 'Label & status'];
const REASONS = [
  ['size', 'Wrong size or fit'], ['damaged', 'Arrived damaged'],
  ['not-described', 'Not as described'], ['changed-mind', 'Changed my mind'],
];
const METHODS = [
  ['original_payment', 'Original payment', 'Back to the card or UPI you paid with.'],
  ['store_credit', 'Store credit', 'Added to your ReturnRight account.'],
];
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function mask(email) {
  const [user, domain] = email.split('@');
  return `${user[0]}${'•'.repeat(Math.max(1, user.length - 1))}@${domain}`;
}

export default function CustomerWizard() {
  const { session, setSession } = useSession();
  const [step, setStep] = useState(session?.role === 'customer' ? 3 : 1);
  const [orderNo, setOrderNo] = useState(session?.order_no || '');
  const [email, setEmail] = useState('');
  const [tried, setTried] = useState(false);
  const [code, setCode] = useState('');
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [order, setOrder] = useState(null);
  const [selected, setSelected] = useState({});
  const [reasons, setReasons] = useState({});
  const [photos, setPhotos] = useState({});
  const [method, setMethod] = useState('original_payment');
  const [result, setResult] = useState(null);
  const [demo, setDemo] = useState(null);
  const heading = useFocusHeading(step);

  useEffect(() => { api('/demo/info').then((d) => d.demo && setDemo(d)).catch(() => {}); }, []);

  const token = session?.role === 'customer' ? session.token : null;
  useEffect(() => {
    if (step === 3 && token && !order) loadOrder();
  }, [step, token]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(fn) {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (err) { setError(err); } finally { setBusy(false); }
  }

  const loadOrder = () => run(async () => {
    setOrder(await api(`/orders/${encodeURIComponent(session.order_no)}`, { token: session.token }));
  });

  const orderErr = tried && !orderNo.trim();
  const emailErr = tried && !EMAIL_RE.test(email.trim());

  const findOrder = (e) => {
    e.preventDefault();
    setTried(true);
    if (!orderNo.trim() || !EMAIL_RE.test(email.trim())) return;
    run(async () => {
      await api('/auth/otp/request', { method: 'POST', body: { order_no: orderNo.trim(), email: email.trim() } });
      setTried(false);
      setCode('');
      setNotice(null);
      setStep(2);
    });
  };

  const resend = () => run(async () => {
    await api('/auth/otp/request', { method: 'POST', body: { order_no: orderNo.trim(), email: email.trim() } });
    setCode('');
    setNotice('A new code is on its way. Earlier codes no longer work.');
  });

  const verify = (e) => {
    e.preventDefault();
    if (code.length !== 6) { setError(new Error('Enter the 6-digit code from the email.')); return; }
    run(async () => {
      const r = await api('/auth/otp/verify', {
        method: 'POST', body: { order_no: orderNo.trim(), email: email.trim(), code } });
      setSession({ token: r.token, role: 'customer', order_no: r.order_no, name: '' });
      setOrder(await api(`/orders/${encodeURIComponent(r.order_no)}`, { token: r.token }));
      setStep(3);
    });
  };

  const chosen = (order?.lines || []).filter((l) => l.eligibility.eligible && selected[l.line_no]);

  async function addPhoto(lineNo, file) {
    if (!file) return;
    setPhotos((p) => ({ ...p, [lineNo]: { busy: true, name: file.name } }));
    try {
      const form = new FormData();
      form.append('file', file);
      const r = await api('/photos', { method: 'POST', form, token: session.token });
      setPhotos((p) => ({ ...p, [lineNo]: { id: r.photo_id, name: file.name } }));
    } catch (err) {
      setPhotos((p) => ({ ...p, [lineNo]: { error: err.message } }));
    }
  }

  const [reasonTried, setReasonTried] = useState(false);
  const reasonsNext = () => {
    if (chosen.some((c) => !reasons[c.line_no])) { setReasonTried(true); return; }
    setReasonTried(false);
    setStep(5);
  };

  const submit = () => run(async () => {
    const r = await api('/returns', {
      method: 'POST', token: session.token,
      body: {
        order_no: order.order_no, refund_method: method,
        items: chosen.map((c) => ({ line_no: c.line_no, reason: reasons[c.line_no], photo_id: photos[c.line_no]?.id || null })),
      },
    });
    setResult(r);
    setStep(7);
  });

  const restart = () => {
    setSelected({}); setReasons({}); setPhotos({}); setMethod('original_payment'); setResult(null); setOrder(null);
    setStep(3);
  };

  return (
    <Layout width="narrow" menu={<span className="muted">Return an item</span>}>
      <nav aria-label="Return steps" className="no-print">
        <ol className="stepper">
          {STEPS.map((t, i) => (
            <li key={t} className={i + 1 === step ? 'current' : i + 1 < step ? 'done' : ''}
              aria-current={i + 1 === step ? 'step' : undefined}>
              {i + 1}. {t}<span className="sr-only">{i + 1 === step ? ' (current step)' : i + 1 < step ? ' (done)' : ''}</span>
            </li>
          ))}
        </ol>
      </nav>
      <p role="status" aria-live="polite" className="sr-only">Step {step} of 7: {STEPS[step - 1]}</p>

      {step === 1 && (
        <form className="card" onSubmit={findOrder} noValidate aria-labelledby="h-step">
          <h1 id="h-step" ref={heading} tabIndex={-1}>Find your order</h1>
          <p className="muted">Enter the order number from your confirmation email and the email you ordered with. Both fields are required.</p>
          <label className="field">Order number
            <input value={orderNo} onChange={(e) => setOrderNo(e.target.value)} autoComplete="off" required
              aria-invalid={orderErr} aria-describedby="err-order" placeholder="RR-10482" />
            {orderErr && <span id="err-order" className="err">Enter your order number, for example RR-10482.</span>}
          </label>
          <label className="field">Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required
              aria-invalid={emailErr} aria-describedby="err-email" />
            {emailErr && <span id="err-email" className="err">Enter the email you ordered with, like name@example.com.</span>}
          </label>
          <ErrorNote error={error} />
          {demo && <p className="small muted">Demo order: {demo.orders[0].order_no} · {demo.orders[0].email}</p>}
          <div className="row end">
            <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Sending…' : 'Find order and send code'}</button>
          </div>
        </form>
      )}

      {step === 2 && (
        <form className="card" onSubmit={verify} noValidate aria-labelledby="h-step">
          <h1 id="h-step" ref={heading} tabIndex={-1}>Check your email</h1>
          <p id="otp-help" className="muted">
            If {orderNo.trim().toUpperCase()} matches that email, we've sent a 6-digit code to {mask(email.trim())}. Enter it to see your order.
            The code works once and expires in 10 minutes.
          </p>
          <label className="field">One-time code
            <input className="otp" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required value={code}
              onChange={(e) => { setCode(e.target.value.replace(/\D/g, '').slice(0, 6)); setError(null); }}
              aria-invalid={!!error} aria-describedby="otp-help otp-msg" />
          </label>
          <div id="otp-msg">
            <ErrorNote error={error} />
            {notice && !error && <p role="status" className="small" style={{ color: 'var(--rr-color-primary-strong)' }}>{notice}</p>}
          </div>
          {demo && <p className="small muted">Demo: read the code in the mailbox at <a href={demo.mailbox} target="_blank" rel="noreferrer">{demo.mailbox}</a>.</p>}
          <div className="row between">
            <button type="button" className="btn secondary" onClick={resend} disabled={busy}>Send a new code</button>
            <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Checking…' : 'Verify'}</button>
          </div>
          <button type="button" className="btn plain" onClick={() => { setStep(1); setError(null); }}>Back</button>
        </form>
      )}

      {step === 3 && (
        <section className="card" aria-labelledby="h-step">
          <h1 id="h-step" ref={heading} tabIndex={-1}>Which items are you returning?</h1>
          {!order && busy && <Loading label="Loading your order…" />}
          <ErrorNote error={error} onRetry={!order ? loadOrder : undefined} />
          {order && (
            <>
              <p className="muted">Order {order.order_no} · {order.delivered_at ? `delivered ${day(order.delivered_at)}` : 'not delivered yet'}.
                Each item shows whether it can be returned and why.</p>
              {order.lines.map((line) => {
                const ok = line.eligibility.eligible;
                const sel = !!selected[line.line_no];
                return (
                  <div key={line.line_no} className={`item-card ${!ok ? 'disabled' : sel ? 'selected' : ''}`}>
                    <div className="info">
                      <span style={{ fontWeight: 600, fontSize: 17 }}>{line.name}</span>
                      <span className="small muted">{line.category_label} · {inr(line.price)}</span>
                      <span className={ok ? 'why-ok' : 'why-no'}>{line.eligibility.reason}</span>
                      {line.return_id && <Link className="small" to={`/returns/${line.return_id}`}>View return {line.return_id}</Link>}
                    </div>
                    {ok && (
                      <button type="button" className={`btn ${sel ? 'primary' : 'secondary'}`} aria-pressed={sel}
                        aria-label={`${sel ? 'Selected: ' : 'Select '}${line.name}`}
                        onClick={() => setSelected((s) => ({ ...s, [line.line_no]: !sel }))}>
                        {sel ? 'Selected' : 'Select'}
                      </button>
                    )}
                  </div>
                );
              })}
              {order.lines.every((l) => !l.eligibility.eligible) && (
                <p className="alert neutral">Nothing on this order can be returned right now. The reason is shown under each item.</p>
              )}
              {order.returns.length > 0 && (
                <p className="small">Earlier returns on this order: {order.returns.map((r, i) => (
                  <span key={r}>{i > 0 && ', '}<Link to={`/returns/${r}`}>{r}</Link></span>))}</p>
              )}
              {chosen.length === 0 && <p className="small muted">Select at least one item to continue.</p>}
              <div className="row end">
                <button type="button" className="btn primary" disabled={chosen.length === 0} onClick={() => setStep(4)}>
                  Continue with {chosen.length} item(s)
                </button>
              </div>
            </>
          )}
        </section>
      )}

      {step === 4 && (
        <section className="card" aria-labelledby="h-step">
          <h1 id="h-step" ref={heading} tabIndex={-1}>Tell us why, and add a photo</h1>
          {reasonTried && chosen.some((c) => !reasons[c.line_no]) && (
            <p className="alert warn" role="alert">Choose a reason for each item before you continue.</p>
          )}
          {chosen.map((c) => {
            const bad = reasonTried && !reasons[c.line_no];
            const ph = photos[c.line_no];
            return (
              <div key={c.line_no} className="subcard">
                <strong>{c.name}</strong>
                <label className="field">Reason (required)
                  <select value={reasons[c.line_no] || ''} required aria-invalid={bad}
                    onChange={(e) => setReasons((r) => ({ ...r, [c.line_no]: e.target.value }))}>
                    <option value="">Choose a reason</option>
                    {REASONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                  </select>
                </label>
                <label className="field">Photo (optional, JPEG/PNG/WebP up to 5 MB)
                  <input type="file" accept="image/jpeg,image/png,image/webp"
                    onChange={(e) => addPhoto(c.line_no, e.target.files[0])} />
                  <span className="hint" aria-live="polite">
                    {ph?.busy ? 'Uploading…' : ph?.id ? `${ph.name} added` : ph?.error ? '' : 'No photo yet'}
                  </span>
                  {ph?.error && <span className="err" role="alert">{ph.error}</span>}
                </label>
              </div>
            );
          })}
          <div className="row between">
            <button type="button" className="btn plain" onClick={() => setStep(3)}>Back</button>
            <button type="button" className="btn primary" onClick={reasonsNext}
              disabled={Object.values(photos).some((p) => p?.busy)}>Continue</button>
          </div>
        </section>
      )}

      {step === 5 && (
        <section className="card" aria-labelledby="h-step">
          <h1 id="h-step" ref={heading} tabIndex={-1}>How should we refund you?</h1>
          <fieldset className="grid-auto" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="sr-only">Refund method</legend>
            {METHODS.map(([v, title, desc]) => (
              <label key={v} className={`choice-card ${method === v ? 'selected' : ''}`}>
                <span className="row">
                  <input type="radio" name="method" value={v} checked={method === v} onChange={() => setMethod(v)} />
                  <span style={{ fontWeight: 600, fontSize: 17 }}>{title}</span>
                </span>
                <span className="small muted">{desc}</span>
              </label>
            ))}
          </fieldset>
          <div className="row between">
            <button type="button" className="btn plain" onClick={() => setStep(4)}>Back</button>
            <button type="button" className="btn primary" onClick={() => setStep(6)}>Review</button>
          </div>
        </section>
      )}

      {step === 6 && (
        <section className="card" aria-labelledby="h-step">
          <h1 id="h-step" ref={heading} tabIndex={-1}>Check and submit</h1>
          {chosen.map((c) => (
            <div key={c.line_no} className="row between" style={{ borderBottom: '1px solid var(--rr-color-border)', paddingBottom: 12 }}>
              <strong>{c.name}</strong>
              <span className="muted">{REASONS.find(([v]) => v === reasons[c.line_no])?.[1]} · {photos[c.line_no]?.id ? 'photo added' : 'no photo'}</span>
            </div>
          ))}
          <p>Refund to: <strong>{METHODS.find(([v]) => v === method)[1]}</strong></p>
          <p className="small muted">The amount is confirmed after the warehouse inspects each item. Items graded C are refunded at 50%.
            Refunds over ₹10,000 are checked by finance before payment.</p>
          <ErrorNote error={error} />
          <div className="row between">
            <button type="button" className="btn plain" onClick={() => setStep(5)}>Back</button>
            <button type="button" className="btn primary" onClick={submit} disabled={busy}>{busy ? 'Submitting…' : 'Submit return'}</button>
          </div>
        </section>
      )}

      {step === 7 && result && (
        <>
          <ReturnStatus initial={result} headingRef={heading} />
          <div className="row no-print">
            <button type="button" className="btn plain" onClick={restart}>Start another return</button>
          </div>
        </>
      )}
    </Layout>
  );
}
