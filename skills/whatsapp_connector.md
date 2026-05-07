# whatsapp_connector

## Uso

Skill bidireccional para WhatsApp.

Tiene dos modos:

- Envío local con WhatsApp Web y `pywhatkit`.
- Webhook bidireccional con WhatsApp Cloud API para recibir mensajes, ejecutar el harness y responder al mismo número.

Comando directo desde el input principal:

```text
whatsapp +521234567890 | Hola desde el sistema
```

También acepta:

```text
wa +521234567890 | Hola
/whatsapp +521234567890 | Hola
/wa +521234567890 | Hola
```

## Requisitos

- Instalar `pywhatkit`.
- Tener navegador disponible.
- La primera vez, WhatsApp Web mostrará el QR para vincular la cuenta.
- Para recibir mensajes automáticamente necesitas WhatsApp Cloud API y un webhook público.

Variables para el modo bidireccional:

```text
WHATSAPP_ACCESS_TOKEN=token_de_meta
WHATSAPP_PHONE_NUMBER_ID=id_del_numero_en_meta
WHATSAPP_VERIFY_TOKEN=un_token_que_tu_elijas
WHATSAPP_GRAPH_API_VERSION=v20.0
WHATSAPP_WEBHOOK_PORT=8080
```

Ejecución del webhook:

```bash
python skills/whatsapp_connector.py
```

Luego publica `http://TU_HOST:8080/webhook` con ngrok, Cloudflare Tunnel o un servidor público y configúralo en Meta Developers.

## API

- `open_whatsapp_login()`: abre `https://web.whatsapp.com`.
- `send_whatsapp_message(phone_number, message)`: envía el mensaje usando WhatsApp Web.
- `handle_input_command(text)`: detecta y ejecuta el comando directo desde el input.
- `send_cloud_message(phone_number, message)`: responde por WhatsApp Cloud API.
- `extract_incoming_messages(payload)`: extrae mensajes entrantes desde el webhook.
- `handle_webhook_payload(payload, responder=harness_responder)`: procesa mensajes y responde.
- `harness_responder(message)`: divide el texto entrante en tareas, ejecuta ciclos y devuelve resumen.

El número debe incluir código de país, por ejemplo `+521234567890`.
