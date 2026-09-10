import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
const execute = promisify(execFile);
// Fixed source allowlist. No user-provided paths, credentials, .env or database exports.
const sourceFiles = [
  'src', 'deploy', 'tests', 'public/popcat-integration.tar.gz',
  'Dockerfile', '.dockerignore', '.env.example', 'package.json', 'package-lock.json',
  'next.config.ts', 'next-env.d.ts', 'tsconfig.json', 'postcss.config.mjs',
  'eslint.config.mjs', 'drizzle.config.ts', 'playwright.config.ts', 'README.md',
];
export async function GET() {
  try {
    const { stdout } = await execute('tar', [
      '--exclude=__pycache__', '--exclude=.venv', '--exclude=.env', '--exclude=deploy/.env*',
      '--exclude=deploy/*.db', '--exclude=deploy/*.log',
      '-czf', '-', ...sourceFiles,
    ], { cwd: process.cwd(), encoding: 'buffer', maxBuffer: 12 * 1024 * 1024, timeout: 15000 });
    return new Response(new Uint8Array(stdout), { headers: {
      'Content-Type': 'application/gzip',
      'Content-Disposition': 'attachment; filename="popcat-app.tar.gz"',
      'Cache-Control': 'no-store',
    } });
  } catch {
    return Response.json({ error: 'Не удалось собрать архив. Проверьте наличие tar и полного исходного кода на сервере.' }, { status: 500 });
  }
}
