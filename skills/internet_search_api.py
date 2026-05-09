from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urlparse

import requests


DEFAULT_SEARCH_URL = "https://html.duckduckgo.com/html/"
DEFAULT_USER_AGENT = "agenteIA-internet-search/1.0"


class HttpSession(Protocol):
    def get(self, url: str, **kwargs: Any) -> requests.Response:
        ...


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class ConnectionStatus:
    ok: bool
    url: str
    status_code: int | None
    detail: str


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self._current_title_href = ""
        self._current_title_parts: list[str] = []
        self._current_snippet_parts: list[str] = []
        self._capture_title = False
        self._capture_snippet = False
        self._pending_title: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._capture_title = True
            self._current_title_href = attr.get("href", "")
            self._current_title_parts = []
        elif "result__snippet" in classes:
            self._capture_snippet = True
            self._current_snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._current_title_parts.append(data)
        if self._capture_snippet:
            self._current_snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            title = _clean_text("".join(self._current_title_parts))
            url = self._current_title_href.strip()
            if title and url:
                self._pending_title = (title, url)
            self._capture_title = False
        elif self._capture_snippet and tag in {"a", "div"}:
            snippet = _clean_text("".join(self._current_snippet_parts))
            if self._pending_title:
                title, url = self._pending_title
                self.results.append(SearchResult(title=title, url=url, snippet=snippet))
                self._pending_title = None
            self._capture_snippet = False


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())


def _is_safe_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class InternetSearchAPI:
    """API simple de conexión a internet y búsqueda web para el sistema agentico."""

    def __init__(
        self,
        *,
        session: HttpSession | None = None,
        search_url: str = DEFAULT_SEARCH_URL,
        timeout: float = 15,
    ) -> None:
        self.session = session or requests.Session()
        self.search_url = search_url
        self.timeout = timeout

    def check_connection(self, url: str = "https://example.com") -> ConnectionStatus:
        if not _is_safe_http_url(url):
            raise ValueError("Solo se permiten URLs http/https válidas.")
        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except requests.RequestException as exc:
            return ConnectionStatus(False, url, None, f"Error de conexión: {exc}")
        ok = 200 <= int(response.status_code) < 400
        detail = "Conexión disponible." if ok else f"Respuesta HTTP inesperada: {response.status_code}"
        return ConnectionStatus(ok, url, int(response.status_code), detail)

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        query = query.strip()
        if not query:
            raise ValueError("La consulta de búsqueda no puede estar vacía.")
        response = self.session.get(
            self.search_url,
            params={"q": query},
            timeout=self.timeout,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        response.raise_for_status()
        parser = _DuckDuckGoHTMLParser()
        parser.feed(response.text)
        return parser.results[: max(max_results, 0)]

    def search_text(self, query: str, *, max_results: int = 5) -> str:
        results = self.search(query, max_results=max_results)
        if not results:
            return "No se encontraron resultados."
        lines: list[str] = []
        for index, item in enumerate(results, start=1):
            lines.append(f"{index}. {item.title}\n   URL: {item.url}\n   {item.snippet}")
        return "\n".join(lines)

    def fetch_text(self, url: str, *, max_chars: int = 6000) -> str:
        if not _is_safe_http_url(url):
            raise ValueError("Solo se permiten URLs http/https válidas.")
        response = self.session.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        response.raise_for_status()
        return response.text[:max_chars]


def run(request: dict[str, Any]) -> dict[str, Any]:
    """Entrada estructurada para agentes.

    Acciones soportadas: check, search, search_text, fetch.
    """
    api = InternetSearchAPI()
    action = str(request.get("action", "")).strip().lower()
    if action == "check":
        status = api.check_connection(str(request.get("url", "https://example.com")))
        return {"ok": status.ok, "status": asdict(status)}
    if action == "search":
        results = api.search(str(request.get("query", "")), max_results=int(request.get("max_results", 5)))
        return {"ok": True, "results": [asdict(item) for item in results]}
    if action == "search_text":
        text = api.search_text(str(request.get("query", "")), max_results=int(request.get("max_results", 5)))
        return {"ok": True, "text": text}
    if action == "fetch":
        text = api.fetch_text(str(request.get("url", "")), max_chars=int(request.get("max_chars", 6000)))
        return {"ok": True, "text": text}
    raise ValueError(f"Acción no soportada: {action}")


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        print("Uso: python skills/internet_search_api.py <consulta>")
        raise SystemExit(1)
    print(InternetSearchAPI().search_text(query))
