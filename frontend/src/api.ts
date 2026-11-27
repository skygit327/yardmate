let csrfToken = '';
let authGeneration = 0;

export function setCsrfToken(token: string) {
  if (csrfToken !== token) authGeneration += 1;
  csrfToken = token;
}

export async function api<T>(path: string, data?: unknown, method?: string): Promise<T> {
  const generation = authGeneration;
  const response = await fetch(`/api${path}`, {
    method: method ?? (data === undefined ? 'GET' : 'POST'),
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      ...(data instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      'X-Spec-Agent': '1',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
    body: data === undefined ? undefined : data instanceof FormData ? data : JSON.stringify(data),
  });
  if (generation !== authGeneration) throw new Error('Your sign-in session has changed.');
  if (!response.ok) {
    if (response.status === 401 && !['/auth/login', '/auth/me'].includes(path)) {
      window.dispatchEvent(new Event('spec-agent:unauthorized'));
    }
    const payload = await response.json().catch(() => ({ detail: 'Please check your connection to the server.' }));
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item: { msg: string }) => item.msg).join(' / ')
      : payload.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return stripMarkers(await response.json()) as T;
}

// Demo records may carry "(synthetic)" prefixes in Korean, Chinese or English; the UI shows names without them.
const MARKER = /\((?:\uac00\uc0c1|\u865a\u62df|\u865b\u64ec|Synthetic)\)\s*/g;

export function stripMarkers(value: unknown): unknown {
  if (typeof value === 'string') return value.includes('(') ? value.replace(MARKER, '') : value;
  if (Array.isArray(value)) return value.map(stripMarkers);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stripMarkers(item)]));
  }
  return value;
}

export function requestId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
