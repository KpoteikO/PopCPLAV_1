import { db } from '@/db';
import { events } from '@/db/schema';
import { desc } from 'drizzle-orm';
export const dynamic = 'force-dynamic';
export async function GET() { return Response.json(await db.select().from(events).orderBy(desc(events.id)).limit(100)); }
