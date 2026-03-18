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

# Patrón para extracción por texto plano:
# "DD/MM NNNNNNN DESCRIPCION $CARGO $ABONO $SALDO"
# Ejemplo: "02/02 535205112 Transf. A Mauro $362.931 $0 $167.069"
_RE_MOV_TEXTO = re.compile(
    r"^(\d{2}/\d{2})\s+"       # fecha DD/MM
    r"(\d{5,})\s+"              # N° operación (5+ dígitos)
    r"(.+?)\s+"                 # descripción (non-greedy)
    r"\$\s*([\d\.]+)\s+"        # cargo  $xxx.xxx
    r"\$\s*([\d\.]+)\s+"        # abono  $xxx.xxx
    r"[-\$]?\s*([\d\.,]+)",     # saldo
    re.MULTILINE,
)


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


def _extraer_de_tabla(tabla: list, anio: int) -> list[Movimiento]:
    """Extrae movimientos a partir de la tabla estructurada de pdfplumber."""
    movimientos: list[Movimiento] = []
    for fila in tabla:
        if fila is None or len(fila) < 5:
            continue

        celda_fecha = (fila[0] or "").strip()
        m_fecha = _RE_FECHA.match(celda_fecha)
        if not m_fecha:
            continue

        descripcion = (fila[2] or "").strip()
        if "Resumen de Movimientos" in descripcion:
            break

        if any(ign.lower() in descripcion.lower() for ign in _IGNORAR):
            continue

        dia, mes = m_fecha.group(1), m_fecha.group(2)
        fecha_str = f"{anio}-{mes}-{dia}"
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            continue

        referencia = (fila[1] or "").strip()
        cargos = _parsear_monto(fila[3] if len(fila) > 3 else "")
        abonos = _parsear_monto(fila[4] if len(fila) > 4 else "")

        if cargos == 0 and abonos == 0:
            continue

        monto = abonos - cargos
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


def _extraer_de_texto(texto: str, anio: int) -> list[Movimiento]:
    """
    Fallback: extrae movimientos por regex sobre el texto plano.
    Útil cuando pdfplumber.extract_table() no detecta la tabla.
    """
    movimientos: list[Movimiento] = []
    for m in _RE_MOV_TEXTO.finditer(texto):
        fecha_dd_mm = m.group(1)   # "02/02"
        referencia = m.group(2)
        descripcion = m.group(3).strip()
        cargo_str = m.group(4)
        abono_str = m.group(5)

        if "Resumen" in descripcion:
            break

        if any(ign.lower() in descripcion.lower() for ign in _IGNORAR):
            continue

        dia, mes = fecha_dd_mm.split("/")
        fecha_str = f"{anio}-{mes}-{dia}"
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            continue

        cargos = _parsear_monto(cargo_str)
        abonos = _parsear_monto(abono_str)
        if cargos == 0 and abonos == 0:
            continue

        monto = abonos - cargos

        movimientos.append(Movimiento(
            fecha=fecha_str,
            descripcion=descripcion,
            monto=monto,
            moneda="CLP",
            monto_clp=monto,
            fuente="itau_cta_cte",
            referencia=referencia,
            confianza_extraccion=1.0,
            raw=m.group(0),
        ))
    return movimientos


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
            # ── Intentar extracción estructurada primero ──────────────────────
            tabla = page.extract_table()
            if tabla:
                filas = _extraer_de_tabla(tabla, anio)
                if filas:
                    movimientos.extend(filas)
                    continue  # tabla funcionó → siguiente página

            # ── Fallback: extracción por texto plano ──────────────────────────
            texto_pagina = page.extract_text() or ""
            if texto_pagina:
                movimientos.extend(_extraer_de_texto(texto_pagina, anio))

    return movimientos
