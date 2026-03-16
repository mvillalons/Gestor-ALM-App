"""
Parser de cartolas bancarias para el Personal Finance OS.

Uso básico:
    from parser.normalizer import extraer_movimientos
    movimientos, formato = extraer_movimientos("cartola.pdf")
"""
from parser.models import Movimiento, PropuestaClasificacion
from parser.detector import detectar_formato
from parser.normalizer import (
    extraer_movimientos,
    movimientos_a_dataframe,
    dataframe_a_movimientos,
)

__all__ = [
    "Movimiento",
    "PropuestaClasificacion",
    "detectar_formato",
    "extraer_movimientos",
    "movimientos_a_dataframe",
    "dataframe_a_movimientos",
]
