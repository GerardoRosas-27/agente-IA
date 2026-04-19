"""
Mundo 3D procedural para la mosca.

Un cubo con 3 tipos de objetos:
  - COMIDA (verde): la mosca gana energia al tocarla; respawnea.
  - PELIGRO (rojo): la mosca pierde energia / recompensa al tocarlo.
  - SEGURO (azul): si la mosca se queda ahi con baja energia, la recupera.

La mosca tiene posicion y orientacion (heading). Sus sensores cuantifican
distancia y direccion al item mas cercano de cada tipo + energia interna.
"""
from __future__ import annotations

from collections import deque

import numpy as np

ACTIONS = [
    "avanzar", "girar_izq", "girar_der", "retroceder",
    "quieto", "explorar", "subir", "bajar",
]


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def _rot_z(v: np.ndarray, angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2]])


class FlyWorld:
    def __init__(
        self,
        size: float = 20.0,
        n_food: int = 8,
        n_threat: int = 4,
        n_safe: int = 3,
        seed: int = 0,
    ):
        self.size = size
        self.rng = np.random.default_rng(seed)

        self.food   = self._spawn_points(n_food)
        self.threat = self._spawn_points(n_threat)
        self.safe   = self._spawn_points(n_safe)

        self.fly_pos = np.array([0.0, 0.0, size / 2], dtype=np.float32)
        self.fly_heading = _unit(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        self.energy = 1.0

        self.trail: deque = deque(maxlen=200)
        self.trail.append(self.fly_pos.copy())

        self.eaten_food = 0
        self.hit_threat = 0
        self.rested_safe = 0
        self.steps = 0

        self.speed = 0.25 * (size / 20.0)
        self.turn_rate = 0.35
        self.vertical_step = 0.2 * (size / 20.0)

    def _spawn_points(self, n: int) -> np.ndarray:
        half = self.size / 2
        return self.rng.uniform(-half, half, size=(n, 3)).astype(np.float32)

    def _nearest(self, items: np.ndarray) -> tuple[float, np.ndarray]:
        if len(items) == 0:
            return self.size, np.zeros(3, dtype=np.float32)
        d = items - self.fly_pos
        dist = np.linalg.norm(d, axis=1)
        i = int(np.argmin(dist))
        return float(dist[i]), _unit(d[i]).astype(np.float32)

    def sense(self) -> np.ndarray:
        """Vector sensorial compacto (dimension 11)."""
        f_d, f_dir = self._nearest(self.food)
        t_d, t_dir = self._nearest(self.threat)
        s_d, s_dir = self._nearest(self.safe)

        diag = self.size * np.sqrt(3)
        f_d_norm = 1.0 - min(f_d / diag, 1.0)
        t_d_norm = 1.0 - min(t_d / diag, 1.0)
        s_d_norm = 1.0 - min(s_d / diag, 1.0)

        return np.concatenate([
            [f_d_norm], f_dir,
            [t_d_norm], t_dir,
            [s_d_norm],
            [self.energy, self._boundary_proximity()],
        ]).astype(np.float32)

    def _boundary_proximity(self) -> float:
        half = self.size / 2
        closest = min(half - abs(self.fly_pos[i]) for i in range(3))
        return float(1.0 - max(0.0, closest / half))

    def step(self, action_idx: int) -> float:
        self.steps += 1
        reward = -0.005
        a = ACTIONS[action_idx]

        if a == "avanzar":
            self.fly_pos += self.fly_heading * self.speed
        elif a == "retroceder":
            self.fly_pos -= self.fly_heading * self.speed * 0.7
        elif a == "girar_izq":
            self.fly_heading = _unit(_rot_z(self.fly_heading, +self.turn_rate))
            self.fly_pos += self.fly_heading * self.speed * 0.3
        elif a == "girar_der":
            self.fly_heading = _unit(_rot_z(self.fly_heading, -self.turn_rate))
            self.fly_pos += self.fly_heading * self.speed * 0.3
        elif a == "quieto":
            pass
        elif a == "explorar":
            jitter = self.rng.normal(0, 0.5, size=3).astype(np.float32)
            self.fly_heading = _unit(self.fly_heading + jitter * 0.3)
            self.fly_pos += self.fly_heading * self.speed * 0.7
        elif a == "subir":
            self.fly_pos[2] += self.vertical_step
        elif a == "bajar":
            self.fly_pos[2] -= self.vertical_step

        half = self.size / 2
        for i in range(3):
            if abs(self.fly_pos[i]) > half:
                self.fly_pos[i] = np.sign(self.fly_pos[i]) * half
                reward -= 0.1
                self.fly_heading[i] *= -1

        reward += self._check_encounters()

        self.energy = float(np.clip(self.energy - 0.003, 0.0, 1.0))
        if self.energy <= 0.0:
            reward -= 0.2

        self.trail.append(self.fly_pos.copy())
        return float(reward)

    def _check_encounters(self) -> float:
        reward = 0.0
        catch_r = 1.0

        for i, p in enumerate(self.food):
            if np.linalg.norm(self.fly_pos - p) < catch_r:
                reward += 1.0
                self.energy = min(1.0, self.energy + 0.4)
                self.eaten_food += 1
                self.food[i] = self._spawn_points(1)[0]

        for i, p in enumerate(self.threat):
            if np.linalg.norm(self.fly_pos - p) < catch_r:
                reward -= 1.0
                self.energy = max(0.0, self.energy - 0.2)
                self.hit_threat += 1

        for p in self.safe:
            if np.linalg.norm(self.fly_pos - p) < catch_r * 1.2:
                if self.energy < 0.5:
                    reward += 0.3
                    self.energy = min(1.0, self.energy + 0.05)
                    self.rested_safe += 1

        return reward
