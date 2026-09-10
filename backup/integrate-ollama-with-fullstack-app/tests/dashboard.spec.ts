import { test, expect } from '@playwright/test';

test('dashboard, settings and service details', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Обзор системы', exact: true })).toBeVisible();
  await expect(page.locator('.service-card')).toHaveCount(4);
  await page.getByRole('button', { name: 'Настройки', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByLabel('Ollama URL', { exact: true })).toHaveValue(/11434/);
  await page.getByRole('button', { name: 'Сохранить настройки' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('status')).toContainText('Настройки сохранены');
  await page.locator('.service-card').first().click();
  await expect(page.getByRole('dialog')).toContainText('Frontend');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(errors).toEqual([]);
});

test('real diagnostics persist and logs can be filtered', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Проверить систему', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Проверка завершена', { timeout: 15000 });
  await page.locator('nav').getByRole('button', { name: 'Диагностика' }).click();
  await expect(page.locator('.diagnostic-item')).toHaveCount(4);
  await expect(page.locator('.diagnostic-item').first()).toContainText('соединение установлено');
  await page.reload();
  await expect(page.locator('.diagnostic-item').first()).toContainText('соединение установлено');
  await page.locator('nav').getByRole('button', { name: /Логи системы/ }).click();
  await page.getByRole('button', { name: 'Ошибки', exact: true }).click();
  await expect(page.locator('tbody tr').first()).toContainText('Ошибка');
  await page.getByPlaceholder('Поиск по событиям').fill('no-such-event-12345');
  await expect(page.getByText('События не найдены')).toBeVisible();
});

test('configuration files, models failure and chat error are usable', async ({ page }) => {
  await page.goto('/#config');
  await expect(page.locator('.code-view')).toContainText('proxy_pass');
  await page.getByRole('button', { name: 'Python API', exact: true }).click();
  await expect(page.locator('.code-view')).toContainText('OLLAMA_BASE_URL');
  await page.locator('nav').getByRole('button', { name: 'Модели Ollama' }).click();
  await page.getByRole('button', { name: 'Получить модели' }).click();
  await expect(page.getByRole('heading', { name: 'Не удалось подключиться к Ollama' })).toBeVisible({ timeout: 12000 });
  await page.locator('nav').getByRole('button', { name: 'Тестовый чат' }).click();
  await page.getByRole('button', { name: 'Привет! Представься в одном предложении.' }).click();
  await expect(page.getByLabel('Сообщение модели')).toHaveValue('Привет! Представься в одном предложении.');
  await page.getByRole('button', { name: 'Отправить сообщение' }).click();
  await expect(page.locator('.chat-error')).toContainText('Запрос не выполнен', { timeout: 12000 });
  await expect(page.getByLabel('Сообщение модели')).not.toHaveValue('');
});

test('API rejects untrusted endpoints, traversal, and invalid messages', async ({ request }) => {
  const settings = await (await request.get('/api/settings')).json();
  const rejected = await request.put('/api/settings', { data: { ...settings, ollamaUrl: 'http://169.254.169.254' } });
  expect(rejected.status()).toBe(400);
  expect((await request.get('/api/config?file=../../.env')).status()).toBe(404);
  expect((await request.post('/api/chat', { data: { messages: [] } })).status()).toBe(400);
  expect((await request.post('/api/diagnostics', { headers: { Origin: 'https://untrusted.example' } })).status()).not.toBe(200);
});

test('mobile navigation and layout do not overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Обзор системы', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Открыть меню' }).click();
  await page.locator('nav').getByRole('button', { name: 'Тестовый чат' }).click();
  await expect(page.getByRole('heading', { name: 'Тестовый чат', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
