"""
Extractor genérico para archivos Excel (.xlsx, .xls) y CSV.
Detecta columnas por nombre o heurística.
"""
from __future__ import annotations
import re
from datetime import datetime, date

import pandas as pd

from parser.models import Movimiento

# Palabras clave para detección de columnas
_KW_FECHA = {"fecha", "date", "día", "dia", "fec"}
_KW_DESC = {"descripcion", "descripción", "glosa", "detalle",
            "concepto", "movimiento", "narration", "detail"}
_KW_MONTO = {"monto", "importe", "valor", "amount", "total"}
_KW_CARGO = {"cargo", "débito", "debito", "gasto", "egreso", "debit"}
_KW_ABONO = {"abono", "crédito", "credito", "ingreso", "haber", "credit"}


def _normalizar_col(nombre: str) -> str:
    return re.sub(r"[^a-záéíóúñ0-9]", "", nombre.lower().strip())


def _detectar_columna(columnas: list[str], keywords: set[str]) -> str | None:
    for col in columnas:
        norm = _normalizar_col(col)
        if any(kw in norm for kw in keywords):
            return col
    return None


def _parsear_fecha(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (datetime, date)):
        return val.strftime("%Y-%m-%d")
    texto = str(val).strip()
    # Intentar varios formatos
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(texto, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parsear_monto(val) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    texto = str(val).strip().replace("$", "").replace(" ", "")
    texto = texto.replace(".", "").replace(",", "")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def extraer(filepath: str) -> list[Movimiento]:
    """Extrae movimientos de un archivo Excel o CSV."""
    fp = str(filepath).lower()
    try:
        if fp.endswith(".csv"):
            # Intentar distintos separadores
            for sep in (",", ";", "\t"):
                try:
                    df = pd.read_csv(filepath, sep=sep, dtype=str, encoding="utf-8")
                    if len(df.columns) >= 3:
                        break
                except Exception:
                    continue
            else:
                df = pd.read_csv(filepath, dtype=str)
        else:
            df = pd.read_excel(filepath, dtype=str)
    except Exception as exc:
        raise ValueError(f"No se pudo leer el archivo: {exc}") from exc

    # Limpiar nombres de columnas
    df.columns = [str(c).strip() for c in df.columns]
    columnas = list(df.columns)

    # Detectar columnas por nombre
    col_fecha = _detectar_columna(columnas, _KW_FECHA)
    col_desc = _detectar_columna(columnas, _KW_DESC)
    col_monto = _detectar_columna(columnas, _KW_MONTO)
    col_cargo = _detectar_columna(columnas, _KW_CARGO)
    col_abono = _detectar_columna(columnas, _KW_ABONO)

    # Confianza según detección
    por_nombre = sum(
        1 for c in [col_fecha, col_desc, col_monto or col_cargo]
        if c is not None
    )
    confianza = 0.9 if por_nombre >= 2 else 0.7

    # Fallback por posición si no hay detección por nombre
    if col_fecha is None and len(columnas) >= 1:
        col_fecha = columnas[0]
    if col_desc is None and len(columnas) >= 2:
        col_desc = columnas[1]
    if col_monto is None and col_cargo is None and len(columnas) >= 3:
        col_monto = columnas[2]

    movimientos: list[Movimiento] = []

    for _, fila in df.iterrows():
        fecha_str = _parsear_fecha(fila.get(col_fecha)) if col_fecha else None
        if not fecha_str:
            continue

        descripcion = str(fila.get(col_desc, "")).strip() if col_desc else "Movimiento"

        # Calcular monto
        if col_cargo and col_abono:
            cargo = _parsear_monto(fila.get(col_cargo, 0))
            abono = _parsear_monto(fila.get(col_abono, 0))
            monto = abono - cargo
        elif col_monto:
            monto = _parsear_monto(fila.get(col_monto, 0))
        else:
            continue

        if monto == 0:
            continue

        raw = "|".join(str(v) for v in fila.values)

        movimientos.append(Movimiento(
            fecha=fecha_str,
            descripcion=descripcion[:200],
            monto=monto,
            moneda="CLP",
            monto_clp=monto,
            fuente="generic_excel",
            referencia="",
            confianza_extraccion=confianza,
            raw=raw[:500],
        ))

    return movimientos
