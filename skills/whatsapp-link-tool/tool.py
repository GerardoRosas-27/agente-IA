"""
Herramienta local para preparar enlaces de WhatsApp.

No llama a internet ni abre aplicaciones; solo normaliza telefonos y genera
URLs/URI que otra capa puede abrir con aprobacion del usuario.
"""


def normalize_phone(phone, default_country_code=""):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    country = "".join(ch for ch in str(default_country_code or "") if ch.isdigit())
    if not digits:
        raise ValueError("telefono vacio")
    if country and len(digits) <= 10 and not digits.startswith(country):
        digits = country + digits
    if len(digits) < 8 or len(digits) > 15:
        raise ValueError("telefono fuera de rango E.164")
    return digits


def _quote_text(text):
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = []
    for byte in str(text or "").encode("utf-8"):
        ch = chr(byte)
        out.append(ch if ch in safe else "%{:02X}".format(byte))
    return "".join(out)


def build_wa_me_url(phone, message="", default_country_code=""):
    normalized = normalize_phone(phone, default_country_code)
    url = "https://wa.me/" + normalized
    if message:
        url += "?text=" + _quote_text(message)
    return url


def build_whatsapp_deep_link(phone, message="", default_country_code=""):
    normalized = normalize_phone(phone, default_country_code)
    link = "whatsapp://send?phone=" + normalized
    if message:
        link += "&text=" + _quote_text(message)
    return link


def contact_payload(phone, message="", default_country_code=""):
    return {
        "phone": normalize_phone(phone, default_country_code),
        "wa_me_url": build_wa_me_url(phone, message, default_country_code),
        "deep_link": build_whatsapp_deep_link(phone, message, default_country_code),
    }


def run(phone, message="", default_country_code=""):
    return contact_payload(phone, message, default_country_code)
