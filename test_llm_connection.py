import json
import requests

def test_connection():
    url = "http://192.168.0.13:1234/api/v1/chat"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": "google/gemma-4-e4b",
        "system_prompt": "You answer only in rhymes.",
        "input": "What is your favorite color?"
    }

    print(f"Intentando conectar a: {url}")
    print("Enviando payload:")
    print(json.dumps(payload, indent=2))
    print("-" * 40)

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"Status Code: {response.status_code}")
        print("Respuesta cruda del servidor:")
        try:
            parsed_json = response.json()
            print(json.dumps(parsed_json, indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(response.text)
            
    except requests.exceptions.ConnectionError:
        print("ERROR: No se pudo conectar al servidor. Asegúrate de que LM Studio esté corriendo y el puerto 1234 esté expuesto.")
    except Exception as e:
        print(f"ERROR inesperado: {e}")

if __name__ == "__main__":
    test_connection()
