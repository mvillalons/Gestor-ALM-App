"""
Extractor genérico para PDFs de cualquier banco.
Usa heurísticas para detectar movimientos cuando el formato es desconocido.
"""
from __future__ import annotations
import re
from datetime import datetime

try:
    import pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False

from parser.models import Movimiento

# Patrones de fecha
_RE_FECHA_DMY = re.compile(r"\b(\d{2})[/\-](\d{2})[/\-](\d{2,4})\b")
_RE_FECHA_YMD = re.compile(r"\b(20\d{2})[/\-](\d{2})[/\-](\d{2})\b")

# Patrón de monto: $ seguido de número, o número con puntos/comas de miles
_RE_MONTO = re.compile(
    r"(?:\$\s*)?([+-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{0,2})?|\d+)"
)


def _intentar_fecha(texto: str) -> str | None:
    """Intenta parsear una fecha de un texto."""
    # YYYY-MM-DD
    m = _RE_FECHA_YMD.search(texto)
    if m:
        fecha_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
            return fecha_str
        except ValueError:
            pass

    # DD/MM/YYYY o DD/MM/YY
    m = _RE_FECHA_DMY.search(texto)
    if m:
        dia, mes, anio_raw = m.group(1), m.group(2), m.group(3)
        anio = "20" + anio_raw if len(anio_raw) == 2 else anio_raw
        fecha_str = f"{anio}-{mes}-{dia}"
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
            return fecha_str
        except ValueError:
            pass

    return None


def _extraer_monto(texto: str) -> float | None:
    """Extrae el monto más significativo de una línea."""
    montos = []
    for m in _RE_MONTO.finditer(texto):
        raw = m.group(1).replace(".", "").replace(",", "")
        try:
            val = float(raw)
            if val > 100:  # ignorar números pequeños (días, meses, etc.)
                montos.append(val)
        except ValueError:
            continue

    if not montos:
        return None

    # Tomar el monto más grande (probablemente el importe de la transacción)
    return max(montos)


def extraer(filepath: str) -> list[Movimiento]:
    """
    Extrae movimientos de un PDF desconocido usando heurísticas.

    Retorna movimientos con confianza_extraccion = 0.7.
    La UI debe avisar al usuario que revise con cuidado.
    """
    if not _PDFPLUMBER_OK:
        raise ImportError("pdfplumber no está instalado.")

    movimientos: list[Movimiento] = []

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            lineas = texto.split("\n")

            for linea in lineas:
                linea = linea.strip()
                if len(linea) < 10:
                    continue

                fecha_str = _intentar_fecha(linea)
                if not fecha_str:
                    continue

                monto = _extraer_monto(linea)
                if monto is None:
                    continue

                # La descripción es el texto de la línea sin fecha ni monto
                descripcion = re.sub(
                    r"\b\d{2}[/\-]\d{2}[/\-]\d{2,4}\b", "", linea
                )
                descripcion = re.sub(r"\$?\s*[\d.,]+", "", descripcion).strip()
                descripcion = re.sub(r"\s{2,}", " ", descripcion).strip()

                if not descripcion:
                    descripcion = "Movimiento"

                # Heurística: si aparece "cargo" o "débito" → negativo
                es_egreso = any(
                    kw in linea.lower()
                    for kw in ["cargo", "débito", "debito", "compra", "pago", "giro"]
                )
                monto_final = -monto if es_egreso else monto

                movimientos.append(Movimiento(
                    fecha=fecha_str,
                    descripcion=descripcion[:200],
                    monto=monto_final,
                    moneda="CLP",
                    monto_clp=monto_final,
                    fuente="generic_pdf",
                    referencia="",
                    confianza_extraccion=0.7,
                    raw=linea[:500],
                ))

    return movimientos
