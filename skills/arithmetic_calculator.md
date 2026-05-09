# arithmetic_calculator

## Uso

Skill de calculadora aritmética con operaciones básicas:

- Suma: `+`
- Resta: `-`
- Multiplicación: `*`
- División: `/`

## Comando textual

```text
calc 10 + 5
calculadora 7 / 3
```

## API estructurada

```python
from skills.arithmetic_calculator import ArithmeticCalculatorSkill

skill = ArithmeticCalculatorSkill()
result = skill.run({"num1": 10, "operator": "+", "num2": 5})
```

Respuesta:

```python
{
    "ok": True,
    "result": 15,
    "message": "El resultado es: 15",
    "operator": "+",
    "num1": 10,
    "num2": 5,
}
```

## Validación

- Acepta enteros y decimales.
- Rechaza texto no numérico.
- Rechaza división por cero con `ValueError`.
- La función `prompt_for_number()` reintenta hasta recibir una entrada válida.
# arithmetic_calculator

## Uso
- Código: `C:/python/agenteIA/skills/arithmetic_calculator.py`
- Importa este módulo desde el harness solo cuando la skill esté habilitada.
- Mantén aquí instrucciones concretas para que el sistema sepa cuándo usarla.
