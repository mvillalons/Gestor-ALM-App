"""
Extractor para Cartola Histórica Cuenta Corriente Itaú.

Columnas: Fecha | N°Op | Descripción | Cargos | Abonos | Saldo
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

# Descripciones a ignorar (transferencias internas)
_IGNORAR = {
    "Abono Desde Linea De Credito",
    "ABONO DESDE LINEA DE CREDITO",
    "Cargo Linea De Credito",
    "CARGO LINEA DE CREDITO",
}

_RE_FECHA = re.compile(r"^(\d{2})/(\d{2})\b")
_RE_MONTO = re.compile(r"[\d.,]+")


def _parsear_monto(texto: str) -> float:
    """Convierte '1.234.567' o '1,234,567' a float."""
    if not texto or texto.strip() in ("", "-", "0"):
        return 0.0
    limpio = texto.replace(".", "").replace(",", "").strip()
    try:
        return float(limpio)
    except ValueError:
        return 0.0


def _detectar_anio(texto_completo: str) -> int:
    """Busca el año del período en el encabezado."""
    match = re.search(r"\b(20\d{2})\b", texto_completo)
    if match:
        return int(match.group(1))
    return datetime.today().year


def extraer(filepath: str) -> list[Movimiento]:
    """Extrae movimientos de una cartola Itaú cuenta corriente."""
    if not _PDFPLUMBER_OK:
        raise ImportError("pdfplumber no está instalado. Instala con: pip install pdfplumber")

    movimientos: list[Movimiento] = []

    with pdfplumber.open(filepath) as pdf:
        texto_completo = "\n".join(
            (p.extract_text() or "") for p in pdf.pages
        )
        anio = _detectar_anio(texto_completo)

        for page in pdf.pages:
            tabla = page.extract_table()
            if tabla is None:
                continue

            for fila in tabla:
                if fila is None or len(fila) < 5:
                    continue

                celda_fecha = (fila[0] or "").strip()
                m_fecha = _RE_FECHA.match(celda_fecha)
                if not m_fecha:
                    continue

                # Detener en resumen
                descripcion = (fila[2] or "").strip()
                if "Resumen de Movimientos" in descripcion:
                    break

                # Ignorar transferencias internas
                if any(ign.lower() in descripcion.lower() for ign in _IGNORAR):
                    continue

                dia, mes = m_fecha.group(1), m_fecha.group(2)
                fecha_str = f"{anio}-{mes}-{dia}"
                # Validar fecha
                try:
                    datetime.strptime(fecha_str, "%Y-%m-%d")
                except ValueError:
                    continue

                referencia = (fila[1] or "").strip()
                cargos = _parsear_monto(fila[3] if len(fila) > 3 else "")
                abonos = _parsear_monto(fila[4] if len(fila) > 4 else "")

                if cargos == 0 and abonos == 0:
                    continue

                monto = abonos - cargos  # positivo=ingreso, negativo=egreso
                raw = "|".join(str(c or "") for c in fila)

                movimientos.append(Movimiento(
                    fecha=fecha_str,
                    descripcion=descripcion,
                    monto=monto,
                    moneda="CLP",
                    monto_clp=monto,
                    fuente="itau_cta_cte",
                    referencia=referencia,
                    confianza_extraccion=1.0,
                    raw=raw,
                ))

    return movimientos
