let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: "include" });
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

export const pct = (value?: number | null) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
