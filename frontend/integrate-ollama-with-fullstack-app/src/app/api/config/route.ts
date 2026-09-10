import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
const files: Record<string, string> = { nginx: 'nginx.conf', local: 'nginx.local.conf', compose: 'compose.yaml', backend: 'api_bridge.py', readme: 'README.md' };
export async function GET(request: Request) {
  const key = new URL(request.url).searchParams.get('file') || 'nginx';
  if (!Object.hasOwn(files, key)) return Response.json({ error: 'Файл не найден' }, { status: 404 });
  const content = await readFile(join(process.cwd(), 'deploy', files[key]), 'utf8');
  return Response.json({ name: `deploy/${files[key]}`, content });
}
