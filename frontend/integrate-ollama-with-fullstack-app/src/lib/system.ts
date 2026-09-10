import { db } from '@/db';
import { settings, events } from '@/db/schema';

export type ConnectionSettings = { ollamaUrl: string; backendUrl: string; nginxUrl: string; model: string };
export type ServiceResult = { id: string; name: string; ok: boolean; latency: number; detail: string; endpoint: string; checkedAt: string };
export type Model = { name: string; size: number; details?: { parameter_size?: string; quantization_level?: string; family?: string } };
export const defaults: ConnectionSettings = {
  ollamaUrl: process.env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434',
  backendUrl: process.env.BACKEND_URL || 'http://127.0.0.1:8000',
  nginxUrl: process.env.NGINX_URL || 'http://127.0.0.1:8080',
  model: 'qwen2.5-coder:7b-instruct-q4_K_M',
};
export async function getSettings(): Promise<ConnectionSettings> {
  const [row] = await db.select().from(settings).limit(1);
  return row || defaults;
}
export function validateUrl(value: unknown, field: keyof ConnectionSettings): string {
  if (typeof value !== 'string') throw new Error('Укажите URL сервиса');
  const u = new URL(value);
  const allowed = new Set([defaults[field], ...Object.values(defaults).slice(0, 3), ...(process.env.SERVICE_ALLOWED_ORIGINS || '').split(',')].filter(Boolean).map(v => { try { return new URL(v).origin; } catch { return ''; } }));
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash || u.pathname !== '/' || !allowed.has(u.origin)) {
    throw new Error('URL не разрешён. Добавьте точный origin в SERVICE_ALLOWED_ORIGINS на сервере и перезапустите приложение.');
  }
  return u.origin;
}
export async function requestJson(base: string, path: string, init?: RequestInit, timeout = 5000) {
  const response = await fetch(base + path, { ...init, redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(timeout) });
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.status === 404 ? 'маршрут не найден — проверьте модуль Uvicorn и proxy_pass' : 'сервис отклонил запрос'}`);
  return response.json();
}
export function errorMessage(error: unknown) {
  if (error instanceof Error) {
    if (error.name === 'TimeoutError' || error.name === 'AbortError') return 'Тайм-аут: сервис не ответил вовремя';
    if (error.message === 'fetch failed') return 'Нет соединения — проверьте запуск сервиса и адрес в настройках';
    return error.message.slice(0, 400);
  }
  return 'Неизвестная ошибка подключения';
}
export async function logEvent(service: string, level: string, message: string) {
  await db.insert(events).values({ service, level, message });
}
export function sameOrigin(request: Request) {
  const origin = request.headers.get('origin');
  if (origin && new URL(origin).host !== (request.headers.get('x-forwarded-host') || request.headers.get('host'))) throw new Error('Cross-origin запрос запрещён');
}
