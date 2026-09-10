import { getSettings, requestJson, errorMessage } from '@/lib/system';
export const dynamic = 'force-dynamic';
export async function GET() {
  try {
    const s = await getSettings();
    const data = await requestJson(s.ollamaUrl, '/api/tags');
    if (!Array.isArray(data.models)) throw new Error('Некорректный ответ Ollama');
    return Response.json(data);
  } catch(e) { return Response.json({ error: errorMessage(e), models: [] }, { status: 502 }); }
}
