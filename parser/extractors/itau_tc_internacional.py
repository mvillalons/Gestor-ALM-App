"""
Extractor para Estado de Cuenta TC Internacional Itaú (USD).

Columnas: N°Ref | Fecha | Descripción | Ciudad | País |
          Monto Moneda Origen | Monto USD
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

_EXCLUIR = {
    "TOTAL DE PAGOS", "TOTAL DE COMPRAS", "TOTAL TARJETA",
    "COMISIONES", "TOTAL PAT", "TOTAL", "SALDO",
}

_RE_FECHA = re.compile(r"^(\d{2})/(\d{2})/(\d{2,4})$")


def _parsear_monto_usd(texto: str) -> float:
    if not texto or texto.strip() in ("", "-"):
        return 0.0
    limpio = re.sub(r"[^0-9.\-]", "", texto.strip().replace(",", ""))
    try:
        return float(limpio)
    except ValueError:
        return 0.0


def _parsear_fecha(texto: str) -> str | None:
    m = _RE_FECHA.match((texto or "").strip())
    if not m:
        return None
    dia, mes, anio_raw = m.group(1), m.group(2), m.group(3)
    anio = "20" + anio_raw if len(anio_raw) == 2 else anio_raw
    fecha_str = f"{anio}-{mes}-{dia}"
    try:
        datetime.strptime(fecha_str, "%Y-%m-%d")
        return fecha_str
    except ValueError:
        return None


def extraer(filepath: str) -> list[Movimiento]:
    """Extrae movimientos de un estado de cuenta TC Internacional Itaú."""
    if not _PDFPLUMBER_OK:
        raise ImportError("pdfplumber no está instalado.")

    movimientos: list[Movimiento] = []

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            tabla = page.extract_table()
            if tabla is None:
                continue

            for fila in tabla:
                if fila is None or len(fila) < 5:
                    continue

                # Columna fecha (índice 1)
                fecha_str = _parsear_fecha(fila[1] if len(fila) > 1 else "")
                if not fecha_str:
                    continue

                descripcion = (fila[2] if len(fila) > 2 else "").strip()

                if any(exc.lower() in descripcion.upper() for exc in _EXCLUIR):
                    continue

                # Monto USD (última columna con valor)
                monto_usd_raw = fila[-1] if fila[-1] else (fila[-2] if len(fila) >= 2 else "")
                monto_abs = _parsear_monto_usd(monto_usd_raw)

                if monto_abs == 0:
                    continue

                es_pago = "PAGO" in descripcion.upper() or "ABONO" in descripcion.upper()
                monto = monto_abs if es_pago else -monto_abs

                referencia = (fila[0] or "").strip()
                raw = "|".join(str(c or "") for c in fila)

                movimientos.append(Movimiento(
                    fecha=fecha_str,
                    descripcion=descripcion,
                    monto=monto,
                    moneda="USD",
                    monto_clp=0.0,  # se calcula en normalizer con tipo de cambio
                    fuente="itau_tc_internacional",
                    referencia=referencia,
                    confianza_extraccion=1.0,
                    raw=raw,
                ))

    return movimientos
