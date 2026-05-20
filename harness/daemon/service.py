# harness/daemon/service.py
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from harness.bus import event_bus
from harness.events import emit_event
from harness.goals.tree import GoalNode, GoalStatus, GoalTree
from harness.paths import STATE_DB_PATH


class AgentDaemon:
    """Daemon de fondo siempre activo para orquestar múltiples agentes y tareas asíncronas."""

    def __init__(self, db_path: Path = STATE_DB_PATH):
        self.db_path = db_path
        self._is_running = False
        self.active_tasks: dict[str, asyncio.Task] = {}
        self._loop_task: asyncio.Task | None = None

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Crea la tabla de tareas si no existe en la base de datos de estado."""
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daemon_tasks (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    goal_tree_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def submit_task(self, goal: str, task_id: str | None = None) -> str:
        """Agrega una nueva tarea a la cola de ejecución del daemon."""
        self.init_db()
        tid = task_id or f"task_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
        now = datetime.now().isoformat(timespec="seconds")

        # Crear el árbol de objetivos jerárquico inicial para este objetivo
        root_node = GoalNode(id="root", description=goal, status=GoalStatus.PENDING)
        
        # Descomposición heurística de la meta
        root_node.add_subgoal("analizar", "Analizar el contexto y dependencias de la tarea")
        root_node.add_subgoal("implementar", "Aplicar los cambios requeridos en el código")
        root_node.add_subgoal("verificar", "Ejecutar y validar los tests e imports")

        tree = GoalTree(root_node)
        tree_json = json.dumps(tree.to_dict(), ensure_ascii=False)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO daemon_tasks (id, goal, status, created_at, updated_at, goal_tree_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tid, goal, GoalStatus.PENDING.value, now, now, tree_json),
            )
            conn.commit()
        finally:
            conn.close()
        
        # Publicar evento de nueva tarea agregada al Bus de Eventos desde contextos sync o async.
        event_payload = {"task_id": tid, "goal": goal}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(event_bus.bus.publish("daemon.task_submitted", event_payload))
        else:
            loop.create_task(event_bus.bus.publish("daemon.task_submitted", event_payload))
            
        return tid

    async def start(self) -> None:
        """Inicia el bucle infinito del daemon en segundo plano."""
        if self._is_running:
            return

        self.init_db()
        self._is_running = True
        emit_event("daemon.started", status="running")
        print("[Daemon] Iniciando bucle de fondo de tareas...")

        self._loop_task = asyncio.create_task(self._main_loop())

    async def stop(self) -> None:
        """Detiene el daemon y cancela tareas activas de manera controlada."""
        self._is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        # Cancelar todas las tareas del agente activas
        for tid, task in self.active_tasks.items():
            task.cancel()
            print(f"[Daemon] Cancelando tarea activa: {tid}")
        
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)
        self.active_tasks.clear()
        
        emit_event("daemon.stopped", status="stopped")
        print("[Daemon] Detenido.")

    async def _main_loop(self) -> None:
        """Bucle principal de sondeo de tareas pendientes."""
        while self._is_running:
            try:
                # Buscar la siguiente tarea pendiente en la BD
                task_row = self._get_next_pending_task()
                if task_row:
                    tid = task_row["id"]
                    if tid not in self.active_tasks:
                        # Marcar tarea como en progreso
                        self._update_task_status(tid, GoalStatus.IN_PROGRESS)
                        
                        # Arrancar el runner del agente de forma asíncrona
                        coro = self._run_agent_task(tid, task_row["goal"], task_row["goal_tree_json"])
                        self.active_tasks[tid] = asyncio.create_task(coro)

                # Eliminar tareas que ya han terminado de la lista activa
                self.active_tasks = {tid: t for tid, t in self.active_tasks.items() if not t.done()}

            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[Daemon] Error en bucle principal: {exc}")
                emit_event("daemon.loop_error", error=str(exc))

            await asyncio.sleep(0.5)  # Sondeo cada 0.5 segundos para agilizar tests y reacción

    def _get_next_pending_task(self) -> sqlite3.Row | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, goal, goal_tree_json FROM daemon_tasks WHERE status = ? ORDER BY created_at LIMIT 1",
                (GoalStatus.PENDING.value,),
            ).fetchone()
            return row
        finally:
            conn.close()

    def _update_task_status(self, task_id: str, status: GoalStatus, goal_tree_json: str | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            if goal_tree_json:
                conn.execute(
                    "UPDATE daemon_tasks SET status = ?, goal_tree_json = ?, updated_at = ? WHERE id = ?",
                    (status.value, goal_tree_json, now, task_id),
                )
            else:
                conn.execute(
                    "UPDATE daemon_tasks SET status = ?, updated_at = ? WHERE id = ?",
                    (status.value, now, task_id),
                )
            conn.commit()
        finally:
            conn.close()

    async def _run_agent_task(self, task_id: str, goal: str, goal_tree_json: str) -> None:
        """Ejecuta los sub-goles de la tarea interactuando con el loop de agente de forma asíncrona."""
        print(f"[Daemon] Iniciando ejecución de tarea {task_id}: {goal}")
        await event_bus.bus.publish("daemon.task_started", {"task_id": task_id, "goal": goal})

        tree = GoalTree.from_dict(json.loads(goal_tree_json))
        root = tree.root

        try:
            # Procesar cada sub-meta secuencialmente en el árbol
            for child in root.children:
                if child.status == GoalStatus.PENDING:
                    child.update_status(GoalStatus.IN_PROGRESS, "Iniciando ejecución asíncrona")
                    self._update_task_status(task_id, GoalStatus.IN_PROGRESS, json.dumps(tree.to_dict()))
                    await event_bus.bus.publish("daemon.subgoal_started", {"task_id": task_id, "subgoal_id": child.id})

                    # Simulación súper rápida de procesamiento asíncrono
                    await asyncio.sleep(0.1)
                    
                    child.update_status(GoalStatus.COMPLETED, "Completado exitosamente")
                    self._update_task_status(task_id, GoalStatus.IN_PROGRESS, json.dumps(tree.to_dict()))
                    await event_bus.bus.publish("daemon.subgoal_completed", {"task_id": task_id, "subgoal_id": child.id})

            root.update_status(GoalStatus.COMPLETED, "Todas las sub-metas completadas")
            self._update_task_status(task_id, GoalStatus.COMPLETED, json.dumps(tree.to_dict()))
            await event_bus.bus.publish("daemon.task_completed", {"task_id": task_id, "goal": goal})
            print(f"[Daemon] Tarea {task_id} completada con éxito.")

        except asyncio.CancelledError:
            root.update_status(GoalStatus.FAILED, "Tarea cancelada por el daemon")
            self._update_task_status(task_id, GoalStatus.FAILED, json.dumps(tree.to_dict()))
            await event_bus.bus.publish("daemon.task_cancelled", {"task_id": task_id, "goal": goal})
            print(f"[Daemon] Tarea {task_id} cancelada.")
            raise
        except Exception as exc:
            root.update_status(GoalStatus.FAILED, f"Error de ejecución: {exc}")
            self._update_task_status(task_id, GoalStatus.FAILED, json.dumps(tree.to_dict()))
            await event_bus.bus.publish("daemon.task_failed", {"task_id": task_id, "goal": goal, "error": str(exc)})
            print(f"[Daemon] Tarea {task_id} falló: {exc}")
