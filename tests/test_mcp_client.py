# tests/test_mcp_client.py
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from harness.mcp.client import MCPClient, MCPClientError, MCPRegistry


class TestMCPClient(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        # Crear un servidor MCP de prueba escrito en Python
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.server_path = Path(self.tmp_dir.name) / "mock_mcp_server.py"
        
        server_code = """
import sys
import json

for line in sys.stdin:
    if not line.strip():
        continue
    try:
        req = json.loads(line)
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        
        if method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [{"name": "add_numbers", "description": "Suma dos números"}]
                }
            }
        elif method == "tools/call" and params.get("name") == "add_numbers":
            args = params.get("arguments") or {}
            a = args.get("a", 0)
            b = args.get("b", 0)
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"sum": a + b}
            }
        else:
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Método no encontrado"}
            }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\\n")
        sys.stderr.flush()
"""
        self.server_path.write_text(server_code, encoding="utf-8")

    async def asyncTearDown(self) -> None:
        self.tmp_dir.cleanup()

    async def test_mcp_client_lifecycle_and_execution(self) -> None:
        # Usar el ejecutable de python actual para correr nuestro servidor mock
        client = MCPClient("mock_server", sys.executable, [str(self.server_path)])
        
        await client.connect()
        self.assertTrue(client._is_connected)

        # 1. Listar herramientas
        tools = await client.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "add_numbers")

        # 2. Llamar a la herramienta 'add_numbers'
        result = await client.call_tool("add_numbers", {"a": 10, "b": 15})
        self.assertEqual(result.get("sum"), 25)

        # 3. Desconectar
        await client.disconnect()
        self.assertFalse(client._is_connected)

    async def test_mcp_registry_loading(self) -> None:
        config_path = Path(self.tmp_dir.name) / "mcp_config.json"
        config_data = {
            "mcpServers": {
                "mock_server_1": {
                    "command": sys.executable,
                    "args": [str(self.server_path)]
                }
            }
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        registry = MCPRegistry()
        registry.load_from_config(config_path)

        self.assertIn("mock_server_1", registry.clients)
        client = registry.get_client("mock_server_1")
        self.assertIsNotNone(client)

        assert client is not None
        await client.connect()
        self.assertTrue(client._is_connected)
        
        tools = await client.list_tools()
        self.assertEqual(tools[0]["name"], "add_numbers")
        
        await client.disconnect()
