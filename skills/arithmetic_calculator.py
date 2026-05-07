from typing import Union, Any
import math

# Tipado para manejar enteros y flotantes que puedan resultar de las operaciones.
Number = Union[int, float]


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
    if operator == '+':
        return num1 + num2
    elif operator == '-':
        return num1 - num2
    elif operator == '*':
        return num1 * num2
    elif operator == '/':
        # Chequeo crítico: División por cero
        if num2 == 0:
            raise ValueError("La división por cero no es válida.")
        return num1 / num2
    else:
        raise ValueError(f"Operador '{operator}' no reconocido. Debe ser '+', '-', '*', o '/'.")

# Ejemplo de uso (Para referencia, aunque el harness lo llamará directamente)
if __name__ == '__main__':
    try:
        result = calculate(10, 2, '/')
        print(f"10 / 2 = {result}")

        calculate(5, 0, '/') # Esto debería fallar
    except ValueError as e:
        print(f"Error capturado (Correcto): {e}")
