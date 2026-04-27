"""
Sesion de simulacion + aprendizaje para el chat: nucleo fijo, plastico vivo.

La red plastica tiene cabezales extra:
  - distribucion sobre un lexicon de TEMAS (pregunta aprendible: que indice)
  - vector continuo de INSTRUCCION al LLM (matices aprendidos)

Cuando la mosca pregunta, se muestrea el indice con REINFORCE pendiente.
Al llegar tu siguiente mensaje, el puente clasifica tono -> recompensa R
y se actualiza la politica de preguntas (y el resto de pesos compartidos).
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Callable

import numpy as np
import torch

from chat_bridge import BridgeResult, LanguageBridge
from download_connectome import ensure_connectome
from llm_api_client import get_resolved_model, remote_openai_chat
from expansive_network import ExpansiveNetwork
from fly_brain import FlyConnectomeBrain
from fly_question_llm import QUESTION_LEXICON, generate_fly_question
from fly_voice import brain_utterance
from fly_world import ACTIONS, FlyWorld, FULL_SENSE_DIM
from llm_reasoner import INTENTS


def build_sensory(world: FlyWorld, n_sensory: int, rng: np.random.Generator) -> torch.Tensor:
    base = world.sense()
    k = max(1, n_sensory // len(base))
    tiled = np.tile(base, k + 1)[:n_sensory].copy()
    tiled += rng.normal(0, 0.04, size=n_sensory).astype(np.float32)
    return torch.from_numpy(tiled)


def select_action(logits: torch.Tensor, temperature: float, rng: np.random.Generator) -> int:
    if temperature <= 1e-6:
        return int(torch.argmax(logits).item())
    probs = torch.softmax(logits / temperature, dim=0)
    p = probs.detach().cpu().numpy().astype(np.float64)
    p = p / p.sum()
    return int(rng.choice(len(p), p=p))


class FlyChatSession:
    INSTRUCTION_DIM = 28

    def __init__(
        self,
        fly_neurons: int = 1500,
        hidden: int = 3200,
        device: str = "cpu",
        seed: int = 11,
        world_size: float = 20.0,
        llm_bridge_model: str = "",
        force_synthetic: bool = False,
        blank_fly_brain: bool = False,
        fly_lr: float = 3e-5,
    ):
        torch.manual_seed(seed)
        np.random.seed(seed)
        self.rng = np.random.default_rng(seed)
        self.device = device
        self.llm_model = get_resolved_model(llm_bridge_model)

        ensure_connectome(force_synthetic=force_synthetic, n_neurons=5000)
        self.fly = FlyConnectomeBrain(
            max_neurons=fly_neurons,
            device=device,
            blank_learnable=blank_fly_brain,
            fly_lr=fly_lr,
        )
        self.motor_dim = int(self.fly.motor_mask.sum().item())
        self.n_sensory = int(self.fly.sensory_mask.sum().item())

        self.n_intents = len(INTENTS)
        self.state_dim = self.motor_dim + FULL_SENSE_DIM + self.n_intents
        self.vocab_q = len(QUESTION_LEXICON)
        self.net = ExpansiveNetwork(
            input_size=self.state_dim,
            hidden_size=hidden,
            output_size=len(ACTIONS),
            question_vocab_size=self.vocab_q,
            instruction_dim=self.INSTRUCTION_DIM,
        ).to(device)

        self.world = FlyWorld(size=world_size, seed=seed)
        self.bridge = LanguageBridge(model=self.llm_model)
        self._llm_chat = remote_openai_chat

        self.intent_vec = np.zeros(self.n_intents, dtype=np.float32)
        self.last_action = 0
        self.reward_ema = 0.0
        self._reward_baseline: deque = deque(maxlen=400)
        self.sim_steps = 0

        self.pending_brain_reply = -1
        self.curiosity = 0.28
        self.steps_since_user = 0

        self._last_plastic_state: torch.Tensor | None = None
        self.pending_q_learning: dict | None = None
        self._question_busy = False
        self.question_queue: deque[str] = deque()

    @property
    def llm_chat(self) -> Callable[..., object]:
        return self._llm_chat

    def drain_question_queue(self) -> list[str]:
        out = []
        while self.question_queue:
            out.append(self.question_queue.popleft())
        return out

    @staticmethod
    def _interaction_reward(br: BridgeResult) -> float:
        s = br.scores
        R = (
            0.52 * s["amable"]
            + 0.42 * s["conversacion"]
            - 0.68 * s["hostil"]
            + 0.12 * s["curiosidad"]
            - 0.22
        )
        return float(np.clip(R, -1.0, 1.0))

    def apply_bridge_result(self, br: BridgeResult) -> tuple[str, str]:
        if self.pending_q_learning is not None:
            R = self._interaction_reward(br)
            st = self.pending_q_learning["state"]
            qi = self.pending_q_learning["q_idx"]
            self.net.learn_question_policy(st, qi, R)
            self.pending_q_learning = None

        self.bridge.apply_to_world(self.world, br)
        self.curiosity = max(0.0, self.curiosity - 0.18)
        self.steps_since_user = 0
        self.pending_brain_reply = 28
        return br.source, br.raw_snippet

    def classify_only(self, text: str) -> BridgeResult:
        return self.bridge.classify(text)

    def step(self, n: int = 6) -> None:
        for _ in range(n):
            self._single_step()

    def _single_step(self) -> None:
        self.sim_steps += 1
        self.steps_since_user += 1

        sensory = build_sensory(self.world, self.n_sensory, self.rng)
        fly_grad = getattr(self.fly, "blank_learnable", False)
        if fly_grad:
            activity = self.fly(sensory, steps=2, enable_grad=True)
        else:
            with torch.no_grad():
                activity = self.fly(sensory, steps=2, enable_grad=False)
        motor = self.fly.motor_output(activity).squeeze(0)

        ext = torch.from_numpy(self.world.sense()).float()
        intent_t = torch.from_numpy(self.intent_vec).float()
        state = torch.cat([motor, ext, intent_t]).to(self.device)

        self._last_plastic_state = state.detach().cpu().clone()

        motor_logits, _q, _inst = self.net.forward_heads(state)
        temp = max(0.75, 1.2 * np.exp(-self.sim_steps / 12000))
        eps = max(0.12, 0.45 * np.exp(-self.sim_steps / 6000))
        if self.rng.random() < eps:
            action_idx = int(self.rng.integers(0, len(ACTIONS)))
        else:
            action_idx = select_action(motor_logits, temp, self.rng)
        self.last_action = action_idx

        reward = self.world.step(action_idx)
        self.reward_ema = 0.97 * self.reward_ema + 0.03 * reward

        self._reward_baseline.append(reward)
        if len(self._reward_baseline) > 40:
            baseline = float(np.mean(self._reward_baseline))
            std = float(np.std(self._reward_baseline)) + 1e-3
            advantage = float(np.clip((reward - baseline) / std, -2.0, 2.0))
        else:
            advantage = 0.0

        log_probs = torch.log_softmax(motor_logits, dim=0)
        probs = torch.softmax(motor_logits, dim=0)
        entropy = -(probs * log_probs).sum()
        loss = (
            -torch.tensor(advantage, device=self.device, dtype=motor_logits.dtype)
            * log_probs[action_idx]
            - 0.07 * entropy
        )
        if fly_grad:
            fe_loss, self.net.last_free_energy_stats = self.net.free_energy_regularizer(
                state.detach()
            )
            loss = loss + 0.015 * fe_loss + 1e-7 * self.fly.W.pow(2).mean()
            self.net.optimizer.zero_grad()
            fo = getattr(self.fly, "fly_optimizer", None)
            if fo is not None:
                fo.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.net.parameters(), max_norm=5.0)
            if fo is not None:
                torch.nn.utils.clip_grad_norm_([self.fly.W], max_norm=1.0)
            self.net.optimizer.step()
            if fo is not None:
                fo.step()
        else:
            self.net.learn(loss, state=state)

        act_name = ACTIONS[action_idx]
        if act_name in ("explorar", "planear", "olfatear"):
            self.curiosity += 0.014
        if self.world.social_curiosidad > 0.2:
            self.curiosity += 0.005
        self.curiosity = float(np.clip(self.curiosity, 0.0, 1.08))

        if self.pending_brain_reply > 0:
            self.pending_brain_reply -= 1

    def pop_brain_reply_if_ready(self) -> str | None:
        if self.pending_brain_reply < 0:
            return None
        if self.pending_brain_reply > 0:
            return None
        with torch.no_grad():
            sensory = build_sensory(self.world, self.n_sensory, self.rng)
            activity = self.fly(sensory, steps=2, enable_grad=False)
            motor = self.fly.motor_output(activity).squeeze(0)
        msg = brain_utterance(
            self.world, motor, ACTIONS[self.last_action], self.reward_ema
        )
        self.pending_brain_reply = -1
        return msg

    def try_enqueue_curiosity_question(
        self,
        on_text: Callable[[str], None],
        min_steps_since_user: int = 1800,
        curiosity_threshold: float = 0.74,
    ) -> None:
        if self._question_busy:
            return
        if self.pending_q_learning is not None:
            return
        if self.steps_since_user < min_steps_since_user:
            return
        if self.curiosity < curiosity_threshold:
            return
        if self._last_plastic_state is None:
            return

        state = self._last_plastic_state.to(self.device)
        with torch.no_grad():
            _, q_logits, inst = self.net.forward_heads(state)
        if q_logits is None:
            return

        dist = torch.distributions.Categorical(logits=q_logits / 0.92)
        q_idx = int(dist.sample().item())
        inst_list = [float(x) for x in inst.detach().cpu().tolist()]

        self.pending_q_learning = {
            "state": state.detach().cpu().clone(),
            "q_idx": q_idx,
        }
        self._question_busy = True
        self.curiosity = max(0.18, self.curiosity * 0.4)
        self.steps_since_user = 0

        model = self.llm_model
        lc = self._llm_chat

        def worker() -> None:
            try:
                text = generate_fly_question(model, q_idx, inst_list, lc)
            except Exception as e:
                text = f"…(LLM: {e})"
            try:
                on_text(text)
            finally:
                self._question_busy = False

        threading.Thread(target=worker, daemon=True).start()
