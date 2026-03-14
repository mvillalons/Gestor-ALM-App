"""core/schedule.py — Generación de tablas de desarrollo mensual.

Motor puro (sin dependencias de Streamlit). Cada función retorna un DataFrame
con las columnas estándar de Tabla_[ID_Posicion].csv.

Columnas estándar:
    ID_Posicion, Periodo (YYYY-MM), Saldo_Inicial, Flujo_Periodo,
    Rendimiento_Costo, Amortizacion, Saldo_Final, Moneda, Tipo_Flujo, Notas

Convención de signos para pasivos:
    - Flujo_Periodo     → negativo (egreso del usuario)
    - Rendimiento_Costo → negativo (costo financiero / interés pagado)
    - Amortizacion      → positivo (capital que reduce el saldo)
    - Saldo_Inicial / Saldo_Final → positivos (saldo adeudado)
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

COLS_TABLA_DESARROLLO: list[str] = [
    "ID_Posicion",
    "Periodo",
    "Saldo_Inicial",
    "Flujo_Periodo",
    "Rendimiento_Costo",
    "Amortizacion",
    "Saldo_Final",
    "Moneda",
    "Tipo_Flujo",
    "Notas",
]

# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _parse_fecha(fecha: date | str) -> date:
    """Convierte un string 'YYYY-MM' o 'YYYY-MM-DD' a :class:`datetime.date`.

    Args:
        fecha: Fecha como objeto :class:`datetime.date` o string.

    Returns:
        Objeto :class:`datetime.date` con día 1.
    """
    if isinstance(fecha, date):
        return fecha.replace(day=1)
    parts = str(fecha).strip().split("-")
    if len(parts) >= 2:
        return date(int(parts[0]), int(parts[1]), 1)
    raise ValueError(
        f"Formato de fecha no reconocido: '{fecha}'. Use 'YYYY-MM' o 'YYYY-MM-DD'."
    )


def _next_month(d: date) -> date:
    """Retorna el primer día del mes siguiente.

    Args:
        d: Fecha de referencia.

    Returns:
        Primer día del mes siguiente.
    """
    month = d.month + 1
    year = d.year
    if month > 12:
        month = 1
        year += 1
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# Generación de tabla de desarrollo — Hipotecario
# ---------------------------------------------------------------------------


def gen_hipotecario(
    capital: float,
    tasa_anual: float,
    plazo_meses: int,
    fecha_inicio: date | str,
    moneda: str = "CLP",
    metodo: Literal["frances", "aleman"] = "frances",
    id_posicion: str = "",
) -> pd.DataFrame:
    """Genera la tabla de desarrollo mensual de un crédito hipotecario.

    Admite dos métodos de amortización:

    * **francés** (cuota fija): la cuota mensual es constante. El interés
      va decreciendo y la amortización de capital aumenta con el tiempo.
    * **alemán** (amortización constante): la amortización de capital es
      fija cada mes; la cuota total decrece porque los intereses bajan.

    Si ``moneda == "UF"``, el capital y todos los saldos quedan expresados
    en UF; el motor **no** convierte a CLP.

    Args:
        capital: Monto del crédito en la moneda indicada. Debe ser > 0.
        tasa_anual: Tasa de interés anual como decimal (p. ej. 0.05 = 5 %).
            Puede ser 0 (préstamo sin interés).
        plazo_meses: Número de cuotas mensuales. Debe ser > 0.
        fecha_inicio: Mes de la **primera cuota**. Acepta :class:`datetime.date`,
            ``"YYYY-MM"`` o ``"YYYY-MM-DD"``.
        moneda: Código de moneda (``"CLP"``, ``"UF"``, ``"USD"``, …).
        metodo: ``"frances"`` (por defecto) o ``"aleman"``.
        id_posicion: ID de la posición para poblar la columna ``ID_Posicion``.

    Returns:
        :class:`pandas.DataFrame` con columnas estándar de tabla de desarrollo.
        Contiene exactamente ``plazo_meses`` filas.

    Raises:
        ValueError: Si algún parámetro es inválido.

    Examples:
        >>> from datetime import date
        >>> df = gen_hipotecario(
        ...     capital=5_000_000,
        ...     tasa_anual=0.06,
        ...     plazo_meses=240,
        ...     fecha_inicio=date(2026, 4, 1),
        ...     moneda="CLP",
        ...     id_posicion="PAS_HIP_001",
        ... )
        >>> df.shape
        (240, 10)
        >>> round(df["Saldo_Final"].iloc[-1], 2)
        0.0
    """
    # --- Validaciones ---
    if capital <= 0:
        raise ValueError(f"capital debe ser mayor que cero, se recibió {capital}.")
    if tasa_anual < 0:
        raise ValueError(
            f"tasa_anual no puede ser negativa, se recibió {tasa_anual}."
        )
    if plazo_meses <= 0:
        raise ValueError(
            f"plazo_meses debe ser mayor que cero, se recibió {plazo_meses}."
        )
    if metodo not in ("frances", "aleman"):
        raise ValueError(
            f"Método no reconocido: '{metodo}'. Use 'frances' o 'aleman'."
        )

    tasa_mensual: float = tasa_anual / 12
    fecha: date = _parse_fecha(fecha_inicio)

    if metodo == "frances":
        rows = _tabla_frances(capital, tasa_mensual, plazo_meses, fecha, moneda, id_posicion)
    else:
        rows = _tabla_aleman(capital, tasa_mensual, plazo_meses, fecha, moneda, id_posicion)

    return pd.DataFrame(rows, columns=COLS_TABLA_DESARROLLO)


# ---------------------------------------------------------------------------
# Algoritmos de amortización
# ---------------------------------------------------------------------------


def _tabla_frances(
    capital: float,
    tasa_mensual: float,
    plazo_meses: int,
    fecha_inicio: date,
    moneda: str,
    id_posicion: str,
) -> list[dict]:
    """Método francés: cuota mensual constante.

    Fórmula de la cuota:
        C = P * r * (1 + r)^n / ((1 + r)^n - 1)

    Cuando ``tasa_mensual == 0``:
        C = P / n
    """
    if tasa_mensual == 0:
        cuota = capital / plazo_meses
    else:
        factor = (1 + tasa_mensual) ** plazo_meses
        cuota = capital * tasa_mensual * factor / (factor - 1)

    nota = f"Cuota fija {moneda} {cuota:,.2f} | Método francés"
    saldo = capital
    fecha = fecha_inicio
    rows: list[dict] = []

    for i in range(plazo_meses):
        saldo_inicial = saldo
        interes = saldo_inicial * tasa_mensual

        # Última cuota: ajuste para cerrar el saldo exactamente en 0
        if i == plazo_meses - 1:
            amort = saldo_inicial
        else:
            amort = cuota - interes

        saldo_final = max(saldo_inicial - amort, 0.0)

        rows.append(
            {
                "ID_Posicion": id_posicion,
                "Periodo": fecha.strftime("%Y-%m"),
                "Saldo_Inicial": round(saldo_inicial, 6),
                "Flujo_Periodo": round(-(amort + interes), 6),
                "Rendimiento_Costo": round(-interes, 6),
                "Amortizacion": round(amort, 6),
                "Saldo_Final": round(saldo_final, 6),
                "Moneda": moneda,
                "Tipo_Flujo": "calculado",
                "Notas": nota,
            }
        )

        saldo = saldo_final
        fecha = _next_month(fecha)

    return rows


def _tabla_aleman(
    capital: float,
    tasa_mensual: float,
    plazo_meses: int,
    fecha_inicio: date,
    moneda: str,
    id_posicion: str,
) -> list[dict]:
    """Método alemán: amortización de capital constante.

    La amortización fija es:
        A = P / n

    La cuota total decrece con el tiempo porque los intereses disminuyen.
    """
    amort_constante = capital / plazo_meses
    nota_base = f"Amortización fija {moneda} {amort_constante:,.2f} | Método alemán"
    saldo = capital
    fecha = fecha_inicio
    rows: list[dict] = []

    for i in range(plazo_meses):
        saldo_inicial = saldo
        interes = saldo_inicial * tasa_mensual

        # Última cuota: amortizar exactamente el saldo restante
        amort = saldo_inicial if i == plazo_meses - 1 else amort_constante
        saldo_final = max(saldo_inicial - amort, 0.0)

        rows.append(
            {
                "ID_Posicion": id_posicion,
                "Periodo": fecha.strftime("%Y-%m"),
                "Saldo_Inicial": round(saldo_inicial, 6),
                "Flujo_Periodo": round(-(amort + interes), 6),
                "Rendimiento_Costo": round(-interes, 6),
                "Amortizacion": round(amort, 6),
                "Saldo_Final": round(saldo_final, 6),
                "Moneda": moneda,
                "Tipo_Flujo": "calculado",
                "Notas": nota_base,
            }
        )

        saldo = saldo_final
        fecha = _next_month(fecha)

    return rows
