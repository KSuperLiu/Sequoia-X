let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const mutation = !["GET", "HEAD", "OPTIONS"].includes(method);
  const send = () => {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (mutation && csrfToken) headers.set("X-CSRF-Token", csrfToken);
    return fetch(`/api/v1${path}`, { ...options, headers, credentials: "include" });
  };
  let response = await send();
  if (response.status === 403 && mutation) {
    const session = await fetch("/api/v1/auth/me", { credentials: "include" });
    if (session.ok) {
      const user = await session.json() as { csrf_token?: string };
      if (user.csrf_token) {
        setCsrfToken(user.csrf_token);
        response = await send();
      }
    }
  }
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // 保留 HTTP 错误文本
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const money = (value?: number | null) =>
  value == null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);

export const accountMoney = (value?: number | null) => {
  if (value == null) return "—";
  if (Math.abs(value) >= 10_000) {
    return `${new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value / 10_000)} 万`;
  }
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
};

export const pct = (value?: number | null) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
