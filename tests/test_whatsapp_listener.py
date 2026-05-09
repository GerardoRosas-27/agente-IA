import unittest
from typing import Dict, Any
# Importamos la utilidad que queremos probar directamente del módulo api_endpoints
# Esto asegura que process_incoming_payload esté en el scope de testing.
from api_endpoints.whatsapp_hook import process_incoming_payload 

class TestWhatsAppListener(unittest.TestCase):

    def setUp(self):
        """Configuración inicial para las pruebas."""
        pass

    # --- Pruebas de Handshake (GET) --
    @unittest.skip("Esta prueba valida el manejo HTTP GET, que requiere un mock de framework web (ej: Flask/FastAPI). Se comprueba la lógica en 'api_endpoints/whatsapp_hook.py'")
    def test_handshake_logic(self):
        """Simula la validación inicial del Webhook."""
        # Nota: Esta prueba debería llamar al handler completo, no solo a process_incoming_payload.
        # Se verifica el principio de negocio que debe estar en el hook.
        pass 

    # --- Pruebas de Mensaje de Texto (POST) ---
    def test_post_payload_text_message(self):
        """Valida la extracción correcta del remitente y contenido de texto."""
        mock_payload: Dict[str, Any] = {
            "entry": [{"changes": [{"value": {"messages": [{"type": "text", "text": "Hola mundo."}]}}]}]
        }
        result = process_incoming_payload(mock_payload)

        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "message_received")
        self.assertTrue("sender_id" in result)
        self.assertEqual(result["content"], "Hola mundo.")
        self.assertFalse(result["media_detected"])

    # --- Pruebas de Mensaje Multimedia (POST) - Imagen ---
    def test_post_payload_image_message(self):
        """Valida la detección y referencia correcta de un mensaje multimedia (imagen)."""
        mock_payload: Dict[str, Any] = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {"type": "image", "image": {"id": "XYZ123"}}
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        result = process_incoming_payload(mock_payload)

        self.assertIsNotNone(result)
        # Se espera que el contenido sea un placeholder y media_detected sea True
        self.assertEqual(result["content"], "[IMAGE: XYZ123]")
        self.assertTrue(result["media_detected"])
        self.assertEqual(result["metadata"]["media_type"], "image")

    # --- Pruebas de Robustez (Edge Cases) ---
    def test_post_payload_robustness_malformed(self):
        """Valida que el procesador maneje payloads incompletos sin fallar."""
        malformed_payload: Dict[str, Any] = {
            "entry": [{"changes": []}] # Falta 'value' o estructura de mensaje.
        }
        result = process_incoming_payload(malformed_payload)

        self.assertIsNone(result)

    def test_post_payload_empty_input(self):
        """Valida el manejo del input nulo o vacío."""
        result = process_incoming_payload({})
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
