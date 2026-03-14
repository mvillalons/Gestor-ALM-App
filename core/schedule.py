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


# ---------------------------------------------------------------------------
# Generación de tabla de desarrollo — Crédito consumo
# ---------------------------------------------------------------------------


def gen_credito_consumo(
    monto: float,
    n_cuotas: int,
    tasa_anual: float,
    fecha_primer_pago: date | str,
    moneda: str = "CLP",
    id_posicion: str = "",
) -> pd.DataFrame:
    """Genera la tabla de desarrollo mensual de un crédito de consumo.

    Aplica el mismo método francés (cuota fija) que :func:`gen_hipotecario`.

    Args:
        monto: Monto total del crédito. Debe ser > 0.
        n_cuotas: Número de cuotas mensuales. Debe ser > 0.
        tasa_anual: Tasa de interés anual como decimal (p. ej. 0.12 = 12 %).
            Puede ser 0 (crédito sin interés).
        fecha_primer_pago: Mes de la primera cuota.
        moneda: Código de moneda (``"CLP"``, ``"UF"``, ``"USD"``, …).
        id_posicion: ID de la posición para poblar la columna ``ID_Posicion``.

    Returns:
        :class:`pandas.DataFrame` con columnas estándar de tabla de desarrollo.
        Contiene exactamente ``n_cuotas`` filas.

    Raises:
        ValueError: Si algún parámetro es inválido.
    """
    if monto <= 0:
        raise ValueError(f"monto debe ser mayor que cero, se recibió {monto}.")
    if n_cuotas <= 0:
        raise ValueError(f"n_cuotas debe ser mayor que cero, se recibió {n_cuotas}.")
    if tasa_anual < 0:
        raise ValueError(f"tasa_anual no puede ser negativa, se recibió {tasa_anual}.")

    tasa_mensual: float = tasa_anual / 12
    fecha: date = _parse_fecha(fecha_primer_pago)
    rows = _tabla_frances(monto, tasa_mensual, n_cuotas, fecha, moneda, id_posicion)
    return pd.DataFrame(rows, columns=COLS_TABLA_DESARROLLO)


# ---------------------------------------------------------------------------
# Generación de tabla de desarrollo — Colegio / cuotas educacionales
# ---------------------------------------------------------------------------


def gen_colegio(
    monto_anual: float,
    cuotas_por_ano: int,
    anos_restantes: int,
    meses_de_pago: list[int],
    fecha_inicio: date | str,
    moneda: str = "CLP",
    id_posicion: str = "",
) -> pd.DataFrame:
    """Genera la tabla de desarrollo para compromisos de colegio o arancel educacional.

    Los pagos se distribuyen en los meses indicados por ``meses_de_pago``,
    durante ``anos_restantes`` años calendario comenzando desde ``fecha_inicio``.
    Solo se generan filas para meses >= ``fecha_inicio``.

    El saldo de cada fila representa el compromiso monetario futuro restante.

    Args:
        monto_anual: Monto total anual del compromiso. Debe ser > 0.
        cuotas_por_ano: Número de cuotas que se pagan por año. Debe ser > 0
            y <= ``len(meses_de_pago)``.
        anos_restantes: Número de años calendario con pagos pendientes
            (incluyendo el año de inicio si aún quedan pagos). Debe ser > 0.
        meses_de_pago: Lista con los números de mes (1–12) en que se realizan
            los pagos cada año. Se toman los primeros ``cuotas_por_ano`` meses
            en orden ascendente.
        fecha_inicio: Fecha desde la cual se generan filas (inclusive).
        moneda: Código de moneda.
        id_posicion: ID de la posición.

    Returns:
        :class:`pandas.DataFrame` con columnas estándar. Puede tener menos
        filas que ``cuotas_por_ano * anos_restantes`` si algunos meses del
        primer año caen antes de ``fecha_inicio``.
        Retorna DataFrame vacío si no hay pagos futuros.

    Raises:
        ValueError: Si algún parámetro es inválido.
    """
    if monto_anual <= 0:
        raise ValueError(
            f"monto_anual debe ser mayor que cero, se recibió {monto_anual}."
        )
    if cuotas_por_ano <= 0:
        raise ValueError(
            f"cuotas_por_ano debe ser mayor que cero, se recibió {cuotas_por_ano}."
        )
    if not meses_de_pago:
        raise ValueError("meses_de_pago no puede estar vacío.")
    if cuotas_por_ano > len(meses_de_pago):
        raise ValueError(
            f"cuotas_por_ano ({cuotas_por_ano}) no puede superar "
            f"len(meses_de_pago) ({len(meses_de_pago)})."
        )
    if anos_restantes <= 0:
        raise ValueError(
            f"anos_restantes debe ser mayor que cero, se recibió {anos_restantes}."
        )

    fecha: date = _parse_fecha(fecha_inicio)
    cuota: float = monto_anual / cuotas_por_ano
    meses_sorted: list[int] = sorted(meses_de_pago)[:cuotas_por_ano]

    # Recopilar todas las fechas de pago futuras (>= fecha)
    fechas_pago: list[date] = []
    for offset in range(anos_restantes):
        anio = fecha.year + offset
        for mes in meses_sorted:
            d = date(anio, mes, 1)
            if d >= fecha:
                fechas_pago.append(d)

    if not fechas_pago:
        return pd.DataFrame(columns=COLS_TABLA_DESARROLLO)

    # El saldo inicial de la primera fila = compromiso total futuro
    saldo: float = round(len(fechas_pago) * cuota, 6)
    rows: list[dict] = []

    for i, d in enumerate(fechas_pago):
        saldo_ini = saldo
        saldo_final = round(saldo_ini - cuota, 6)
        rows.append(
            {
                "ID_Posicion": id_posicion,
                "Periodo": d.strftime("%Y-%m"),
                "Saldo_Inicial": round(saldo_ini, 6),
                "Flujo_Periodo": round(-cuota, 6),
                "Rendimiento_Costo": 0.0,
                "Amortizacion": round(cuota, 6),
                "Saldo_Final": saldo_final,
                "Moneda": moneda,
                "Tipo_Flujo": "calculado",
                "Notas": (
                    f"Cuota colegio {d.year} "
                    f"({i % cuotas_por_ano + 1}/{cuotas_por_ano})"
                ),
            }
        )
        saldo = saldo_final

    return pd.DataFrame(rows, columns=COLS_TABLA_DESARROLLO)


