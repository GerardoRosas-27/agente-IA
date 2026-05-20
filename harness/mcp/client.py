# harness/mcp/client.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class MCPClientError(Exception):
    """Excepción para errores del Cliente MCP."""
    pass


class MCPClient:
    """Cliente estandarizado para servidores Model Context Protocol (MCP) vía stdio/JSON-RPC."""

    def __init__(self, server_name: str, command: str, args: list[str] | None = None):
        self.server_name = server_name
        self.command = command
        self.args = args or []
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task | None = None
        self._is_connected = False

    async def connect(self) -> None:
        """Inicia el subproceso del servidor MCP y arranca el bucle de lectura."""
        if self._is_connected:
            return

        try:
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._is_connected = True
            self._reader_task = asyncio.create_task(self._read_loop())
        except Exception as exc:
            self._is_connected = False
            raise MCPClientError(f"No se pudo iniciar el servidor MCP '{self.server_name}': {exc}") from exc

    async def disconnect(self) -> None:
        """Cierra la conexión con el servidor MCP de forma limpia."""
        self._is_connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self.process:
            try:
                self.process.terminate()
                await self.process.wait()
            except Exception:
                pass
            self.process = None

        # Resolver cualquier petición pendiente con un error
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.set_exception(MCPClientError("Cliente desconectado."))
        self._pending_requests.clear()

    async def _read_loop(self) -> None:
        """Lee respuestas continuamente desde el stdout del subproceso."""
        if not self.process or not self.process.stdout:
            return

        while self._is_connected:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break  # EOF alcanzado

                data = json.loads(line.decode("utf-8", errors="ignore").strip())
                if "id" in data:
                    req_id = data["id"]
                    if req_id in self._pending_requests:
                        fut = self._pending_requests.pop(req_id)
                        if not fut.done():
                            fut.set_result(data)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[MCPClient] Error leyendo del servidor '{self.server_name}': {exc}")
                await asyncio.sleep(0.1)

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Envía una solicitud JSON-RPC 2.0 y espera la respuesta correspondiente."""
        if not self._is_connected or not self.process or not self.process.stdin:
            raise MCPClientError(f"El cliente MCP '{self.server_name}' no está conectado.")

        req_id = self._next_id
        self._next_id += 1

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut

        try:
            payload = json.dumps(request, ensure_ascii=False) + "\n"
            self.process.stdin.write(payload.encode("utf-8"))
            await self.process.stdin.drain()
        except Exception as exc:
            self._pending_requests.pop(req_id, None)
            raise MCPClientError(f"Error al escribir al servidor '{self.server_name}': {exc}") from exc

        try:
            response = await fut
            if "error" in response:
                err = response["error"]
                raise MCPClientError(f"Error de servidor MCP [{err.get('code')}]: {err.get('message')}")
            return response.get("result") or {}
        except Exception as exc:
            if isinstance(exc, MCPClientError):
                raise
            raise MCPClientError(f"Error esperando respuesta de '{self.server_name}': {exc}") from exc

    async def list_tools(self) -> list[dict[str, Any]]:
        """Solicita la lista de herramientas disponibles al servidor MCP."""
        result = await self._send_request("tools/list")
        return result.get("tools") or []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ejecuta una herramienta en el servidor MCP con los argumentos dados."""
        params = {
            "name": tool_name,
            "arguments": arguments or {},
        }
        return await self._send_request("tools/call", params)


class MCPRegistry:
    """Registro y cargador para múltiples servidores MCP."""
    def __init__(self):
        self.clients: dict[str, MCPClient] = {}

    def load_from_config(self, config_path: Path) -> None:
        """Carga servidores MCP desde un archivo de configuración JSON."""
        if not config_path.is_file():
            return

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            mcp_servers = data.get("mcpServers") or {}
            for name, cfg in mcp_servers.items():
                command = cfg.get("command")
                args = cfg.get("args") or []
                if command:
                    self.clients[name] = MCPClient(name, command, args)
        except Exception as exc:
            print(f"[MCPRegistry] Error cargando configuración MCP desde {config_path}: {exc}")

    async def connect_all(self) -> None:
        """Conecta todos los clientes registrados."""
        tasks = [client.connect() for client in self.clients.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def disconnect_all(self) -> None:
        """Desconecta todos los clientes registrados."""
        tasks = [client.disconnect() for client in self.clients.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_client(self, name: str) -> MCPClient | None:
        return self.clients.get(name)


# Instancia única del registro MCP
mcp_registry = MCPRegistry()
