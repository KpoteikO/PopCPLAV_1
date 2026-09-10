import { db } from '@/db';
import { settings } from '@/db/schema';
import { getSettings, validateUrl, logEvent, errorMessage, sameOrigin } from '@/lib/system';
export const dynamic = 'force-dynamic';
export async function GET() { return Response.json(await getSettings()); }
export async function PUT(request: Request) {
  try {
    sameOrigin(request);
    const data = await request.json();
    if (typeof data.model !== 'string' || !/^[a-zA-Z0-9._:/-]{1,120}$/.test(data.model)) throw new Error('Некорректное имя модели');
    const values = { ollamaUrl: validateUrl(data.ollamaUrl, 'ollamaUrl'), backendUrl: validateUrl(data.backendUrl, 'backendUrl'), nginxUrl: validateUrl(data.nginxUrl, 'nginxUrl'), model: data.model, updatedAt: new Date() };
    await db.insert(settings).values({ id: 1, ...values }).onConflictDoUpdate({ target: settings.id, set: values });
    await logEvent('System', 'info', 'Настройки подключения обновлены');
    return Response.json({ ok: true, ...values });
  } catch (e) { return Response.json({ error: errorMessage(e) }, { status: 400 }); }
}
