import json
import unittest
from contextlib import AsyncExitStack
import httpx
from api_bridge import app


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()
        self.requests = []
        self.mode = 'ok'

        def handle(request):
            self.requests.append(request)
            if self.mode == 'timeout':
                raise httpx.ReadTimeout('timeout', request=request)
            if self.mode == 'offline':
                raise httpx.ConnectError('offline', request=request)
            if self.mode == 'missing':
                return httpx.Response(404, json={'error': 'model not found'})
            if request.url.path == '/api/tags':
                return httpx.Response(200, json={'models': [{'name': 'test-model'}]})
            return httpx.Response(200, json={'message': {'role': 'assistant', 'content': 'test response'}, 'done': True})

        app.state.ollama = await self.stack.enter_async_context(httpx.AsyncClient(
            base_url='http://ollama.test', transport=httpx.MockTransport(handle)))
        self.client = await self.stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://api.test'))

    async def asyncTearDown(self):
        await self.stack.aclose()

    async def test_health_identifies_correct_module(self):
        response = await self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'popcat-api')
        self.assertEqual(len(self.requests), 0)

    async def test_chat_forwards_model_and_messages(self):
        payload = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hello'}]}
        response = await self.client.post('/api/chat', json=payload)
        self.assertEqual(response.status_code, 200)
        forwarded = json.loads(self.requests[-1].content)
        self.assertEqual(forwarded['model'], payload['model'])
        self.assertEqual(forwarded['messages'], payload['messages'])
        self.assertFalse(forwarded['stream'])
        self.assertEqual(response.json()['message']['content'], 'test response')

    async def test_models(self):
        response = await self.client.get('/api/models')
        self.assertEqual(response.json()['models'][0]['name'], 'test-model')

    async def test_validation(self):
        response = await self.client.post('/api/chat', json={'messages': []})
        self.assertEqual(response.status_code, 422)
        response = await self.client.post('/api/chat', json={'messages': [{'role': 'system', 'content': 'hello'}]})
        self.assertEqual(response.status_code, 422)

    async def test_ollama_timeout(self):
        self.mode = 'timeout'
        response = await self.client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'hello'}]})
        self.assertEqual(response.status_code, 504)

    async def test_ollama_offline(self):
        self.mode = 'offline'
        response = await self.client.get('/api/models')
        self.assertEqual(response.status_code, 502)

    async def test_model_not_found(self):
        self.mode = 'missing'
        response = await self.client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'hello'}]})
        self.assertEqual(response.status_code, 404)

if __name__ == '__main__':
    unittest.main()
