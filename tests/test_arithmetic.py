import pytest
from skills.arithmetic_calculator import (
    ArithmeticCalculatorSkill,
    calculate,
    calculate_and_display,
    handle_input_command,
    parse_number,
    prompt_for_number,
)

# Testes positivos requeridos por la Feature ID 5
def test_addition():
    """Dado 5 y 3, el resultado debe ser 8."""
    assert calculate(5, 3, '+') == 8

def test_subtraction():
    """Dado 10 y 4, el resultado debe ser 6."""
    assert calculate(10, 4, '-') == 6

def test_multiplication():
    """Dado 5 y 4, el resultado debe ser 20."""
    assert calculate(5, 4, '*') == 20

def test_division_ok():
    """Test de división estándar (10 / 2 = 5)."""
    # Usamos float para asegurar la precisión de la división.
    assert calculate(10, 2, '/') == 5.0

# Testes negativos y límites
def test_division_by_zero():
    """Caso crítico: Dado N y 0, intentar dividir debe levantar ValueError."""
    with pytest.raises(ValueError) as excinfo:
        calculate(10, 0, '/')
    assert "La división por cero no es válida." in str(excinfo.value)

def test_unknown_operator():
    """Test de operador desconocido para verificar manejo de errores."""
    with pytest.raises(ValueError) as excinfo:
        calculate(1, 2, '%')
    assert "Operador '%' no reconocido" in str(excinfo.value)

def test_float_precision():
    """Verificar que las operaciones con floats funcionen correctamente."""
    # Prueba de un resultado decimal esperado
    assert calculate(7, 3, '/') == pytest.approx(2.3333333333333335)


def test_parse_number_rejects_text():
    with pytest.raises(ValueError):
        parse_number("hola")
    assert parse_number("5.5") == 5.5
    assert parse_number("-3") == -3


def test_prompt_for_number_retries_until_valid():
    inputs = iter(["hola", "5.5"])
    messages = []
    result = prompt_for_number(
        "Numero: ",
        input_func=lambda _prompt: next(inputs),
        output_func=messages.append,
    )
    assert result == 5.5
    assert messages


def test_calculate_and_display():
    assert calculate_and_display(10, 5, "+") == "El resultado es: 15"


def test_skill_structured_request():
    result = ArithmeticCalculatorSkill().run({"num1": "10", "operator": "+", "num2": 5})
    assert result["ok"] is True
    assert result["result"] == 15
    assert result["message"] == "El resultado es: 15"


def test_handle_input_command():
    result = handle_input_command("calc 10 + 5")
    assert result is not None
    assert result["result"] == 15
    assert handle_input_command("haz otra cosa") is None
