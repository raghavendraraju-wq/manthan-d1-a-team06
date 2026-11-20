import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { api } from '../api.js';
import { HOME_FOR_ROLE, useSession } from '../session.jsx';
import { Layout } from '../ui.jsx';

export default function StaffSignIn() {
  const { setSession } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState(null);

  useEffect(() => {
    api('/demo/info').then((d) => d.demo && setDemo(d)).catch(() => {});
  }, []);

  async function signIn(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api('/auth/login', { method: 'POST', body: { email, password } });
      setSession({ token: r.token, role: r.role, name: r.name });
      navigate(location.state?.next || HOME_FOR_ROLE[r.role], { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Layout width="tiny" menu={<span className="muted">Staff sign-in</span>}>
      <form className="card" onSubmit={signIn} aria-labelledby="h-signin" noValidate>
        <h1 id="h-signin">Sign in</h1>
        <p className="muted">You'll only see the screens for your role.</p>
        <label className="field">Work email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)}
            aria-invalid={!!error} aria-describedby="signin-err" />
        </label>
        <label className="field">Password
          <input type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} aria-invalid={!!error} aria-describedby="signin-err" />
        </label>
        <div id="signin-err">{error && <p className="alert warn" role="alert">{error}</p>}</div>
        <button type="submit" className="btn primary lg" disabled={busy || !email || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      {demo && (
        <section aria-labelledby="h-demo" className="stack small muted">
          <h2 id="h-demo" style={{ fontSize: 14, fontWeight: 400 }}>
            Demo accounts (password <code>{demo.password}</code>):
          </h2>
          <div className="row">
            {demo.staff.map((s) => (
              <button key={s.email} type="button" className="btn chip" aria-label={`Fill in the ${s.role} demo account`}
                onClick={() => { setEmail(s.email); setPassword(demo.password); setError(null); }}>
                {s.role[0].toUpperCase() + s.role.slice(1)}
              </button>
            ))}
          </div>
        </section>
      )}
    </Layout>
  );
}
