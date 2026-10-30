"""HTTP-level smoke tests for the FutBot API router and authentication."""

import unittest

from aiohttp.test_utils import TestClient, TestServer

from api_server import create_app


class FutBotApiRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(create_app("test-secret")))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_health_is_public(self):
        response = await self.client.get("/health")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["ok"])

    async def test_api_routes_require_key(self):
        response = await self.client.get("/api/v1/status")
        self.assertEqual(response.status, 401)

    async def test_authenticated_status(self):
        response = await self.client.get(
            "/api/v1/status",
            headers={"X-API-Key": "test-secret"},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["discord_gateway_connected"])


if __name__ == "__main__":
    unittest.main()