# ---------------------------------------------------------------------------
# Generación de tabla de desarrollo — Tarjeta de crédito
# ---------------------------------------------------------------------------


def gen_tarjeta(
    deuda_total: float,
    pago_mensual: float,
    tasa_mensual: float,
    fecha_inicio: date | str,
    moneda: str = "CLP",
    id_posicion: str = "",
    max_meses: int = 360,
) -> pd.DataFrame:
    """Genera la tabla de desarrollo para deuda de tarjeta de crédito.

    Calcula un plan de amortización con cuota fija mensual sobre un saldo
    inicial, con la tasa mensual indicada. Se detiene cuando el saldo llega
    a cero o se alcanza ``max_meses``.

    Convención de signos:
        - ``Flujo_Periodo`` negativo (egreso del usuario).
        - ``Rendimiento_Costo`` negativo (costo financiero).
        - ``Amortizacion`` positiva (capital amortizado).

    Args:
        deuda_total: Saldo deudor actual. Debe ser > 0.
        pago_mensual: Monto a pagar cada mes. Debe ser mayor que los intereses
            del primer período para que la deuda se amortice efectivamente.
        tasa_mensual: Tasa de interés mensual como decimal (p. ej. 0.02 = 2 %).
            Puede ser 0 (sin interés).
        fecha_inicio: Mes del primer pago.
        moneda: Código de moneda.
        id_posicion: ID de la posición.
        max_meses: Tope de iteraciones para evitar ciclos infinitos (defecto 360).

    Returns:
        :class:`pandas.DataFrame` con columnas estándar de tabla de desarrollo.

    Raises:
        ValueError: Si algún parámetro es inválido o el pago no cubre los
            intereses del primer período.
    """
    if deuda_total <= 0:
        raise ValueError(
            f"deuda_total debe ser mayor que cero, se recibió {deuda_total}."
        )
    if pago_mensual <= 0:
        raise ValueError(
            f"pago_mensual debe ser mayor que cero, se recibió {pago_mensual}."
        )
    if tasa_mensual < 0:
        raise ValueError(
            f"tasa_mensual no puede ser negativa, se recibió {tasa_mensual}."
        )

    interes_inicial = deuda_total * tasa_mensual
    if tasa_mensual > 0 and pago_mensual <= interes_inicial:
        raise ValueError(
            f"pago_mensual ({pago_mensual:,.2f}) debe ser mayor que el interés "
            f"inicial ({interes_inicial:,.2f}) para amortizar la deuda."
        )

    fecha: date = _parse_fecha(fecha_inicio)
    saldo: float = deuda_total
    nota = (
        f"Pago mensual {moneda} {pago_mensual:,.0f} | "
        f"tasa mensual {tasa_mensual * 100:.2f}%"
    )
    rows: list[dict] = []

    for _ in range(max_meses):
        if saldo <= 0.01:
            break
        saldo_ini = saldo
        interes = saldo_ini * tasa_mensual
        pago = min(pago_mensual, saldo_ini + interes)
        amort = pago - interes
        saldo = max(saldo_ini - amort, 0.0)

        rows.append(
            {
                "ID_Posicion": id_posicion,
                "Periodo": fecha.strftime("%Y-%m"),
                "Saldo_Inicial": round(saldo_ini, 6),
                "Flujo_Periodo": round(-pago, 6),
                "Rendimiento_Costo": round(-interes, 6),
                "Amortizacion": round(amort, 6),
                "Saldo_Final": round(saldo, 6),
                "Moneda": moneda,
                "Tipo_Flujo": "calculado",
                "Notas": nota,
            }
        )
        fecha = _next_month(fecha)

    return pd.DataFrame(rows, columns=COLS_TABLA_DESARROLLO)


