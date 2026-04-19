"""
Simulacion 3D en tiempo real:

  MUNDO 3D  (cubo con comida, peligros y zonas seguras)
      |   sensores
      v
  CONECTOMA FLYWIRE  (pesos fijos biologicos)
      |   actividad motora
      v
  RED EXPANSIVA      (pesos plasticos, aprende por RL)
      |   accion
      v
  MUNDO 3D           (la mosca se mueve, recibe recompensa)

La ventana muestra al mismo tiempo:
  [3D] el mundo con la mosca, su rastro y los objetos
  [R]  recompensa promedio movil
  [P]  % de sinapsis activas en la red plastica
  [W]  magnitud media de los pesos
  [HUD] energia, comida tomada, peligros, accion actual

Uso:
    python live_simulation.py                         # rapido, conectoma submuestreado
    python live_simulation.py --fly-neurons 5000      # mas biologia
    python live_simulation.py --synthetic             # sin datos reales
    python live_simulation.py --fps 20                # limitar velocidad visual
"""
from __future__ import annotations

import argparse
import time
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
import torch
from matplotlib import gridspec
from matplotlib.animation import FuncAnimation
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

from download_connectome import ensure_connectome
from expansive_network import ExpansiveNetwork
from fly_brain import FlyConnectomeBrain
from fly_world import ACTIONS, FlyWorld


