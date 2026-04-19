"""
Simulacion sin renderizado 3D. Solo dashboard de estadisticas en la terminal.

Arquitectura:
    NUCLEO REPTILIANO (conectoma FlyWire, pesos CONGELADOS e inmoviles)
        |                                          ^
        | actividad motora                          | sensores
        v                                          |
    RAZONAMIENTO INTERNO (LLM local, pesos CONGELADOS; solo inferencia)
        |  intenciones abstractas (JSON) + sesgo fijo sobre logits
        v
    CAPA PLASTICA (red en blanco, APRENDE por refuerzo)
        |
        v                ==>    MUNDO 3D FICTICIO (comida / peligro / seguro)
      accion

La mosca aprende sola a:
    - moverse por el espacio
    - buscar comida
    - esquivar peligros
    - refugiarse en zonas seguras cuando tiene baja energia

Uso:
    python stats_simulation.py
    python stats_simulation.py --llm --llm-model gemma3:270m --llm-every 30
    python stats_simulation.py --max-steps 10000 --fly-neurons 5000

Requisito LLM local: instalar Ollama y ejecutar p. ej.:
    ollama pull gemma3:270m
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import torch

from download_connectome import ensure_connectome
from expansive_network import ExpansiveNetwork
from fly_brain import FlyConnectomeBrain
from fly_world import ACTIONS, FlyWorld, FULL_SENSE_DIM
from llm_reasoner import INTENTS, FrozenLLMReasoner


if os.name == "nt":
    os.system("")

ESC = "\033["
CLEAR_ALL = ESC + "2J"
HOME = ESC + "H"
CLR_EOL = ESC + "K"
CLR_EOS = ESC + "J"
HIDE_CURSOR = ESC + "?25l"
SHOW_CURSOR = ESC + "?25h"

BOLD = ESC + "1m"
DIM = ESC + "2m"
RESET = ESC + "0m"
FG_GREEN = ESC + "38;5;46m"
FG_RED = ESC + "38;5;196m"
FG_YELLOW = ESC + "38;5;226m"
FG_BLUE = ESC + "38;5;39m"
FG_MAGENTA = ESC + "38;5;207m"
FG_CYAN = ESC + "38;5;51m"
FG_WHITE = ESC + "38;5;255m"
FG_GRAY = ESC + "38;5;244m"
FG_DARK = ESC + "38;5;238m"


def bar(value: float, total: float, width: int = 20,
        full: str = "#", empty: str = ".") -> str:
    if total <= 0:
        return empty * width
    filled = max(0, min(width, int(round(width * value / total))))
    return full * filled + empty * (width - filled)


def color_reward(r: float) -> str:
    if r > 0.05: return FG_GREEN
    if r < -0.05: return FG_RED
    return FG_GRAY


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def build_sensory(world: FlyWorld, n_sensory: int, rng: np.random.Generator) -> torch.Tensor:
    base = world.sense()
    k = max(1, n_sensory // len(base))
    tiled = np.tile(base, k + 1)[:n_sensory].copy()
    tiled += rng.normal(0, 0.04, size=n_sensory).astype(np.float32)
    return torch.from_numpy(tiled)


def select_action(logits: torch.Tensor, temperature: float,
                  rng: np.random.Generator) -> int:
    if temperature <= 1e-6:
        return int(torch.argmax(logits).item())
    probs = torch.softmax(logits / temperature, dim=0)
    p = probs.detach().cpu().numpy().astype(np.float64)
    p = p / p.sum()
    return int(rng.choice(len(p), p=p))


def verify_frozen_core(fly_brain: FlyConnectomeBrain) -> dict:
    """Certifica que el nucleo reptiliano NO puede ser modificado por el aprendizaje."""
    trainable = sum(p.numel() for p in fly_brain.parameters() if p.requires_grad)
    total_weights = fly_brain.W.numel()
    return {
        "trainable_params": trainable,
        "frozen_synapses": total_weights,
        "requires_grad_W": fly_brain.W.requires_grad,
        "is_parameter": any(fly_brain.W is p for p in fly_brain.parameters()),
        "sign_fingerprint": float(fly_brain.W.abs().sum().item()),
    }


def render_dashboard(
    *,
    step: int,
    elapsed: float,
    fps: float,
    world: FlyWorld,
    fly_info: dict,
    frozen_info: dict,
    initial_fingerprint: float,
    net_stats: dict,
    reward_hist: deque,
    action_hist: deque,
    food_rate_hist: deque,
    threat_rate_hist: deque,
    safe_rate_hist: deque,
    last_action: int,
    temperature: float,
    llm_panel: dict | None = None,
) -> str:
    rewards = list(reward_hist)
    r_inst = rewards[-1] if rewards else 0.0
    r_last_100 = float(np.mean(rewards[-100:])) if len(rewards) >= 5 else 0.0
    r_last_1000 = float(np.mean(rewards[-1000:])) if len(rewards) >= 5 else 0.0
    r_total = float(np.mean(rewards)) if rewards else 0.0

    action_counts = np.bincount(list(action_hist) or [0],
                                minlength=len(ACTIONS)).astype(float)
    action_total = max(action_counts.sum(), 1.0)
    action_pct = action_counts / action_total

    def rate_trend(hist: deque) -> str:
        h = list(hist)
        if len(h) < 20:
            return FG_GRAY + "..." + RESET
        half = len(h) // 2
        a = np.mean(h[:half])
        b = np.mean(h[half:])
        if a < 0.01 and b < 0.01:
            return FG_GRAY + "   " + RESET
        if b > a * 1.1:
            return FG_GREEN + "+ +" + RESET
        if b < a * 0.9:
            return FG_RED + "- -" + RESET
        return FG_YELLOW + " = " + RESET

    food_per_100 = sum(food_rate_hist) * (100.0 / max(1, len(food_rate_hist)))
    threat_per_100 = sum(threat_rate_hist) * (100.0 / max(1, len(threat_rate_hist)))
    safe_per_100 = sum(safe_rate_hist) * (100.0 / max(1, len(safe_rate_hist)))

    drift = frozen_info["sign_fingerprint"] - initial_fingerprint

    out = []
    out.append(HOME + CLR_EOS)
    out.append(BOLD + FG_CYAN +
               "=" * 78 + RESET + "\n")
    title = "  MOSCA HIBRIDA :: REPTIL (FIJO) + LLM RAZONADOR (FIJO) + CAPA PLASTICA"
    if not (llm_panel and llm_panel.get("enabled")):
        title = "  MOSCA HIBRIDA :: CEREBRO REPTILIANO (FIJO) + CAPA PLASTICA"
    out.append(BOLD + FG_WHITE + title + RESET + "\n")
    out.append(BOLD + FG_CYAN +
               "=" * 78 + RESET + "\n")

    out.append(f"  sesion: {format_time(elapsed)}   "
               f"pasos: {step:>7d}   "
               f"fps: {fps:>5.1f}\n\n")

    out.append(BOLD + FG_YELLOW + "[ NUCLEO REPTILIANO - CONECTOMA FLYWIRE CONGELADO ]" + RESET + "\n")
    out.append(f"  neuronas totales      : {fly_info['n_neurons']:>7,}\n")
    out.append(f"  sinapsis biologicas   : {fly_info['n_synapses_nonzero']:>7,}  "
               f"(excit {fly_info['excitatory_neurons']:,}  inhib {fly_info['inhibitory_neurons']:,})\n")
    out.append(f"  sensoriales           : {fly_info['n_sensory']:>7,}   "
               f"motoras: {fly_info['n_motor']:,}\n")
    frozen_status = FG_GREEN + "SI CONGELADO" + RESET if frozen_info["trainable_params"] == 0 else FG_RED + "FALLO" + RESET
    out.append(f"  parametros entrenables: {frozen_info['trainable_params']}  "
               f"{frozen_status}\n")
    drift_color = FG_GREEN if abs(drift) < 1e-6 else FG_RED
    out.append(f"  huella sinaptica      : {drift_color}{frozen_info['sign_fingerprint']:.6f}"
               f"{RESET}   delta desde inicio: {drift_color}{drift:+.2e}{RESET}\n\n")

    out.append(BOLD + FG_MAGENTA + "[ CAPA PLASTICA - RED EN BLANCO QUE APRENDE ]" + RESET + "\n")
    pop = net_stats["active_pct"]
    out.append(f"  sinapsis activas      : {bar(pop, 100, width=30)} "
               f"{pop:5.2f}%  "
               f"({net_stats['active_synapses']:,}/{net_stats['total_synapses']:,})\n")
    out.append(f"  peso |w| medio        : {net_stats['mean_abs_weight']:.4e}\n")
    out.append(f"  peso |w| maximo       : {net_stats['max_abs_weight']:.4e}\n\n")

    if llm_panel and llm_panel.get("enabled"):
        out.append(BOLD + FG_BLUE + "[ RAZONAMIENTO INTERNO - LLM (PESOS CONGELADOS) ]" + RESET + "\n")
        out.append(f"  modelo                : {llm_panel.get('model', '-')}\n")
        out.append(f"  intencion activa      : {BOLD}{FG_CYAN}{llm_panel.get('intent', '-')}{RESET}  "
                   f"(conf {llm_panel.get('confidence', 0):.2f}  fuente {llm_panel.get('source', '-')})\n")
        out.append(f"  llamadas ok / fallo   : {llm_panel.get('calls_ok', 0)} / {llm_panel.get('calls_fail', 0)}\n")
        snip = str(llm_panel.get("snippet", ""))[:72].replace("\n", " ")
        out.append(f"  ultima salida         : {DIM}{snip}{RESET}\n\n")

    out.append(BOLD + FG_GREEN + "[ MUNDO SIMULADO ]" + RESET + "\n")
    energy_bar = bar(world.energy, 1.0, width=20,
                     full="#", empty=".")
    e_color = FG_GREEN if world.energy > 0.5 else (FG_YELLOW if world.energy > 0.25 else FG_RED)
    out.append(f"  energia mosca         : {e_color}{energy_bar}{RESET} {world.energy:.2f}\n")
    out.append(f"  posicion              : ({world.fly_pos[0]:+5.2f}, "
               f"{world.fly_pos[1]:+5.2f}, {world.fly_pos[2]:+5.2f})\n")
    out.append(f"  accion actual         : {BOLD}{FG_CYAN}{ACTIONS[last_action]:<16s}{RESET}   "
               f"temperatura: {temperature:.2f}\n\n")

    out.append(BOLD + FG_BLUE + "[ DESEMPENO - CONTADORES ABSOLUTOS Y TENDENCIA ]" + RESET + "\n")
    out.append(f"  {FG_GREEN}comida tomada   {RESET}: "
               f"{world.eaten_food:>5d}   "
               f"{food_per_100:5.2f}/100 pasos    tendencia {rate_trend(food_rate_hist)}\n")
    out.append(f"  {FG_RED}peligros hit    {RESET}: "
               f"{world.hit_threat:>5d}   "
               f"{threat_per_100:5.2f}/100 pasos    tendencia {rate_trend(threat_rate_hist)}\n")
    out.append(f"  {FG_BLUE}refugios usados {RESET}: "
               f"{world.rested_safe:>5d}   "
               f"{safe_per_100:5.2f}/100 pasos    tendencia {rate_trend(safe_rate_hist)}\n\n")

    out.append(BOLD + FG_YELLOW + "[ RECOMPENSA ]" + RESET + "\n")
    out.append(f"  instantanea           : {color_reward(r_inst)}{r_inst:+.3f}{RESET}\n")
    out.append(f"  media ultimos 100     : {color_reward(r_last_100)}{r_last_100:+.3f}{RESET}\n")
    out.append(f"  media ultimos 1000    : {color_reward(r_last_1000)}{r_last_1000:+.3f}{RESET}\n")
    out.append(f"  media TOTAL           : {color_reward(r_total)}{r_total:+.3f}{RESET}\n\n")

    out.append(BOLD + FG_WHITE + "[ USO DE ACCIONES (ultimas 500) ]" + RESET + "\n")
    max_pct = max(action_pct.max(), 0.01)
    for i, name in enumerate(ACTIONS):
        p = action_pct[i]
        sel = " <" if i == last_action else "  "
        out.append(f"  {name:<16s} {bar(p, max_pct, width=26)} "
                   f"{p*100:5.1f}%{sel}\n")

    out.append("\n" + DIM + FG_GRAY +
               "  (Ctrl+C para terminar; se mostrara resumen final)" + RESET)

    return "".join(out)


def run(
    max_steps: int = 5000,
    fly_neurons: int = 2000,
    hidden: int = 1500,
    world_size: float = 20.0,
    force_synthetic: bool = False,
    refresh_every: int = 10,
    device: str = "cpu",
    seed: int = 7,
    use_llm: bool = False,
    llm_model: str = "gemma3:270m",
    llm_every: int = 30,
) -> None:

    torch.manual_seed(seed)
    np.random.seed(seed)
    rng = np.random.default_rng(seed)

    ensure_connectome(force_synthetic=force_synthetic, n_neurons=5000)

    print("Cargando nucleo reptiliano (conectoma FlyWire)...")
    t0 = time.time()
    fly_brain = FlyConnectomeBrain(max_neurons=fly_neurons, device=device)
    fly_info = fly_brain.describe()
    print(f"  listo en {time.time()-t0:.1f}s")

    frozen_info = verify_frozen_core(fly_brain)
    initial_fp = frozen_info["sign_fingerprint"]
    print(f"  nucleo congelado: trainable_params={frozen_info['trainable_params']}  "
          f"requires_grad={frozen_info['requires_grad_W']}")
    assert frozen_info["trainable_params"] == 0, "El nucleo NO esta congelado"

    motor_dim = int(fly_brain.motor_mask.sum().item())
    n_intents = len(INTENTS)
    state_dim = motor_dim + FULL_SENSE_DIM + n_intents
    net = ExpansiveNetwork(
        input_size=state_dim, hidden_size=hidden, output_size=len(ACTIONS),
    ).to(device)

    reasoner = FrozenLLMReasoner(model=llm_model, n_actions=len(ACTIONS)) if use_llm else None
    last_intent_vec = np.zeros(n_intents, dtype=np.float32)
    last_llm_bias = np.zeros(len(ACTIONS), dtype=np.float32)

    world = FlyWorld(size=world_size, seed=seed)

    reward_hist = deque(maxlen=10000)
    action_hist = deque(maxlen=500)
    food_rate_hist = deque(maxlen=500)
    threat_rate_hist = deque(maxlen=500)
    safe_rate_hist = deque(maxlen=500)
    _reward_baseline: deque = deque(maxlen=500)

    history_for_final = {
        "reward_smooth": [],
        "population_pct": [],
        "weight_mean": [],
        "food_cumulative": [],
        "threat_cumulative": [],
        "safe_cumulative": [],
    }

    n_sensory = int(fly_brain.sensory_mask.sum().item())
    last_action = 0
    temperature = 1.0

    print(f"\nMundo: cubo {world_size}x{world_size}x{world_size}  "
          f"({len(world.food)} comidas, {len(world.threat)} peligros, "
          f"{len(world.safe)} seguros)")
    print(f"Acciones motoras     : {len(ACTIONS)}  {', '.join(ACTIONS[:4])}...")
    print(f"Capa plastica        : {state_dim} -> {hidden} -> {len(ACTIONS)}")
    if reasoner:
        print(f"LLM razonador (fijo): modelo={llm_model!r}  cada {llm_every} pasos  "
              f"(Ollama; sin entrenamiento en linea)")
    else:
        print("LLM razonador        : desactivado")
    print(f"Iniciando en 1s...")
    time.sleep(1.0)

    sys.stdout.write(CLEAR_ALL + HIDE_CURSOR)
    sys.stdout.flush()

    session_start = time.time()
    last_render = 0.0
    fps_buf: deque = deque(maxlen=50)

    try:
        for step in range(1, max_steps + 1):
            tick = time.time()

            food_before = world.eaten_food
            threat_before = world.hit_threat
            safe_before = world.rested_safe

            if reasoner is not None and (step == 1 or step % llm_every == 0):
                last_intent_vec, last_llm_bias = reasoner.reason(world)

            with torch.no_grad():
                sensory = build_sensory(world, n_sensory, rng)
                activity = fly_brain(sensory, steps=2)
                motor = fly_brain.motor_output(activity).squeeze(0)

            external = torch.from_numpy(world.sense())
            intent_t = torch.from_numpy(last_intent_vec).float()
            state = torch.cat([motor, external, intent_t])

            logits = net(state)
            bias_t = torch.from_numpy(last_llm_bias).to(device=device, dtype=logits.dtype)
            biased_logits = logits + bias_t

            temperature = max(0.8, 1.5 * np.exp(-step / 10000))
            epsilon = max(0.15, 0.6 * np.exp(-step / 5000))
            if rng.random() < epsilon:
                action_idx = int(rng.integers(0, len(ACTIONS)))
            else:
                action_idx = select_action(biased_logits, temperature, rng)
            last_action = action_idx

            reward = world.step(action_idx)

            _reward_baseline.append(reward)
            if len(_reward_baseline) > 50:
                baseline = float(np.mean(_reward_baseline))
                std = float(np.std(_reward_baseline)) + 1e-3
                advantage = float(np.clip((reward - baseline) / std, -2.0, 2.0))
            else:
                advantage = 0.0

            log_probs = torch.log_softmax(biased_logits, dim=0)
            probs = torch.softmax(biased_logits, dim=0)
            entropy = -(probs * log_probs).sum()
            loss = (-torch.tensor(advantage, device=device) * log_probs[action_idx]
                    - 0.08 * entropy)
            net.learn(loss)

            reward_hist.append(reward)
            action_hist.append(action_idx)
            food_rate_hist.append(world.eaten_food - food_before)
            threat_rate_hist.append(world.hit_threat - threat_before)
            safe_rate_hist.append(world.rested_safe - safe_before)

            tock = time.time()
            fps_buf.append(1.0 / max(1e-6, tock - tick))

            if step % refresh_every == 0 or step == 1:
                current_fp = float(fly_brain.W.abs().sum().item())
                frozen_info["sign_fingerprint"] = current_fp
                net_stats = net.connection_stats()

                history_for_final["reward_smooth"].append(
                    float(np.mean(list(reward_hist)[-100:]))
                )
                history_for_final["population_pct"].append(net_stats["active_pct"])
                history_for_final["weight_mean"].append(net_stats["mean_abs_weight"])
                history_for_final["food_cumulative"].append(world.eaten_food)
                history_for_final["threat_cumulative"].append(world.hit_threat)
                history_for_final["safe_cumulative"].append(world.rested_safe)

                elapsed = tock - session_start
                avg_fps = float(np.mean(fps_buf)) if fps_buf else 0.0

                llm_panel = None
                if reasoner is not None:
                    st = reasoner.state
                    llm_panel = {
                        "enabled": True,
                        "model": llm_model,
                        "intent": st.last_intent,
                        "confidence": st.last_confidence,
                        "source": st.last_source,
                        "calls_ok": st.calls_ok,
                        "calls_fail": st.calls_fail,
                        "snippet": st.last_raw,
                    }

                dash = render_dashboard(
                    step=step, elapsed=elapsed, fps=avg_fps,
                    world=world, fly_info=fly_info,
                    frozen_info=frozen_info, initial_fingerprint=initial_fp,
                    net_stats=net_stats, reward_hist=reward_hist,
                    action_hist=action_hist,
                    food_rate_hist=food_rate_hist,
                    threat_rate_hist=threat_rate_hist,
                    safe_rate_hist=safe_rate_hist,
                    last_action=last_action, temperature=temperature,
                    llm_panel=llm_panel,
                )
                sys.stdout.write(dash)
                sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + "\n")
        sys.stdout.flush()

    final_fp = float(fly_brain.W.abs().sum().item())
    drift = final_fp - initial_fp

    print("\n" + "=" * 78)
    print(f"{BOLD}RESUMEN FINAL DE LA SESION{RESET}")
    print("=" * 78)
    print(f"  pasos totales        : {world.steps:,}")
    print(f"  comida tomada        : {world.eaten_food}   "
          f"({100.0 * world.eaten_food / max(1, world.steps):.2f} / 100 pasos)")
    print(f"  peligros golpeados   : {world.hit_threat}   "
          f"({100.0 * world.hit_threat / max(1, world.steps):.2f} / 100 pasos)")
    print(f"  refugios usados      : {world.rested_safe}   "
          f"({100.0 * world.rested_safe / max(1, world.steps):.2f} / 100 pasos)")
    print(f"  recompensa media     : {np.mean(reward_hist):+.3f}")
    net_stats = net.connection_stats()
    print(f"  sinapsis plasticas   : {net_stats['active_pct']:.2f}% activas")
    print(f"  peso plastico medio  : {net_stats['mean_abs_weight']:.4e}")
    print(f"  peso plastico max    : {net_stats['max_abs_weight']:.4e}")
    print(f"  nucleo reptiliano    : drift sinaptico = {drift:+.2e}  "
          f"{'[INMOVIL, OK]' if abs(drift) < 1e-6 else '[ALTERADO]'}")
    if reasoner is not None:
        st = reasoner.state
        print(f"  LLM (sin entrenar)   : intent={st.last_intent!r}  "
              f"llamadas ok={st.calls_ok}  fallos={st.calls_fail}")

    first_q = max(1, len(reward_hist) // 4)
    inicial = np.mean(list(reward_hist)[:first_q])
    final = np.mean(list(reward_hist)[-first_q:])
    print(f"\n  {BOLD}aprendizaje:{RESET} recompensa primer cuarto {inicial:+.3f}  "
          f"-> ultimo cuarto {final:+.3f}  "
          f"({(final-inicial)/max(abs(inicial),1e-3)*100:+.0f}%)")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 3, figsize=(15, 8))
        fig.suptitle("Evolucion del aprendizaje de la mosca (sesion completa)",
                     fontsize=13)
        h = history_for_final
        x = np.arange(len(h["reward_smooth"])) * refresh_every

        axes[0, 0].plot(x, h["reward_smooth"], color="seagreen"); axes[0, 0].set_title("recompensa (media 100)")
        axes[0, 1].plot(x, h["population_pct"], color="orange"); axes[0, 1].set_title("% sinapsis plasticas activas")
        axes[0, 2].plot(x, h["weight_mean"], color="steelblue"); axes[0, 2].set_title("|w| medio"); axes[0, 2].set_yscale("log")
        axes[1, 0].plot(x, h["food_cumulative"], color="green"); axes[1, 0].set_title("comida acumulada")
        axes[1, 1].plot(x, h["threat_cumulative"], color="red"); axes[1, 1].set_title("peligros acumulados")
        axes[1, 2].plot(x, h["safe_cumulative"], color="royalblue"); axes[1, 2].set_title("refugios acumulados")
        for ax in axes.ravel():
            ax.set_xlabel("pasos"); ax.grid(alpha=0.3)
        plt.tight_layout()
        out_path = Path(__file__).parent / "session_results.png"
        plt.savefig(out_path, dpi=110)
        print(f"\n  graficas de la sesion guardadas en: {out_path}")
    except Exception as exc:
        print(f"  (no se pudo guardar grafica: {exc})")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--max-steps", type=int, default=5000)
    p.add_argument("--fly-neurons", type=int, default=2000)
    p.add_argument("--hidden", type=int, default=1500)
    p.add_argument("--world-size", type=float, default=20.0)
    p.add_argument("--refresh-every", type=int, default=10)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--llm", action="store_true",
                   help="activar razonamiento interno via Ollama (pesos del LLM congelados)")
    p.add_argument("--llm-model", default="gemma3:270m",
                   help="modelo Ollama pequeno, ej: gemma3:270m, gemma2:2b, gemma2:1b")
    p.add_argument("--llm-every", type=int, default=30,
                   help="cada cuantos pasos se reconsulta el LLM")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        max_steps=args.max_steps,
        fly_neurons=args.fly_neurons,
        hidden=args.hidden,
        world_size=args.world_size,
        force_synthetic=args.synthetic,
        refresh_every=args.refresh_every,
        device=args.device,
        seed=args.seed,
        use_llm=args.llm,
        llm_model=args.llm_model,
        llm_every=args.llm_every,
    )
