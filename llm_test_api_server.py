"""
API HTTP mínima compatible con OpenAI Chat Completions (solo POST no-stream).

Sirve de respaldo temporal cuando LM Studio no está en marcha: arranca en otra
terminal y deja `LLM_TEST_API_BASE_URL` por defecto (http://127.0.0.1:8765/v1).

Solo biblioteca estándar.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


def _last_user_text(messages: list) -> str:
    for m in reversed(messages or []):
        if isinstance(m, dict) and (m.get("role") or "").lower() == "user":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                return c.strip()
    return ""


def _stub_reply(messages: list, system_hint: str) -> str:
    u = _last_user_text(messages)[:1200]
    low = (system_hint + "\n" + u).lower()
    if "revisor" in low or "objetivo_alcanzado" in low or "respuesta_final" in low:
        return (
            "OBJETIVO_ALCANZADO: NO\n"
            "MOTIVO: API de prueba (LM Studio no respondió).\n"
            "RETROALIMENTACION: Arranca LM Studio o revisa LLM_API_BASE_URL en .env.\n"
            "RESPUESTA_FINAL: Respuesta sustituta del servidor de prueba local. "
            "Último contexto recibido (recorte):\n"
            f"{u[:500]}"
        )
    if "interpret" in low or "objetivo_claro" in low or "entiende" in low:
        return (
            "OBJETIVO_CLARO: (simulado por API de prueba — conecta LM Studio para respuestas reales)\n"
            "CRITERIO_1: Verificar que el servidor principal de chat responda.\n"
            "CRITERIO_2: Revisar URL y modelo en .env.\n"
            f"(Entrada recibida, {len(u)} caracteres.)\n{u[:400]}"
        )
    if "planific" in low or "plan:" in low:
        return (
            "1. Comprobar LM Studio (servidor local activo).\n"
            "2. Validar LLM_API_BASE_URL y LLM_MODEL en .env.\n"
            "3. Opcional: mantener este script en marcha como fallback.\n"
            "4. Volver a ejecutar el pipeline.\n"
            "5. Revisar logs si el principal sigue fallando.\n"
        )
    return (
        "[API de prueba]\n"
        "LM Studio no contestó; este es un borrador local.\n\n" + (u[:700] or "(sin mensaje user)")
    )


class _Handler(BaseHTTPRequestHandler):
    server_version = "LLMTestAPI/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[llm-test-api] {self.address_string()} - {fmt % args}")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path or ""
        if path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self.send_error(404, "solo /v1/chat/completions")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            self.send_error(400, "JSON inválido")
            return

        messages = body.get("messages") or []
        sys_parts = [str(m.get("content", "")) for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        system_hint = "\n".join(sys_parts)[:4000]
        text = _stub_reply(messages if isinstance(messages, list) else [], system_hint)
        out = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "model": body.get("model") or "test-llm",
        }
        data = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    p = argparse.ArgumentParser(description="API de prueba tipo OpenAI / LM Studio.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    httpd = HTTPServer((args.host, args.port), _Handler)
    base = f"http://{args.host}:{args.port}/v1"
    print(f"API de prueba en {base}  (POST {base}/chat/completions)")
    print("Ctrl+C para detener.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")


if __name__ == "__main__":
    main()
