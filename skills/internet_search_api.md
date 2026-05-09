# internet_search_api

## Uso

Skill de conexión a internet y búsqueda web para el sistema agentico.

Permite:

- verificar conectividad HTTP/HTTPS;
- buscar resultados web usando DuckDuckGo HTML;
- devolver resultados estructurados con `title`, `url` y `snippet`;
- devolver resultados en texto legible;
- descargar texto crudo de una URL para inspección básica.

## API estructurada

```python
from skills.internet_search_api import run

run({
    "action": "check",
    "url": "https://example.com",
})

run({
    "action": "search",
    "query": "documentación Python requests",
    "max_results": 5,
})
```

Acciones soportadas:

- `check`: prueba conexión contra una URL.
- `search`: retorna lista estructurada de resultados.
- `search_text`: retorna resultados formateados como texto.
- `fetch`: descarga texto de una URL HTTP/HTTPS.

## Uso directo

```bash
python skills/internet_search_api.py "noticias tecnologia"
```

## Nota

Esta skill usa `requests` y evita navegadores. No ejecuta JavaScript ni accede a
sitios que requieren login. Para automatización web interactiva se debe usar una
skill de navegador separada.
