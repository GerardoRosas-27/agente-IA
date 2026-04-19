"""
Experimento: una mosca con INSTINTOS reales (conectoma FlyWire congelado)
mas una RED EXPANSIVA EN BLANCO que puede aprender y poblar nuevas
conexiones sinapticas a lo largo de la vida del individuo.

Pregunta cientifica:
    Dado un cerebro base biologicamente realista y congelado, ¿puede una
    red plastica adjunta descubrir por refuerzo comportamientos utiles
    y, al hacerlo, "poblar" sinapsis que inicialmente estaban vacias?

Uso:
    python experiment.py                      # corrida estandar
    python experiment.py --episodes 500 --hidden 3000
    python experiment.py --no-llm             # sin sugerencias de Ollama
    python experiment.py --synthetic          # fuerza conectoma sintetico
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch

from download_connectome import ensure_connectome
from expansive_network import ExpansiveNetwork
from fly_brain import FlyConnectomeBrain

CONCEPTS = [
    "comida", "peligro", "hambre", "seguro",
    "explorar", "estrategia", "futuro", "pareja",
]
ACTIONS = [
    "avanzar", "girar_izq", "girar_der", "retroceder",
    "quieto", "explorar", "planear", "reproducir",
]

REWARD_TABLE = {
    "comida":     {"avanzar": 1.2, "explorar": 0.4},
    "peligro":    {"girar_izq": 1.0, "girar_der": 1.0, "retroceder": 1.0},
    "hambre":     {"avanzar": 0.9, "explorar": 0.7},
    "seguro":     {"quieto": 0.8, "explorar": 0.6},
    "explorar":   {"explorar": 1.1, "planear": 0.6, "avanzar": 0.3},
    "estrategia": {"planear": 1.2, "explorar": 0.5},
    "futuro":     {"planear": 1.1, "explorar": 0.6},
    "pareja":     {"reproducir": 1.3, "avanzar": 0.4},
}

def reward_for(concept: str, action_idx: int) -> float:
    action = ACTIONS[action_idx]
    table = REWARD_TABLE.get(concept, {})
    return table.get(action, -0.3)


def maybe_llm_hint(concept: str, use_llm: bool) -> str:
    if not use_llm:
        return ""
    try:
        import ollama
        prompt = (
            f"Una mosca percibe el estimulo '{concept}'. Responde con UNA "
            f"sola palabra de esta lista: {', '.join(ACTIONS)}."
        )
        resp = ollama.chat(model="phi3", messages=[{"role": "user", "content": prompt}])
        return resp["message"]["content"].strip().lower()
    except Exception:
        return ""


def run(
    episodes: int = 300,
    steps_per_episode: int = 40,
    expansive_hidden: int = 2000,
    max_fly_neurons: int = 2500,
    use_llm: bool = True,
    force_synthetic: bool = False,
    device: str = "cpu",
    seed: int = 42,
) -> None:

    torch.manual_seed(seed)
    np.random.seed(seed)

    ensure_connectome(force_synthetic=force_synthetic, n_neurons=5000)

    print("\nCargando conectoma de la mosca como red fija...")
    t0 = time.time()
    fly = FlyConnectomeBrain(max_neurons=max_fly_neurons, device=device)
    info = fly.describe()
    print(f"  cargado en {time.time()-t0:.1f}s")
    for k, v in info.items():
        print(f"    {k}: {v:,}" if isinstance(v, int) else f"    {k}: {v}")

    motor_dim = int(fly.motor_mask.sum().item())
    state_dim = motor_dim + len(CONCEPTS)
    print(f"\nRed expansiva en blanco:")
    print(f"  entrada : {state_dim}")
    print(f"  oculta  : {expansive_hidden}")
    print(f"  salida  : {len(ACTIONS)}")

    brain_plastic = ExpansiveNetwork(
        input_size=state_dim,
        hidden_size=expansive_hidden,
        output_size=len(ACTIONS),
    ).to(device)

    init = brain_plastic.connection_stats()
    print(
        f"  estado inicial: {init['active_pct']:.3f}% sinapsis activas "
        f"(|w|>1e-3), peso medio {init['mean_abs_weight']:.2e}\n"
    )

    reward_hist, weight_hist, pop_hist, llm_hist = [], [], [], []

    n_sensory = int(fly.sensory_mask.sum().item())

    print("Iniciando simulacion...\n")
    for ep in range(episodes):
        total_reward = 0.0
        llm_calls = 0

        for _ in range(steps_per_episode):
            sensory = (torch.rand(n_sensory, device=device) * 2 - 1) * 0.5
            concept_idx = int(np.random.randint(0, len(CONCEPTS)))
            concept = CONCEPTS[concept_idx]

            with torch.no_grad():
                activity = fly(sensory, steps=2)
                motor_activity = fly.motor_output(activity).squeeze(0)

            concept_vec = torch.zeros(len(CONCEPTS), device=device)
            concept_vec[concept_idx] = 1.0
            state = torch.cat([motor_activity, concept_vec])

            logits = brain_plastic(state)

            if np.random.rand() < 0.15:
                hint = maybe_llm_hint(concept, use_llm)
                if hint:
                    llm_calls += 1
                    for i, a in enumerate(ACTIONS):
                        if a in hint:
                            logits = logits.clone()
                            logits[i] = logits[i] + 0.3
                            break

            probs = torch.softmax(logits, dim=0)
            if np.random.rand() < max(0.05, 0.3 * (1 - ep / episodes)):
                action_idx = int(torch.multinomial(probs + 1e-6, 1).item())
            else:
                action_idx = int(torch.argmax(logits).item())

            r = reward_for(concept, action_idx)
            total_reward += r

            log_prob = torch.log_softmax(logits, dim=0)[action_idx]
            loss = -torch.tensor(r, device=device) * log_prob
            brain_plastic.learn(loss)

        stats = brain_plastic.connection_stats()
        avg_r = total_reward / steps_per_episode
        reward_hist.append(avg_r)
        weight_hist.append(stats["mean_abs_weight"])
        pop_hist.append(stats["active_pct"])
        llm_hist.append(llm_calls)

        if ep == 0 or (ep + 1) % max(1, episodes // 15) == 0:
            print(
                f"  ep {ep+1:4d}/{episodes}  "
                f"R={avg_r:+.3f}  "
                f"|w|avg={stats['mean_abs_weight']:.2e}  "
                f"|w|max={stats['max_abs_weight']:.2e}  "
                f"pobl={stats['active_pct']:5.2f}%  "
                f"LLM={llm_calls}"
            )

    final = brain_plastic.connection_stats()
    print("\nSimulacion terminada.")
    print(f"  recompensa final (ult. 10 ep): {np.mean(reward_hist[-10:]):+.3f}")
    print(f"  sinapsis activas finales     : {final['active_pct']:.2f}% "
          f"({final['active_synapses']:,}/{final['total_synapses']:,})")
    print(f"  peso absoluto medio final    : {final['mean_abs_weight']:.4e}")
    print(f"  peso absoluto maximo final   : {final['max_abs_weight']:.4e}")

    plot_results(reward_hist, weight_hist, pop_hist, brain_plastic)


def plot_results(rewards, weights, population, net: ExpansiveNetwork) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))

    ax = axes[0, 0]
    ax.plot(rewards, color="steelblue")
    ax.plot(np.convolve(rewards, np.ones(10)/10, mode="valid"), color="crimson",
            label="media movil (10)")
    ax.set_title("Recompensa promedio por episodio")
    ax.set_xlabel("episodio"); ax.set_ylabel("recompensa")
    ax.grid(alpha=0.3); ax.legend()

    ax = axes[0, 1]
    ax.plot(population, color="darkorange")
    ax.set_title("% de sinapsis activas en la red expansiva")
    ax.set_xlabel("episodio"); ax.set_ylabel("% |w| > 1e-3")
    ax.grid(alpha=0.3)

    ax = axes[1, 0]
    ax.plot(weights, color="seagreen")
    ax.set_title("Magnitud promedio de los pesos (|w|)")
    ax.set_xlabel("episodio"); ax.set_ylabel("|w| medio")
    ax.set_yscale("log"); ax.grid(alpha=0.3, which="both")

    ax = axes[1, 1]
    with torch.no_grad():
        w = net.fc1.weight.flatten().cpu().numpy()
    ax.hist(w, bins=80, color="slateblue", alpha=0.8)
    ax.set_title("Distribucion final de pesos fc1")
    ax.set_xlabel("peso"); ax.set_ylabel("frecuencia")
    ax.set_yscale("log"); ax.grid(alpha=0.3)

    plt.tight_layout()
    out = Path(__file__).parent / "results.png"
    plt.savefig(out, dpi=110)
    print(f"\nGrafica guardada en {out}")
    try:
        plt.show()
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--episodes", type=int, default=300)
    p.add_argument("--steps", type=int, default=40)
    p.add_argument("--hidden", type=int, default=2000)
    p.add_argument("--fly-neurons", type=int, default=2500,
                   help="submuestrear el conectoma para acelerar")
    p.add_argument("--no-llm", action="store_true",
                   help="desactivar sugerencias de Ollama")
    p.add_argument("--synthetic", action="store_true",
                   help="forzar conectoma sintetico (no descargar)")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        episodes=args.episodes,
        steps_per_episode=args.steps,
        expansive_hidden=args.hidden,
        max_fly_neurons=args.fly_neurons,
        use_llm=not args.no_llm,
        force_synthetic=args.synthetic,
        device=args.device,
        seed=args.seed,
    )
