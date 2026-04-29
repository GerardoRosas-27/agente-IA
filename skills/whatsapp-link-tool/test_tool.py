import unittest
from tool import build_wa_me_url, build_whatsapp_deep_link, contact_payload, normalize_phone, run


class TestWhatsAppLinkTool(unittest.TestCase):
    def test_build_wa_me_url_with_message(self):
        self.assertEqual(
            build_wa_me_url("+52 55 1234 5678", "hola mundo"),
            "https://wa.me/525512345678?text=hola%20mundo",
        )

    def test_default_country_code(self):
        self.assertEqual(normalize_phone("5512345678", "52"), "525512345678")

    def test_deep_link(self):
        self.assertEqual(
            build_whatsapp_deep_link("525512345678", "ok"),
            "whatsapp://send?phone=525512345678&text=ok",
        )

    def test_payload_and_invalid_phone(self):
        payload = contact_payload("525512345678")
        self.assertIn("wa_me_url", payload)
        with self.assertRaises(ValueError):
            normalize_phone("")

    def test_run_contract(self):
        payload = run("5512345678", "hola", "52")
        self.assertEqual(payload["phone"], "525512345678")


if __name__ == "__main__":
    unittest.main()
