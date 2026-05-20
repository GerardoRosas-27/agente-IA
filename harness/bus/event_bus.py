# harness/bus/event_bus.py
from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from harness.events import emit_event


class EventBus:
    """Bus de eventos reactivo y asíncrono para coordinar sub-agentes."""
    _instance: EventBus | None = None

    def __new__(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._subscribers = {}
        return cls._instance

    def __init__(self) -> None:
        # Evitar sobreescribir _subscribers si ya existe (patrón Singleton)
        if not hasattr(self, "_subscribers"):
            self._subscribers: dict[str, list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]]] = {}

    def subscribe(self, event_type: str, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        """Suscribe una función callback asíncrona a un tipo de evento."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        """Desuscribe un callback de un tipo de evento."""
        if event_type in self._subscribers:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publica un evento, lo registra en events.jsonl e invoca callbacks."""
        # Registrar el evento persistentemente
        emit_event(event_type, **payload)

        if event_type in self._subscribers:
            callbacks = self._subscribers[event_type]
            if not callbacks:
                return

            # Ejecutar todas las tareas asíncronamente en paralelo
            tasks = []
            for cb in callbacks:
                tasks.append(self._safe_execute(cb, payload))
            await asyncio.gather(*tasks)

    async def _safe_execute(self, cb: Callable[[dict[str, Any]], Coroutine[Any, Any, None]], payload: dict[str, Any]) -> None:
        """Ejecuta un callback de forma segura, capturando cualquier excepción."""
        try:
            await cb(payload)
        except Exception as exc:
            # Registrar el error del suscriptor de manera observable
            emit_event(
                "event_bus.subscriber_error",
                subscriber=cb.__name__ if hasattr(cb, "__name__") else str(cb),
                error=str(exc),
            )
            print(f"[EventBus] Error en suscriptor {cb}: {exc}")


# Instancia única pre-inicializada para importación directa
bus = EventBus()
