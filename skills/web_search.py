import sys
from duckduckgo_search import DDGS

def search_web(query: str, max_results: int = 5):
    """
    Realiza una búsqueda en internet usando DuckDuckGo y devuelve los resultados en texto.
    Esto permite que el sistema consulte información actualizada.
    """
    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No se encontraron resultados."
        
        output = []
        for i, r in enumerate(results, 1):
            output.append(f"{i}. {r.get('title')}\n   URL: {r.get('href')}\n   {r.get('body')}\n")
        return "\n".join(output)
    except Exception as e:
        return f"Error en la búsqueda web: {e}"

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f"Buscando: {query}\n")
        print(search_web(query))
    else:
        print("Uso: python web_search.py <consulta>")
