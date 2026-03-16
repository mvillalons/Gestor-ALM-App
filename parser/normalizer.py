"""
Normaliza montos de movimientos a CLP y coordina la extracción.
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd

from parser.models import Movimiento
from parser.detector import detectar_formato


def extraer_movimientos(
    filepath: str,
    valor_usd_clp: float = 950.0,
    valor_uf_clp: float = 39700.0,
) -> tuple[list[Movimiento], str]:
    """
    Detecta formato, extrae movimientos y normaliza montos a CLP.

    Args:
        filepath: Ruta al archivo de cartola.
        valor_usd_clp: Tipo de cambio USD/CLP.
        valor_uf_clp: Valor de la UF en CLP.

    Returns:
        Tupla (movimientos, formato_detectado).
    """
    formato = detectar_formato(filepath)

    if formato == "desconocido":
        raise ValueError(
            f"Formato no soportado: {Path(filepath).suffix}. "
            "Soportados: PDF, XLSX, XLS, CSV."
        )

    # Seleccionar extractor
    if formato == "itau_cta_cte":
        from parser.extractors.itau_cta_cte import extraer
    elif formato == "itau_tc_nacional":
        from parser.extractors.itau_tc_nacional import extraer
    elif formato == "itau_tc_internacional":
        from parser.extractors.itau_tc_internacional import extraer
    elif formato == "generic_excel":
        from parser.extractors.generic_excel import extraer
    else:  # generic_pdf
        from parser.extractors.generic_pdf import extraer

    movimientos = extraer(filepath)

    # Normalizar monto_clp
    for mov in movimientos:
        if mov.moneda == "USD":
            mov.monto_clp = mov.monto * valor_usd_clp
        elif mov.moneda == "UF":
            mov.monto_clp = mov.monto * valor_uf_clp
        elif mov.moneda == "CLP":
            mov.monto_clp = mov.monto
        # Otros: dejar monto_clp en 0 si FX desconocido

    return movimientos, formato


def movimientos_a_dataframe(movimientos: list[Movimiento]) -> pd.DataFrame:
    """
    Convierte lista de movimientos a DataFrame para mostrar en UI.

    Columnas: fecha, descripcion, monto, moneda, monto_clp,
              fuente, confianza_extraccion
    """
    if not movimientos:
        return pd.DataFrame(columns=[
            "fecha", "descripcion", "monto", "moneda",
            "monto_clp", "fuente", "confianza_extraccion",
        ])

    return pd.DataFrame([
        {
            "fecha": m.fecha,
            "descripcion": m.descripcion,
            "monto": m.monto,
            "moneda": m.moneda,
            "monto_clp": m.monto_clp,
            "fuente": m.fuente,
            "referencia": m.referencia,
            "confianza_extraccion": m.confianza_extraccion,
        }
        for m in movimientos
    ])


def dataframe_a_movimientos(df: pd.DataFrame) -> list[Movimiento]:
    """
    Reconstruye lista de Movimiento desde un DataFrame editado en UI.
    """
    movimientos = []
    for _, fila in df.iterrows():
        movimientos.append(Movimiento(
            fecha=str(fila.get("fecha", "")),
            descripcion=str(fila.get("descripcion", "")),
            monto=float(fila.get("monto", 0)),
            moneda=str(fila.get("moneda", "CLP")),
            monto_clp=float(fila.get("monto_clp", 0)),
            fuente=str(fila.get("fuente", "editado")),
            referencia=str(fila.get("referencia", "")),
            confianza_extraccion=float(fila.get("confianza_extraccion", 1.0)),
            raw="",
        ))
    return movimientos
