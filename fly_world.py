"""
Mundo 3D procedural para la mosca.

Cubo con comida, peligros y zonas seguras. Acciones motoras extendidas
(avance, giro, laterales, sprint, olfato, refugio activo, huida, memoria
de trabajo rudimentaria, planeacion lenta).

RECOMPENSAS:
  - densa  : acercarse a comida / alejarse de peligro / refugio si hambre
  - evento : tocar items
  - olfatear: activa un impulso temporal de 'olfato' (refuerza gradiente hacia comida)
"""
from __future__ import annotations

from collections import deque

import numpy as np

ACTIONS = [
    "avanzar",
    "girar_izq",
    "girar_der",
    "retroceder",
    "quieto",
    "explorar",
    "subir",
    "bajar",
    "sprint",
    "lateral_izq",
    "lateral_der",
    "olfatear",
    "refugiarse",
    "huir",
    "memorizar",
    "planear",
]


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def _rot_z(v: np.ndarray, angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2]], dtype=np.float32)


def _lateral_basis(heading: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    up = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    right = np.cross(heading, up)
    nr = float(np.linalg.norm(right))
    if nr < 1e-3:
        right = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    else:
        right = (right / nr).astype(np.float32)
    left = np.cross(up, heading)
    nl = float(np.linalg.norm(left))
    if nl < 1e-3:
        left = -right
    else:
        left = (left / nl).astype(np.float32)
    return left, right


class FlyWorld:
    def __init__(
        self,
        size: float = 20.0,
        n_food: int = 10,
        n_threat: int = 4,
        n_safe: int = 3,
        seed: int = 0,
    ):
        self.size = size
        self.rng = np.random.default_rng(seed)

        self.food = self._spawn_points(n_food)
        self.threat = self._spawn_points(n_threat)
        self.safe = self._spawn_points(n_safe)

        self.fly_pos = np.array([0.0, 0.0, size / 2], dtype=np.float32)
        self.fly_heading = _unit(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        self.energy = 1.0

        self.trail: deque = deque(maxlen=200)
        self.trail.append(self.fly_pos.copy())

        self.eaten_food = 0
        self.hit_threat = 0
        self.rested_safe = 0
        self.steps = 0
        self.memory_ticks = 0

        self.speed = 0.35 * (size / 20.0)
        self.turn_rate = 0.45
        self.vertical_step = 0.3 * (size / 20.0)
        self.catch_radius = 1.5
        self.scent_boost_ticks = 0

        self._prev_food_dist = self._min_dist_to(self.food)
        self._prev_threat_dist = self._min_dist_to(self.threat)
        self._prev_safe_dist = self._min_dist_to(self.safe)

    def _spawn_points(self, n: int) -> np.ndarray:
        half = self.size / 2
        return self.rng.uniform(-half, half, size=(n, 3)).astype(np.float32)

    def _min_dist_to(self, items: np.ndarray) -> float:
        if len(items) == 0:
            return self.size
        return float(np.linalg.norm(items - self.fly_pos, axis=1).min())

    def _nearest_point(self, items: np.ndarray) -> np.ndarray:
        if len(items) == 0:
            return self.fly_pos.copy()
        d = items - self.fly_pos
        dist = np.linalg.norm(d, axis=1)
        i = int(np.argmin(dist))
        return items[i].copy()

    def _nearest(self, items: np.ndarray) -> tuple[float, np.ndarray]:
        if len(items) == 0:
            return self.size, np.zeros(3, dtype=np.float32)
        d = items - self.fly_pos
        dist = np.linalg.norm(d, axis=1)
        i = int(np.argmin(dist))
        return float(dist[i]), _unit(d[i]).astype(np.float32)

    def sense(self) -> np.ndarray:
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
        reward = 0.0
        a = ACTIONS[action_idx]
        left, right = _lateral_basis(self.fly_heading)

        if a == "avanzar":
            self.fly_pos += self.fly_heading * self.speed
        elif a == "retroceder":
            self.fly_pos -= self.fly_heading * self.speed * 0.6
        elif a == "girar_izq":
            self.fly_heading = _unit(_rot_z(self.fly_heading, +self.turn_rate))
            self.fly_pos += self.fly_heading * self.speed * 0.25
        elif a == "girar_der":
            self.fly_heading = _unit(_rot_z(self.fly_heading, -self.turn_rate))
            self.fly_pos += self.fly_heading * self.speed * 0.25
        elif a == "quieto":
            reward -= 0.04
        elif a == "explorar":
            jitter = self.rng.normal(0, 0.5, size=3).astype(np.float32)
            self.fly_heading = _unit(self.fly_heading + jitter * 0.3)
            self.fly_pos += self.fly_heading * self.speed * 0.7
        elif a == "subir":
            if self.fly_pos[2] < self.size / 2 - 0.1:
                self.fly_pos[2] += self.vertical_step
            else:
                reward -= 0.1
        elif a == "bajar":
            if self.fly_pos[2] > -self.size / 2 + 0.1:
                self.fly_pos[2] -= self.vertical_step
            else:
                reward -= 0.1
        elif a == "sprint":
            self.fly_pos += self.fly_heading * self.speed * 1.65
            self.energy = max(0.0, self.energy - 0.012)
        elif a == "lateral_izq":
            self.fly_pos += left * self.speed * 0.85
        elif a == "lateral_der":
            self.fly_pos += right * self.speed * 0.85
        elif a == "olfatear":
            reward -= 0.02
            self.scent_boost_ticks = max(self.scent_boost_ticks, 10)
        elif a == "refugiarse":
            target = self._nearest_point(self.safe) - self.fly_pos
            n = float(np.linalg.norm(target))
            if n > 1e-3:
                self.fly_pos += (target / n) * self.speed * 0.55
        elif a == "huir":
            tvec = self._nearest_point(self.threat) - self.fly_pos
            n = float(np.linalg.norm(tvec))
            if n > 1e-3:
                self.fly_pos -= (tvec / n) * self.speed * 0.75
        elif a == "memorizar":
            reward -= 0.015
            self.memory_ticks = min(50, self.memory_ticks + 6)
        elif a == "planear":
            wobble = self.rng.normal(0, 0.12, size=3).astype(np.float32)
            self.fly_heading = _unit(self.fly_heading + wobble)
            self.fly_pos += self.fly_heading * self.speed * 0.18
            reward -= 0.01

        half = self.size / 2
        for i in range(3):
            if abs(self.fly_pos[i]) > half:
                self.fly_pos[i] = np.sign(self.fly_pos[i]) * half
                reward -= 0.2
                self.fly_heading[i] *= -1

        if len(self.trail) >= 5:
            disp = float(np.linalg.norm(self.fly_pos - self.trail[-5]))
            if disp < 0.3:
                reward -= 0.05

        food_d = self._min_dist_to(self.food)
        threat_d = self._min_dist_to(self.threat)
        safe_d = self._min_dist_to(self.safe)

        scent = 1.45 if self.scent_boost_ticks > 0 else 1.0
        if self.scent_boost_ticks > 0:
            self.scent_boost_ticks -= 1

        mem = 1.08 if self.memory_ticks > 0 else 1.0
        if self.memory_ticks > 0:
            self.memory_ticks -= 1

        reward += 0.25 * scent * mem * (self._prev_food_dist - food_d)
        reward -= 0.15 * (self._prev_threat_dist - threat_d)
        if self.energy < 0.5:
            reward += 0.15 * (self._prev_safe_dist - safe_d)

        self._prev_food_dist = food_d
        self._prev_threat_dist = threat_d
        self._prev_safe_dist = safe_d

        reward += self._check_encounters()

        self.energy = float(np.clip(self.energy - 0.002, 0.0, 1.0))
        if self.energy <= 0.0:
            reward -= 0.1

        self.trail.append(self.fly_pos.copy())
        return float(reward)

    def _check_encounters(self) -> float:
        reward = 0.0
        for i, p in enumerate(self.food):
            if np.linalg.norm(self.fly_pos - p) < self.catch_radius:
                reward += 5.0
                self.energy = min(1.0, self.energy + 0.5)
                self.eaten_food += 1
                self.food[i] = self._spawn_points(1)[0]
                self._prev_food_dist = self._min_dist_to(self.food)

        for i, p in enumerate(self.threat):
            if np.linalg.norm(self.fly_pos - p) < self.catch_radius:
                reward -= 2.0
                self.energy = max(0.0, self.energy - 0.2)
                self.hit_threat += 1

        for p in self.safe:
            if np.linalg.norm(self.fly_pos - p) < self.catch_radius * 1.3:
                if self.energy < 0.6:
                    reward += 1.0
                    self.energy = min(1.0, self.energy + 0.08)
                    self.rested_safe += 1

        return reward
