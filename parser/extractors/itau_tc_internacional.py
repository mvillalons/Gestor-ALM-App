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

_RE_FECHA_TABLA = re.compile(r"^(\d{2})/(\d{2})/(\d{2,4})$")

# Patrón para extracción por texto plano.
# Formato real: "2601 82305096023500039362181 23/01/26 LIME*RIDE 6XET SAN FRANCIS US 1.900,00 2,21"
# Grupos: (nref)(nop)(fecha DD/MM/YY)(descripcion+ciudad)(pais 2L)(monto_orig)(monto_usd)
_RE_MOV_TEXTO = re.compile(
    r"^(\d{3,6})\s+"                  # N°Ref (3-6 dígitos)
    r"(\d{10,})\s+"                    # N°Op (10+ dígitos)
    r"(\d{2}/\d{2}/\d{2,4})\s+"       # fecha DD/MM/YY o DD/MM/YYYY
    r"(.+?)\s+"                        # descripcion + ciudad (non-greedy)
    r"([A-Z]{2})\s+"                   # código país (2 letras mayúsculas)
    r"([\d\.,]+)\s+"                   # monto en moneda origen
    r"([\d\.,]+)\s*$",                 # monto USD
    re.MULTILINE,
)


def _parsear_monto_usd(texto: str) -> float:
    if not texto or texto.strip() in ("", "-"):
        return 0.0
    limpio = texto.strip()
    # Formato europeo: "1.900,00" → punto=miles, coma=decimal
    if re.search(r"\d\.\d{3},", limpio):
        limpio = limpio.replace(".", "").replace(",", ".")
    else:
        limpio = re.sub(r"[^0-9.\-]", "", limpio.replace(",", ""))
    try:
        return float(limpio)
    except ValueError:
        return 0.0


def _parsear_fecha(texto: str) -> str | None:
    m = _RE_FECHA_TABLA.match((texto or "").strip())
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


def _es_excluido(descripcion: str) -> bool:
    desc_up = descripcion.upper()
    return any(exc in desc_up for exc in _EXCLUIR)


def _extraer_de_tabla(tabla: list) -> list[Movimiento]:
    """Extrae movimientos desde la tabla estructurada de pdfplumber."""
    movimientos: list[Movimiento] = []
    for fila in tabla:
        if fila is None or len(fila) < 5:
            continue

        fecha_str = _parsear_fecha(fila[1] if len(fila) > 1 else "")
        if not fecha_str:
            continue

        descripcion = (fila[2] if len(fila) > 2 else "").strip()
        if not descripcion or _es_excluido(descripcion):
            continue

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
            monto_clp=0.0,
            fuente="itau_tc_internacional",
            referencia=referencia,
            confianza_extraccion=1.0,
            raw=raw,
        ))
    return movimientos


def _extraer_de_texto(texto: str) -> list[Movimiento]:
    """
    Fallback: extrae movimientos por regex sobre el texto plano.
    Cubre el caso en que pdfplumber no detecta tabla.
    """
    movimientos: list[Movimiento] = []
    for m in _RE_MOV_TEXTO.finditer(texto):
        referencia = m.group(1)
        fecha_raw = m.group(3)         # "23/01/26"
        descripcion_ciudad = m.group(4).strip()
        # grupo 5 = código país (ej: "US") — incluido en descripcion_ciudad para display
        monto_usd_raw = m.group(7)     # monto USD (último campo)

        fecha_str = _parsear_fecha(fecha_raw)
        if not fecha_str:
            continue

        if _es_excluido(descripcion_ciudad):
            continue

        monto_abs = _parsear_monto_usd(monto_usd_raw)
        if monto_abs == 0:
            continue

        es_pago = "PAGO" in descripcion_ciudad.upper() or "ABONO" in descripcion_ciudad.upper()
        monto = monto_abs if es_pago else -monto_abs

        movimientos.append(Movimiento(
            fecha=fecha_str,
            descripcion=descripcion_ciudad,
            monto=monto,
            moneda="USD",
            monto_clp=0.0,
            fuente="itau_tc_internacional",
            referencia=referencia,
            confianza_extraccion=1.0,
            raw=m.group(0),
        ))
    return movimientos


def extraer(filepath: str) -> list[Movimiento]:
    """Extrae movimientos de un estado de cuenta TC Internacional Itaú."""
    if not _PDFPLUMBER_OK:
        raise ImportError("pdfplumber no está instalado.")

    movimientos: list[Movimiento] = []

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            # ── Intentar extracción estructurada primero ──────────────────────
            tabla = page.extract_table()
            if tabla:
                filas = _extraer_de_tabla(tabla)
                if filas:
                    movimientos.extend(filas)
                    continue  # tabla funcionó → siguiente página

            # ── Fallback: extracción por texto plano ──────────────────────────
            texto_pagina = page.extract_text() or ""
            if texto_pagina:
                movimientos.extend(_extraer_de_texto(texto_pagina))

    return movimientos
