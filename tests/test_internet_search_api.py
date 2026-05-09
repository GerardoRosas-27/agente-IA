from __future__ import annotations

import pytest
import requests

from skills.internet_search_api import InternetSearchAPI


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_check_connection_success():
    session = FakeSession(FakeResponse(status_code=204))
    api = InternetSearchAPI(session=session)

    status = api.check_connection("https://example.com")

    assert status.ok is True
    assert status.status_code == 204
    assert "Conexión disponible" in status.detail


def test_search_parses_duckduckgo_html_results():
    html = """
    <html>
      <body>
        <a class="result__a" href="https://example.com/a">Resultado A</a>
        <a class="result__snippet">Resumen del resultado A</a>
        <a class="result__a" href="https://example.com/b">Resultado B</a>
        <div class="result__snippet">Resumen del resultado B</div>
      </body>
    </html>
    """
    session = FakeSession(FakeResponse(text=html))
    api = InternetSearchAPI(session=session)

    results = api.search("consulta", max_results=1)

    assert len(results) == 1
    assert results[0].title == "Resultado A"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "Resumen del resultado A"


def test_search_text_formats_results():
    html = """
    <a class="result__a" href="https://example.com">Titulo</a>
    <a class="result__snippet">Snippet</a>
    """
    api = InternetSearchAPI(session=FakeSession(FakeResponse(text=html)))

    text = api.search_text("consulta")

    assert "1. Titulo" in text
    assert "URL: https://example.com" in text
    assert "Snippet" in text


def test_rejects_unsafe_urls():
    api = InternetSearchAPI(session=FakeSession(FakeResponse()))

    with pytest.raises(ValueError):
        api.check_connection("file:///etc/passwd")
