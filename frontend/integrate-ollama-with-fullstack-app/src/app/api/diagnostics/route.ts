import { db } from '@/db';
import { diagnosticRuns, events } from '@/db/schema';
import { desc } from 'drizzle-orm';
import { getSettings, requestJson, errorMessage, sameOrigin, type ServiceResult } from '@/lib/system';
export const dynamic = 'force-dynamic';
export async function GET() {
  const [run] = await db.select().from(diagnosticRuns).orderBy(desc(diagnosticRuns.id)).limit(1);
  return Response.json(run || { results: [], createdAt: null });
}
export async function POST(request: Request) {
  try {
    sameOrigin(request);
    const s = await getSettings();
    const checks = [
      { id: 'nginx', name: 'Nginx', base: s.nginxUrl, path: '/health', validate: (d: Record<string, unknown>) => d.service === 'nginx' },
      { id: 'backend', name: 'Backend', base: s.backendUrl, path: '/health', validate: (d: Record<string, unknown>) => d.service === 'popcat-api' },
      { id: 'ollama', name: 'Ollama', base: s.ollamaUrl, path: '/api/tags', validate: (d: Record<string, unknown>) => Array.isArray(d.models) },
    ];
    const results: ServiceResult[] = await Promise.all(checks.map(async c => {
      const start = Date.now();
      try {
        const data = await requestJson(c.base, c.path);
        if (!c.validate(data)) throw new Error('Неожиданный ответ: по этому адресу запущен другой сервис');
        return { id: c.id, name: c.name, ok: true, latency: Date.now() - start, detail: c.id === 'ollama' ? `API доступен · моделей: ${data.models.length}` : 'Health-check пройден', endpoint: c.base + c.path, checkedAt: new Date().toISOString() };
      } catch (e) {
        return { id: c.id, name: c.name, ok: false, latency: Date.now() - start, detail: errorMessage(e), endpoint: c.base + c.path, checkedAt: new Date().toISOString() };
      }
    }));
    results.unshift({ id: 'frontend', name: 'Frontend', ok: true, latency: 0, detail: 'React → API панели → PostgreSQL: соединение установлено', endpoint: '/api/diagnostics', checkedAt: new Date().toISOString() });
    const [run] = await db.insert(diagnosticRuns).values({ results }).returning();
    await db.insert(events).values(results.map(r => ({ service: r.name, level: r.ok ? 'success' : 'error', message: r.detail })));
    return Response.json(run);
  } catch(e) { return Response.json({ error: errorMessage(e) }, { status: 500 }); }
}