# ---------------------------------------------------------------------------
# Generación de tabla de desarrollo — AFP
# ---------------------------------------------------------------------------


def gen_afp(
    saldo_actual: float,
    aporte_mensual: float,
    tasa_anual: float,
    edad_actual: float,
    edad_jubilacion: float,
    fecha_inicio: date | str,
    moneda: str = "CLP",
    id_posicion: str = "",
) -> pd.DataFrame:
    """Genera la proyección mensual del saldo AFP hasta la jubilación.

    El saldo crece cada mes por rentabilidad (``tasa_anual / 12``) y por el
    aporte mensual. Convención de signos:
        - ``Flujo_Periodo`` negativo (el aporte es un egreso del usuario).
        - ``Rendimiento_Costo`` positivo (crecimiento del fondo).
        - ``Saldo_Final`` crece con el tiempo.

    Args:
        saldo_actual: Saldo AFP al inicio de la proyección. Debe ser >= 0.
        aporte_mensual: Aporte mensual obligatorio + voluntario. Debe ser >= 0.
        tasa_anual: Rentabilidad anual esperada del fondo como decimal.
            Puede ser 0 (sin rentabilidad).
        edad_actual: Edad actual del afiliado en años (acepta decimales).
        edad_jubilacion: Edad de jubilación objetivo. Debe ser > ``edad_actual``.
        fecha_inicio: Mes del primer aporte proyectado.
        moneda: Código de moneda (típicamente ``"CLP"``).
        id_posicion: ID de la posición.

    Returns:
        :class:`pandas.DataFrame` con columnas estándar.
        Número de filas = ``round((edad_jubilacion - edad_actual) * 12)``.

    Raises:
        ValueError: Si los parámetros son inválidos.
    """
    if saldo_actual < 0:
        raise ValueError(
            f"saldo_actual no puede ser negativo, se recibió {saldo_actual}."
        )
    if aporte_mensual < 0:
        raise ValueError(
            f"aporte_mensual no puede ser negativo, se recibió {aporte_mensual}."
        )
    if tasa_anual < 0:
        raise ValueError(
            f"tasa_anual no puede ser negativa, se recibió {tasa_anual}."
        )
    if edad_jubilacion <= edad_actual:
        raise ValueError(
            f"edad_jubilacion ({edad_jubilacion}) debe ser mayor que "
            f"edad_actual ({edad_actual})."
        )

    plazo: int = max(1, round((edad_jubilacion - edad_actual) * 12))
    tasa_mensual: float = tasa_anual / 12
    fecha: date = _parse_fecha(fecha_inicio)
    saldo: float = float(saldo_actual)
    nota = (
        f"Aporte {moneda} {aporte_mensual:,.0f} | "
        f"rentabilidad {tasa_anual * 100:.1f}% anual"
    )
    rows: list[dict] = []

    for _ in range(plazo):
        saldo_ini = saldo
        rendimiento = round(saldo_ini * tasa_mensual, 2)
        saldo = round(saldo_ini + rendimiento + aporte_mensual, 2)

        rows.append(
            {
                "ID_Posicion": id_posicion,
                "Periodo": fecha.strftime("%Y-%m"),
                "Saldo_Inicial": saldo_ini,
                "Flujo_Periodo": round(-aporte_mensual, 2),  # egreso del usuario
                "Rendimiento_Costo": rendimiento,             # positivo: crecimiento
                "Amortizacion": 0.0,
                "Saldo_Final": saldo,
                "Moneda": moneda,
                "Tipo_Flujo": "calculado",
                "Notas": nota,
            }
        )
        fecha = _next_month(fecha)

    return pd.DataFrame(rows, columns=COLS_TABLA_DESARROLLO)


