from __future__ import annotations

import operator as operator_module
import re
from dataclasses import dataclass
from typing import Any, Callable, Union

# Tipado para manejar enteros y flotantes que puedan resultar de las operaciones.
Number = Union[int, float]
InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]


SUPPORTED_OPERATORS = {
    "+": operator_module.add,
    "-": operator_module.sub,
    "*": operator_module.mul,
    "/": operator_module.truediv,
}


def calculate(num1: Number, num2: Number, operator: str) -> Number:
    """
    Realiza la operación aritmética especificada entre dos números.

    Args:
        num1 (number): Primer operando.
        num2 (number): Segundo operando.
        operator (str): Operador a usar ('+', '-', '*', '/').

    Returns:
        The result of the calculation.

    Raises:
        ValueError: Si el operador no es válido o si ocurre una división por cero.
    """
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"Operador '{operator}' no reconocido. Debe ser '+', '-', '*', o '/'.")

    if operator == "/":
        if num2 == 0:
            raise ValueError("La división por cero no es válida.")
    return SUPPORTED_OPERATORS[operator](num1, num2)


def parse_number(value: Any) -> Number:
    """Convierte una entrada externa a número entero o decimal."""
    if isinstance(value, bool):
        raise ValueError("La entrada debe ser un número, no un booleano.")
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        raise ValueError("La entrada debe ser numérica.")

    text = value.strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise ValueError(f"Entrada numérica inválida: {value!r}")
    number = float(text) if "." in text else int(text)
    return number


def prompt_for_number(
    label: str,
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> Number:
    """Solicita un número hasta recibir una entrada válida."""
    while True:
        try:
            return parse_number(input_func(label))
        except ValueError as exc:
            output_func(f"Error: {exc}. Intenta nuevamente.")


def format_number(value: Number) -> str:
    """Formatea resultados enteros sin `.0`."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def calculate_and_display(num1: Any, num2: Any, operator: str) -> str:
    """Calcula y devuelve un mensaje legible para UI o logs."""
    result = calculate(parse_number(num1), parse_number(num2), operator)
    return f"El resultado es: {format_number(result)}"


@dataclass
class ArithmeticCalculatorSkill:
    """Skill invocable por el sistema principal."""

    name: str = "arithmetic_calculator"

    def run(self, request: dict[str, Any]) -> dict[str, Any]:
        """
        Ejecuta una solicitud estructurada:
        {"num1": 10, "operator": "+", "num2": 5}
        """
        num1 = request.get("num1")
        num2 = request.get("num2")
        op = str(request.get("operator", "")).strip()
        result = calculate(parse_number(num1), parse_number(num2), op)
        return {
            "ok": True,
            "result": result,
            "message": f"El resultado es: {format_number(result)}",
            "operator": op,
            "num1": parse_number(num1),
            "num2": parse_number(num2),
        }


def handle_input_command(text: str) -> dict[str, Any] | None:
    """Permite invocar la skill desde texto: `calc 10 + 5`."""
    match = re.fullmatch(
        r"\s*(?:calc|calculadora)\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([+\-*/])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    num1, op, num2 = match.groups()
    return ArithmeticCalculatorSkill().run({"num1": num1, "operator": op, "num2": num2})

# Ejemplo de uso (Para referencia, aunque el harness lo llamará directamente)
if __name__ == '__main__':
    try:
        print(calculate_and_display(10, 2, '/'))

        calculate(5, 0, '/') # Esto debería fallar
    except ValueError as e:
        print(f"Error capturado (Correcto): {e}")
