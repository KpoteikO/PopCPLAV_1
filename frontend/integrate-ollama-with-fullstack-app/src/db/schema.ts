import { pgTable, serial, text, jsonb, timestamp, integer } from 'drizzle-orm/pg-core';

export const settings = pgTable('connection_settings', {
  id: integer('id').primaryKey().default(1),
  ollamaUrl: text('ollama_url').notNull(),
  backendUrl: text('backend_url').notNull(),
  nginxUrl: text('nginx_url').notNull(),
  model: text('model').notNull(),
  updatedAt: timestamp('updated_at').defaultNow().notNull(),
});
export const diagnosticRuns = pgTable('diagnostic_runs', {
  id: serial('id').primaryKey(),
  results: jsonb('results').notNull(),
  createdAt: timestamp('created_at').defaultNow().notNull(),
});
export const events = pgTable('system_events', {
  id: serial('id').primaryKey(),
  service: text('service').notNull(),
  level: text('level').notNull(),
  message: text('message').notNull(),
  createdAt: timestamp('created_at').defaultNow().notNull(),
});
