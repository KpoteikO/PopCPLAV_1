import { getSettings, requestJson, errorMessage, logEvent, sameOrigin } from '@/lib/system';
export const dynamic = 'force-dynamic';
export async function POST(request: Request) {
  try {
    sameOrigin(request);
    const { messages, model } = await request.json();
    if (!Array.isArray(messages) || messages.length < 1 || messages.length > 40 || messages.some(m => !['user', 'assistant'].includes(m.role) || typeof m.content !== 'string' || !m.content.trim() || m.content.length > 12000)) return Response.json({ error: 'Некорректное сообщение (до 12 000 символов, до 40 сообщений)' }, { status: 400 });
    const s = await getSettings();
    const selectedModel = typeof model === 'string' && /^[a-zA-Z0-9._:/-]{1,120}$/.test(model) ? model : s.model;
    const data = await requestJson(s.backendUrl, '/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ messages, model: selectedModel }) }, 300000);
    if (typeof data.message?.content !== 'string') throw new Error('Backend не вернул message.content. Установите исправленный api_bridge.py из комплекта.');
    await logEvent('Backend', 'success', `Тестовый запрос выполнен · ${selectedModel}`);
    return Response.json(data);
  } catch(e) {
    const message = errorMessage(e);
    await logEvent('Backend', 'error', message);
    return Response.json({ error: message }, { status: 502 });
  }
}
