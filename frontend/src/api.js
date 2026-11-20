export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/** Calls the ReturnRight API. The token lives only in memory (never localStorage). */
export async function api(path, { method = 'GET', body, token, form } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError("We can't reach ReturnRight right now. Check your connection and try again.", 0);
  }
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const data = isJson ? await res.json() : null;
  if (!res.ok) throw new ApiError(data?.detail || `Something went wrong (${res.status}). Try again.`, res.status, data);
  return data;
}

/** Fetches a protected image (photo) and returns an object URL. */
export async function apiBlobUrl(path, token) {
  const res = await fetch(`/api${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError('Photo unavailable', res.status);
  return URL.createObjectURL(await res.blob());
}

export function newKey() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return 'k-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
}

export function inr(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  return '₹' + n.toLocaleString('en-IN', { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
}

export function when(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit' });
}

export function day(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric' });
}