# ---------------------------------------------------------------------------
# Análisis consolidado — flujo neto mensual
# ---------------------------------------------------------------------------


def flujo_neto_mensual(
    tablas: list[pd.DataFrame],
    ingreso_mensual: float,
    valor_uf: float = 39_700.0,
    valor_usd: float = 950.0,
) -> pd.DataFrame:
    """Consolida el flujo neto mensual sumando pagos de pasivos e ingreso.

    Agrega los ``Flujo_Periodo`` de todas las tablas proporcionadas (negativos
    para pasivos) y los combina con el ingreso mensual constante del usuario.

    Normalización de moneda: si alguna tabla tiene flujos en UF o USD, éstos
    se convierten a CLP antes de sumar usando ``valor_uf`` y ``valor_usd``.
    La columna ``Moneda`` de cada tabla se usa para determinar la conversión.
    Si la columna ``Moneda`` no existe en una tabla, sus flujos se tratan
    como CLP (sin conversión).

    ``ingreso_mensual`` debe expresarse ya en CLP (o en la misma moneda base
    de referencia) — la conversión del ingreso es responsabilidad del llamador.

    Args:
        tablas: Lista de DataFrames de tablas de desarrollo.
            Cada uno debe tener las columnas ``Periodo`` y ``Flujo_Periodo``.
            La columna ``Moneda`` es opcional; si falta se asume ``"CLP"``.
        ingreso_mensual: Ingreso mensual del usuario en CLP (constante en
            todo el horizonte). Debe ser >= 0.
        valor_uf: Tipo de cambio UF → CLP usado para normalizar flujos en UF.
            Por defecto :data:`~core.calculator.VALOR_UF_DEFAULT` (39 700).
        valor_usd: Tipo de cambio USD → CLP usado para normalizar flujos en USD.
            Por defecto :data:`~core.calculator.VALOR_USD_DEFAULT` (950).

    Returns:
        :class:`pandas.DataFrame` con columnas:
            - ``Periodo`` (YYYY-MM, ordenado ascendente)
            - ``Flujo_Pasivos`` (suma de pagos en CLP, negativo para deudas)
            - ``Ingreso`` (= ``ingreso_mensual`` para todos los períodos)
            - ``Flujo_Neto`` (= ``Ingreso`` + ``Flujo_Pasivos``)

        Retorna DataFrame vacío (con las columnas) si ``tablas`` está vacío.

    Raises:
        ValueError: Si ``ingreso_mensual`` es negativo.
    """
    _cols_out = ["Periodo", "Flujo_Pasivos", "Ingreso", "Flujo_Neto"]

    if ingreso_mensual < 0:
        raise ValueError(
            f"ingreso_mensual no puede ser negativo, se recibió {ingreso_mensual}."
        )

    if not tablas:
        return pd.DataFrame(columns=_cols_out)

    def _a_clp(flujo: float, moneda: str) -> float:
        """Convierte un flujo a CLP según su moneda."""
        if moneda == "UF":
            return flujo * valor_uf
        if moneda == "USD":
            return flujo * valor_usd
        return float(flujo)  # CLP u otra moneda → sin cambio

    frames: list[pd.DataFrame] = []
    for t in tablas:
        chunk = t[["Periodo", "Flujo_Periodo"]].copy()
        # Usar la columna Moneda si existe; si no, asumir CLP
        moneda_serie = (
            t["Moneda"] if "Moneda" in t.columns
            else pd.Series(["CLP"] * len(t), index=t.index)
        )
        chunk["Flujo_CLP"] = [
            _a_clp(f, m) for f, m in zip(chunk["Flujo_Periodo"], moneda_serie)
        ]
        frames.append(chunk[["Periodo", "Flujo_CLP"]])

    combined = pd.concat(frames, ignore_index=True)
    grouped = (
        combined.groupby("Periodo", sort=True)["Flujo_CLP"]
        .sum()
        .reset_index()
        .rename(columns={"Flujo_CLP": "Flujo_Pasivos"})
    )
    grouped["Ingreso"] = ingreso_mensual
    grouped["Flujo_Neto"] = grouped["Ingreso"] + grouped["Flujo_Pasivos"]

    return grouped[_cols_out].reset_index(drop=True)
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