def build_sensory_input(world: FlyWorld, n_sensory: int) -> torch.Tensor:
    """Inyecta el vector sensorial del mundo en los canales sensoriales
    del conectoma (con repeticion + ruido para llenar todos los canales)."""
    base = world.sense()
    k = max(1, n_sensory // len(base))
    tiled = np.tile(base, k + 1)[:n_sensory].copy()
    tiled += np.random.normal(0, 0.05, size=n_sensory).astype(np.float32)
    return torch.from_numpy(tiled)


def select_action(logits: torch.Tensor, temperature: float) -> int:
    if temperature <= 1e-6:
        return int(torch.argmax(logits).item())
    probs = torch.softmax(logits / temperature, dim=0)
    return int(torch.multinomial(probs + 1e-9, 1).item())


def run_live(
    fly_neurons: int = 2000,
    hidden: int = 1500,
    world_size: float = 20.0,
    force_synthetic: bool = False,
    target_fps: int = 25,
    device: str = "cpu",
    seed: int = 7,
) -> None:

    torch.manual_seed(seed)
    np.random.seed(seed)

    ensure_connectome(force_synthetic=force_synthetic, n_neurons=5000)

    print(f"Cargando conectoma (muestra {fly_neurons} neuronas) ...")
    t0 = time.time()
    fly_brain = FlyConnectomeBrain(max_neurons=fly_neurons, device=device)
    info = fly_brain.describe()
    print(f"  listo en {time.time()-t0:.1f}s  |  sensoriales={info['n_sensory']}  "
          f"motoras={info['n_motor']}  inhib={info['inhibitory_neurons']}")

    motor_dim = int(fly_brain.motor_mask.sum().item())
    state_dim = motor_dim + 11  # 11 = tamano del vector sensorial externo
    net = ExpansiveNetwork(
        input_size=state_dim, hidden_size=hidden, output_size=len(ACTIONS),
    ).to(device)

    world = FlyWorld(size=world_size, seed=seed)

    reward_hist   = deque(maxlen=2000)
    pop_hist      = deque(maxlen=2000)
    wmean_hist    = deque(maxlen=2000)
    action_hist   = deque(maxlen=500)
    energy_hist   = deque(maxlen=500)

    step_counter = {"n": 0}

    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor("#0b0f14")
    gs = gridspec.GridSpec(
        3, 3, width_ratios=[2.0, 1.0, 1.0], height_ratios=[1, 1, 0.6], figure=fig,
    )

    ax3d = fig.add_subplot(gs[0:3, 0], projection="3d")
    ax_r = fig.add_subplot(gs[0, 1])
    ax_p = fig.add_subplot(gs[0, 2])
    ax_w = fig.add_subplot(gs[1, 1])
    ax_a = fig.add_subplot(gs[1, 2])
    ax_hud = fig.add_subplot(gs[2, 1:])
    ax_hud.axis("off")

    for ax in (ax_r, ax_p, ax_w, ax_a):
        ax.set_facecolor("#11161e")
        for s in ax.spines.values():
            s.set_color("#2a3340")
        ax.tick_params(colors="#9db0c7", labelsize=8)
        ax.title.set_color("#e6edf3")
        ax.xaxis.label.set_color("#9db0c7")
        ax.yaxis.label.set_color("#9db0c7")
        ax.grid(True, alpha=0.15, color="#9db0c7")

    ax3d.set_facecolor("#0b0f14")
    try:
        ax3d.xaxis.pane.fill = False
        ax3d.yaxis.pane.fill = False
        ax3d.zaxis.pane.fill = False
        for axis in (ax3d.xaxis, ax3d.yaxis, ax3d.zaxis):
            axis.pane.set_edgecolor("#1d2632")
            axis.label.set_color("#9db0c7")
        ax3d.tick_params(colors="#6c7a8a", labelsize=7)
    except Exception:
        pass
    ax3d.set_title("Mosca en mundo 3D  (verde=comida  rojo=peligro  azul=seguro)",
                   color="#e6edf3", fontsize=11)

    half = world_size / 2
    ax3d.set_xlim(-half, half)
    ax3d.set_ylim(-half, half)
    ax3d.set_zlim(-half, half)

    food_scat   = ax3d.scatter([], [], [], c="#3ddc84", s=120, marker="o",
                               edgecolors="#0b0f14", linewidths=0.5, label="comida")
    threat_scat = ax3d.scatter([], [], [], c="#ff4d4d", s=130, marker="^",
                               edgecolors="#0b0f14", linewidths=0.5, label="peligro")
    safe_scat   = ax3d.scatter([], [], [], c="#4d9bff", s=110, marker="s",
                               edgecolors="#0b0f14", linewidths=0.5, label="seguro")
    fly_scat    = ax3d.scatter([], [], [], c="#ffd84d", s=90, marker="*",
                               edgecolors="#ffffff", linewidths=0.6)
    trail_line, = ax3d.plot([], [], [], color="#ffd84d", alpha=0.5, linewidth=1.2)
    heading_q   = [None]

    r_line, = ax_r.plot([], [], color="#3ddc84", lw=1.4)
    ax_r.set_title("Recompensa (media movil 50)", fontsize=10)
    ax_r.set_xlim(0, 500); ax_r.set_ylim(-0.2, 1.0)

    p_line, = ax_p.plot([], [], color="#ffd84d", lw=1.4)
    ax_p.set_title("% sinapsis activas (|w|>1e-3)", fontsize=10)
    ax_p.set_xlim(0, 500); ax_p.set_ylim(0, 100)

    w_line, = ax_w.plot([], [], color="#4d9bff", lw=1.4)
    ax_w.set_title("|w| promedio red plastica", fontsize=10)
    ax_w.set_xlim(0, 500); ax_w.set_ylim(1e-5, 1)
    ax_w.set_yscale("log")

    ax_a.set_title("Uso de acciones (ultimas 500)", fontsize=10)
    action_bars = ax_a.bar(
        np.arange(len(ACTIONS)),
        np.zeros(len(ACTIONS)),
        color="#8a7dff", edgecolor="#0b0f14",
    )
    ax_a.set_xticks(np.arange(len(ACTIONS)))
    ax_a.set_xticklabels(ACTIONS, rotation=30, ha="right", fontsize=7,
                         color="#9db0c7")
    ax_a.set_ylim(0, 1.0)

    hud_text = ax_hud.text(
        0.01, 0.95, "", transform=ax_hud.transAxes,
        fontsize=11, color="#e6edf3", family="monospace",
        va="top", ha="left",
    )

    min_dt = 1.0 / max(1, target_fps)
    last_time = [time.time()]
    smoothed_reward = [0.0]

    def step_once(n_steps: int = 1) -> None:
        for _ in range(n_steps):
            with torch.no_grad():
                sensory = build_sensory_input(world, int(fly_brain.sensory_mask.sum().item()))
                activity = fly_brain(sensory, steps=2)
                motor = fly_brain.motor_output(activity).squeeze(0)

            external = torch.from_numpy(world.sense())
            state = torch.cat([motor, external])

            logits = net(state)
            temperature = max(0.15, 0.8 * np.exp(-step_counter["n"] / 4000))
            action_idx = select_action(logits, temperature)

            reward = world.step(action_idx)

            log_prob = torch.log_softmax(logits, dim=0)[action_idx]
            loss = -torch.tensor(reward, device=device) * log_prob
            net.learn(loss)

            reward_hist.append(reward)
            stats = net.connection_stats()
            pop_hist.append(stats["active_pct"])
            wmean_hist.append(stats["mean_abs_weight"])
            action_hist.append(action_idx)
            energy_hist.append(world.energy)
            step_counter["n"] += 1

            smoothed_reward[0] = 0.98 * smoothed_reward[0] + 0.02 * reward

    def update(_frame):
        now = time.time()
        elapsed = now - last_time[0]
        if elapsed < min_dt:
            n_steps = 1
        else:
            n_steps = max(1, int(elapsed / min_dt))
        last_time[0] = now
        step_once(n_steps=min(n_steps, 5))

        food_scat._offsets3d   = (world.food[:, 0], world.food[:, 1], world.food[:, 2])
        threat_scat._offsets3d = (world.threat[:, 0], world.threat[:, 1], world.threat[:, 2])
        safe_scat._offsets3d   = (world.safe[:, 0], world.safe[:, 1], world.safe[:, 2])
        fly_scat._offsets3d    = (world.fly_pos[0:1], world.fly_pos[1:2], world.fly_pos[2:3])

        if len(world.trail) >= 2:
            tr = np.array(world.trail)
            trail_line.set_data(tr[:, 0], tr[:, 1])
            trail_line.set_3d_properties(tr[:, 2])

        if heading_q[0] is not None:
            try:
                heading_q[0].remove()
            except Exception:
                pass
        p = world.fly_pos; h = world.fly_heading * 1.4
        heading_q[0] = ax3d.quiver(
            p[0], p[1], p[2], h[0], h[1], h[2],
            color="#ffd84d", linewidth=1.5, arrow_length_ratio=0.4,
        )

        if len(reward_hist) > 5:
            r = np.array(reward_hist)
            window = min(50, len(r))
            mov = np.convolve(r, np.ones(window) / window, mode="valid")
            xs = np.arange(len(mov))
            r_line.set_data(xs, mov)
            ax_r.set_xlim(max(0, len(mov) - 500), max(500, len(mov)))
            ax_r.set_ylim(float(mov.min()) - 0.1, float(mov.max()) + 0.1)

        if len(pop_hist) > 2:
            p_line.set_data(np.arange(len(pop_hist)), np.array(pop_hist))
            ax_p.set_xlim(max(0, len(pop_hist) - 500), max(500, len(pop_hist)))
            ax_p.set_ylim(0, max(1.0, max(pop_hist) * 1.1))

        if len(wmean_hist) > 2:
            w = np.array(wmean_hist)
            w_line.set_data(np.arange(len(w)), w)
            ax_w.set_xlim(max(0, len(w) - 500), max(500, len(w)))
            ax_w.set_ylim(max(1e-6, w.min() * 0.5), w.max() * 2.0)

        if len(action_hist) > 1:
            counts = np.bincount(list(action_hist), minlength=len(ACTIONS)).astype(float)
            counts = counts / counts.sum()
            for bar, c in zip(action_bars, counts):
                bar.set_height(c)
            ax_a.set_ylim(0, max(0.3, counts.max() * 1.2))

        current_action = ACTIONS[action_hist[-1]] if action_hist else "-"
        stats = net.connection_stats()
        hud = (
            f" pasos        : {step_counter['n']:>7d}\n"
            f" energia      : {'#' * int(world.energy * 20):<20s} {world.energy:.2f}\n"
            f" accion actual: {current_action}\n"
            f" comida       : {world.eaten_food:>4d}   "
            f"peligros: {world.hit_threat:>3d}   "
            f"descansos: {world.rested_safe:>3d}\n"
            f" recompensa   : {smoothed_reward[0]:+.3f} (suavizada)\n"
            f" sinapsis act : {stats['active_pct']:5.2f}%   "
            f"|w| medio: {stats['mean_abs_weight']:.2e}\n"
        )
        hud_text.set_text(hud)

        return []

    print("\nIniciando simulacion en tiempo real (cierra la ventana para terminar).")
    anim = FuncAnimation(fig, update, interval=1000 // target_fps,
                         blit=False, cache_frame_data=False)
    plt.tight_layout()
    plt.show()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--fly-neurons", type=int, default=2000,
                   help="submuestra del conectoma (mas=mas realista, mas lento)")
    p.add_argument("--hidden", type=int, default=1500)
    p.add_argument("--world-size", type=float, default=20.0)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--fps", type=int, default=25)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--seed", type=int, default=7)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_live(
        fly_neurons=args.fly_neurons,
        hidden=args.hidden,
        world_size=args.world_size,
        force_synthetic=args.synthetic,
        target_fps=args.fps,
        device=args.device,
        seed=args.seed,
    )
