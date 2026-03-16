"""
Extractor para Estado de Cuenta TC Nacional Itaú (CLP).

Columnas: Lugar | Fecha | Código | Descripción |
          Monto Op | Monto Total | N°Cuota | Valor Cuota
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

_EXCLUIR_DESC = {
    "TOTAL TARJETA", "TOTAL OPERACIONES", "TOTAL PAGOS",
    "TOTAL PAT", "TOTAL", "IMPUESTO DECRETO LEY",
    "COMISION ADMINISTRACION", "INTERESES ROTATIVOS",
    "SALDO ANTERIOR", "SALDO",
}
_EXCLUIR_SECCIONES = {
    "4. INFORMACION COMPRAS EN CUOTAS",
    "INFORMACION COMPRAS EN CUOTAS",
    "4. INFORMACIÓN COMPRAS EN CUOTAS",
}

_RE_FECHA_CORTA = re.compile(r"^(\d{2})/(\d{2})/(\d{2,4})$")


def _parsear_monto(texto: str) -> float:
    if not texto or texto.strip() in ("", "-"):
        return 0.0
    limpio = re.sub(r"[^0-9,.\-]", "", texto.strip())
    limpio = limpio.replace(".", "").replace(",", "")
    try:
        return float(limpio)
    except ValueError:
        return 0.0


def _parsear_fecha(texto: str) -> str | None:
    m = _RE_FECHA_CORTA.match((texto or "").strip())
    if not m:
        return None
    dia, mes, anio_raw = m.group(1), m.group(2), m.group(3)
    if len(anio_raw) == 2:
        anio = "20" + anio_raw
    else:
        anio = anio_raw
    fecha_str = f"{anio}-{mes}-{dia}"
    try:
        datetime.strptime(fecha_str, "%Y-%m-%d")
        return fecha_str
    except ValueError:
        return None


def extraer(filepath: str) -> list[Movimiento]:
    """Extrae movimientos de un estado de cuenta TC Nacional Itaú."""
    if not _PDFPLUMBER_OK:
        raise ImportError("pdfplumber no está instalado.")

    movimientos: list[Movimiento] = []
    en_periodo_actual = False
    en_seccion_excluida = False

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            tabla = page.extract_table()
            if tabla is None:
                continue

            for fila in tabla:
                if fila is None:
                    continue

                texto_fila = " ".join(str(c or "").strip() for c in fila).strip()

                # Detectar inicio sección periodo actual
                if "2. PERIODO ACTUAL" in texto_fila or "PERIODO ACTUAL" in texto_fila:
                    en_periodo_actual = True
                    en_seccion_excluida = False
                    continue

                # Detectar secciones excluidas
                if any(sec in texto_fila for sec in _EXCLUIR_SECCIONES):
                    en_seccion_excluida = True
                    continue

                # Detectar fin del periodo actual
                if en_periodo_actual and re.match(r"^[3-9]\.", texto_fila):
                    if "4." not in texto_fila:  # no marcar 4 dos veces
                        en_periodo_actual = False
                    continue

                if not en_periodo_actual or en_seccion_excluida:
                    continue

                if len(fila) < 4:
                    continue

                # Columna fecha (índice 1 para TC nacional)
                fecha_str = _parsear_fecha(fila[1] if len(fila) > 1 else "")
                if not fecha_str:
                    continue

                descripcion = (fila[3] if len(fila) > 3 else "").strip() or \
                              (fila[0] if fila[0] else "").strip()

                # Excluir por descripción
                if any(exc.lower() in descripcion.upper() for exc in _EXCLUIR_DESC):
                    continue

                # Monto total (índice 5)
                monto_raw = fila[5] if len(fila) > 5 else (fila[4] if len(fila) > 4 else "")
                monto_abs = _parsear_monto(monto_raw)

                if monto_abs == 0:
                    continue

                # Pagos tienen descripción "PAGO" o monto aparece como abono
                es_pago = "PAGO" in descripcion.upper() or "ABONO" in descripcion.upper()
                monto = monto_abs if es_pago else -monto_abs

                codigo = (fila[2] if len(fila) > 2 else "").strip()
                raw = "|".join(str(c or "") for c in fila)

                movimientos.append(Movimiento(
                    fecha=fecha_str,
                    descripcion=descripcion,
                    monto=monto,
                    moneda="CLP",
                    monto_clp=monto,
                    fuente="itau_tc_nacional",
                    referencia=codigo,
                    confianza_extraccion=1.0,
                    raw=raw,
                ))

    return movimientos
