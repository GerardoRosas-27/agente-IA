import pytest
from skills.arithmetic_calculator import calculate

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
