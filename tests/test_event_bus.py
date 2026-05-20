# tests/test_event_bus.py
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from harness.bus.event_bus import EventBus, bus
from harness.events import read_events


class TestEventBus(unittest.TestCase):

    def setUp(self) -> None:
        # Limpiar suscriptores para evitar efectos secundarios entre pruebas
        bus._subscribers.clear()

    def test_singleton_behavior(self) -> None:
        bus1 = EventBus()
        bus2 = EventBus()
        self.assertIs(bus1, bus2)
        self.assertIs(bus1, bus)

    def test_subscribe_and_unsubscribe(self) -> None:
        async def callback(payload: dict[str, Any]) -> None:
            pass

        bus.subscribe("test.event", callback)
        self.assertIn("test.event", bus._subscribers)
        self.assertIn(callback, bus._subscribers["test.event"])

        bus.unsubscribe("test.event", callback)
        self.assertNotIn(callback, bus._subscribers["test.event"])

    def test_publish_and_receive(self) -> None:
        received_payloads = []

        async def callback(payload: dict[str, Any]) -> None:
            received_payloads.append(payload)

        bus.subscribe("test.event", callback)
        
        # Ejecutar publicación asíncronamente
        asyncio.run(bus.publish("test.event", {"data": "hello"}))

        self.assertEqual(len(received_payloads), 1)
        self.assertEqual(received_payloads[0]["data"], "hello")

    def test_subscriber_error_handling(self) -> None:
        first_called = False
        second_called = False

        async def bad_callback(payload: dict[str, Any]) -> None:
            nonlocal first_called
            first_called = True
            raise ValueError("Algo falló en el suscriptor")

        async def good_callback(payload: dict[str, Any]) -> None:
            nonlocal second_called
            second_called = True

        bus.subscribe("test.error", bad_callback)
        bus.subscribe("test.error", good_callback)

        asyncio.run(bus.publish("test.error", {"foo": "bar"}))

        # Ambos deben llamarse, y el error del primero no debe impedir que se llame al segundo
        self.assertTrue(first_called)
        self.assertTrue(second_called)
