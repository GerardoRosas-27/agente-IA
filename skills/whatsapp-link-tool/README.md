# whatsapp-link-tool

Genera enlaces wa.me y whatsapp://send para iniciar conversaciones de WhatsApp sin usar red desde el modulo.

## Uso
Importa build_wa_me_url, build_whatsapp_deep_link o contact_payload desde tool.py. La herramienta no abre WhatsApp por si sola; devuelve enlaces listos para que la UI o el usuario los abra.

## Pruebas
`python -m unittest discover -s skills\whatsapp-link-tool -p test*.py`
