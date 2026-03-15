"""core/calculator.py — Métricas financieras de Capa 1, 2 y 3.

Motor puro (sin dependencias de Streamlit). Todas las funciones reciben
parámetros escalares y retornan escalares — sin accesos a Drive ni estado.

Convenciones de signos:
    - Los valores monetarios son positivos (ingreso, activos, cuotas).
    - ``gap_fondo`` es positivo cuando falta dinero; negativo cuando hay superávit.
    - ``margen_libre`` puede ser negativo (gastos > ingreso).
    - Los ratios (carga_financiera, posicion_vida) son adimensionales.

Benchmarks de referencia:
    - ``carga_financiera`` saludable: < 0.35  (35 % del ingreso)
    - ``posicion_vida_v1`` mínimo recomendado: 3.0  (3 meses de esenciales)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MESES_META_DEFAULT: int = 3
BENCHMARK_CARGA_FINANCIERA: float = 0.35

# Mapeo tipo de pasivo → ID del bucket sugerido (Capa 2-C)
_BUCKET_SUGERIDO_MAP: dict[str, str] = {
    "Hipotecario":     "GAS_ESE_BUCKET",
    "Colegio":         "GAS_ESE_BUCKET",
    "Crédito consumo": "GAS_IMP_BUCKET",
    "Tarjeta":         "GAS_IMP_BUCKET",
    "APV":             "GAS_ASP_BUCKET",
}


# ---------------------------------------------------------------------------
# Capa 1 — Claridad
# ---------------------------------------------------------------------------


def margen_libre(
    ingreso: float,
    esenciales: float,
    importantes: float,
    aspiraciones: float,
) -> float:
    """Calcula el margen libre mensual después de todos los gastos.

    Args:
        ingreso: Ingreso mensual total.
        esenciales: Suma de gastos esenciales del mes.
        importantes: Suma de gastos importantes del mes.
        aspiraciones: Suma de gastos de aspiración del mes.

    Returns:
        ``ingreso - (esenciales + importantes + aspiraciones)``.
        Puede ser negativo si los gastos superan el ingreso.
    """
    return ingreso - (esenciales + importantes + aspiraciones)


def meta_fondo_reserva(
    esenciales: float,
    meses_meta: int = MESES_META_DEFAULT,
) -> float:
    """Calcula el monto objetivo del fondo de reserva.

    Args:
        esenciales: Gasto mensual en ítems esenciales.
        meses_meta: Número de meses de cobertura deseados. Por defecto 3.

    Returns:
        ``esenciales * meses_meta``.

    Raises:
        ValueError: Si ``meses_meta`` es menor o igual a cero.
    """
    if meses_meta <= 0:
        raise ValueError(
            f"meses_meta debe ser mayor que cero, se recibió {meses_meta}."
        )
    return esenciales * meses_meta


def gap_fondo(meta: float, activo_liquido: float) -> float:
    """Calcula la brecha entre la meta del fondo y el activo líquido actual.

    Args:
        meta: Monto objetivo del fondo de reserva (de :func:`meta_fondo_reserva`).
        activo_liquido: Valor total de activos líquidos disponibles.

    Returns:
        ``meta - activo_liquido``.
        Positivo → falta dinero para alcanzar la meta.
        Negativo → el fondo ya supera la meta (superávit).
    """
    return meta - activo_liquido


def meses_para_fondo(gap: float, margen: float) -> float | None:
    """Estima cuántos meses se necesitan para cubrir la brecha del fondo.

    Args:
        gap: Brecha del fondo (de :func:`gap_fondo`).
        margen: Margen libre mensual (de :func:`margen_libre`).

    Returns:
        ``0.0`` si el gap ya es cero o negativo (fondo cubierto).
        ``None`` si el margen libre es menor o igual a cero (no es posible
        estimar el plazo porque no hay capacidad de ahorro).
        ``gap / margen`` en caso contrario.
    """
    if gap <= 0:
        return 0.0
    if margen <= 0:
        return None
    return gap / margen


def posicion_vida_v1(activo_liquido: float, esenciales: float) -> float:
    """Calcula la Posición de Vida versión 1 (Capa 1).

    Mide cuántos meses de gastos esenciales están cubiertos por los
    activos líquidos actuales.

    Args:
        activo_liquido: Valor total de activos líquidos.
        esenciales: Gasto mensual en ítems esenciales.

    Returns:
        ``activo_liquido / esenciales``.
        Un valor de 3.0 significa 3 meses de cobertura.

    Raises:
        ValueError: Si ``esenciales`` es cero o negativo.
    """
    if esenciales <= 0:
        raise ValueError(
            f"esenciales debe ser mayor que cero, se recibió {esenciales}."
        )
    return activo_liquido / esenciales


# ---------------------------------------------------------------------------
# Capa 2 — Control
# ---------------------------------------------------------------------------


def carga_financiera(cuotas_pasivos: list[float], ingreso: float) -> float:
    """Calcula el ratio de carga financiera mensual.

    Mide qué fracción del ingreso se destina al servicio de deuda.
    El benchmark saludable es inferior a :data:`BENCHMARK_CARGA_FINANCIERA` (0.35).

    Args:
        cuotas_pasivos: Lista de cuotas mensuales de todos los pasivos.
            Puede estar vacía (ratio = 0).
        ingreso: Ingreso mensual total. Debe ser > 0.

    Returns:
        ``sum(cuotas_pasivos) / ingreso``.

    Raises:
        ValueError: Si ``ingreso`` es cero o negativo.
    """
    if ingreso <= 0:
        raise ValueError(
            f"ingreso debe ser mayor que cero, se recibió {ingreso}."
        )
    return sum(cuotas_pasivos) / ingreso


def posicion_vida_v2(
    activo_liquido: float,
    esenciales: float,
    cuotas_pasivos: list[float],
) -> float:
    """Calcula la Posición de Vida versión 2 (Capa 2).

    Extiende v1 incorporando el servicio de deuda en el denominador,
    dando una imagen más conservadora de la cobertura.

    Args:
        activo_liquido: Valor total de activos líquidos.
        esenciales: Gasto mensual en ítems esenciales.
        cuotas_pasivos: Lista de cuotas mensuales de todos los pasivos.

    Returns:
        ``activo_liquido / (esenciales + sum(cuotas_pasivos))``.

    Raises:
        ValueError: Si ``esenciales + sum(cuotas_pasivos)`` es cero o negativo.
    """
    denominador = esenciales + sum(cuotas_pasivos)
    if denominador <= 0:
        raise ValueError(
            f"La suma de esenciales y cuotas debe ser mayor que cero, "
            f"se recibió {denominador}."
        )
    return activo_liquido / denominador


def mes_stress(flujo_neto: float) -> bool:
    """Determina si un período tiene flujo neto negativo (mes en stress).

    Args:
        flujo_neto: Flujo neto del período (ingresos menos egresos totales).

    Returns:
        ``True`` si ``flujo_neto < 0`` (stress financiero).
        ``False`` si ``flujo_neto >= 0`` (equilibrado o superávit).
    """
    return flujo_neto < 0


# ---------------------------------------------------------------------------
# Capa 3 — Crecimiento
# ---------------------------------------------------------------------------


def posicion_vida_v3(
    activo_liquido: float,
    valor_liquidable_portafolio: float,
    esenciales: float,
    cuotas_pasivos: list[float],
) -> float:
    """Calcula la Posición de Vida versión 3 (Capa 3).

    Extiende v2 sumando el valor liquidable del portafolio financiero al
    numerador, para una visión completa de la capacidad de cobertura.

    Args:
        activo_liquido: Valor total de activos líquidos.
        valor_liquidable_portafolio: Valor de mercado liquidable de activos
            financieros (ETFs, fondos mutuos, APV, etc.).
        esenciales: Gasto mensual en ítems esenciales.
        cuotas_pasivos: Lista de cuotas mensuales de todos los pasivos.

    Returns:
        ``(activo_liquido + valor_liquidable_portafolio)
        / (esenciales + sum(cuotas_pasivos))``.

    Raises:
        ValueError: Si el denominador es cero o negativo.
    """
    denominador = esenciales + sum(cuotas_pasivos)
    if denominador <= 0:
        raise ValueError(
            f"La suma de esenciales y cuotas debe ser mayor que cero, "
            f"se recibió {denominador}."
        )
    return (activo_liquido + valor_liquidable_portafolio) / denominador


# ---------------------------------------------------------------------------
# Normalización de moneda — conversión a CLP
# ---------------------------------------------------------------------------

#: Valor UF en CLP usado por defecto cuando no hay configuración del usuario.
VALOR_UF_DEFAULT: float = 39_700.0

#: Tipo de cambio USD/CLP usado por defecto cuando no hay configuración del usuario.
VALOR_USD_DEFAULT: float = 950.0


def normalizar_a_clp(
    flujo: float,
    moneda: str,
    valor_uf: float,
    valor_usd: float,
) -> float:
    """Convierte un valor monetario a CLP según la moneda indicada.

    Función de normalización para comparar métricas expresadas en distintas
    monedas (UF, USD) con el ingreso o los gastos en CLP.

    Los tipos de cambio son manuales (actualizados por el usuario). En Capa 4
    se reemplazarán por la API del Banco Central / SII.

    Args:
        flujo: Valor en la moneda original (puede ser positivo o negativo).
        moneda: Código de moneda: ``"CLP"``, ``"UF"`` o ``"USD"``.
            Cualquier otro valor se trata como CLP (retorna el flujo sin cambio).
        valor_uf: Tipo de cambio UF → CLP (CLP por cada UF). Debe ser > 0.
        valor_usd: Tipo de cambio USD → CLP (CLP por cada dólar). Debe ser > 0.

    Returns:
        Equivalente en CLP:
        - ``"UF"``  → ``flujo * valor_uf``
        - ``"USD"`` → ``flujo * valor_usd``
        - ``"CLP"`` (o desconocida) → ``flujo`` sin cambio.

    Raises:
        ValueError: Si ``valor_uf`` o ``valor_usd`` son menores o iguales a cero.

    Examples:
        >>> normalizar_a_clp(100.0, "UF", 39_700.0, 950.0)
        3970000.0
        >>> normalizar_a_clp(1_000.0, "USD", 39_700.0, 950.0)
        950000.0
        >>> normalizar_a_clp(500_000.0, "CLP", 39_700.0, 950.0)
        500000.0
    """
    if valor_uf <= 0:
        raise ValueError(
            f"valor_uf debe ser mayor que cero, se recibió {valor_uf}."
        )
    if valor_usd <= 0:
        raise ValueError(
            f"valor_usd debe ser mayor que cero, se recibió {valor_usd}."
        )
    if moneda == "UF":
        return flujo * valor_uf
    if moneda == "USD":
        return flujo * valor_usd
    return float(flujo)  # CLP u otro → sin cambio


# ---------------------------------------------------------------------------
# Helper general — desbloqueo de capas
# ---------------------------------------------------------------------------


def capa_desbloqueada(session_state: dict) -> int:
    """Retorna la capa máxima desbloqueada según el estado de sesión.

    Las capas se desbloquean secuencialmente; una capa superior solo es
    accesible si todas las anteriores están desbloqueadas.

    Condiciones de desbloqueo (del CLAUDE.md):
        - Capa 1: siempre activa.
        - Capa 2: ``meta_fondo_definida == True`` AND ``buckets_confirmados == True``.
        - Capa 3: ``len(pasivos_con_tabla) >= 1`` AND ``afp_saldo is not None``.
        - Capa 4: ``len(activos_con_tabla) >= 1`` AND ``len(objetivos_activos) >= 1``.

    Args:
        session_state: Diccionario de estado de sesión (equivalente a
            ``st.session_state``). Se usan las claves:
            ``meta_fondo_definida``, ``buckets_confirmados``,
            ``pasivos_con_tabla``, ``afp_saldo``,
            ``activos_con_tabla``, ``objetivos_activos``.
            Las claves ausentes se tratan como falsy / lista vacía.

    Returns:
        Entero entre 1 y 4 con la capa máxima desbloqueada.
    """
    # Capa 2
    if not (
        session_state.get("meta_fondo_definida")
        and session_state.get("buckets_confirmados")
    ):
        return 1

    # Capa 3
    if not (
        len(session_state.get("pasivos_con_tabla", [])) >= 1
        and session_state.get("afp_saldo") is not None
    ):
        return 2

    # Capa 4
    if not (
        len(session_state.get("activos_con_tabla", [])) >= 1
        and len(session_state.get("objetivos_activos", [])) >= 1
    ):
        return 3

    return 4


# ---------------------------------------------------------------------------
# Capa 2-C — Desagregación de buckets
# ---------------------------------------------------------------------------


def bucket_sugerido(tipo_pasivo: str) -> str:
    """Retorna el ID del bucket sugerido para un tipo de pasivo.

    Usada al confirmar un pasivo para proponer automáticamente en qué bucket
    clasificar la cuota mensual.

    Args:
        tipo_pasivo: Tipo del pasivo: ``"Hipotecario"``, ``"Colegio"``,
            ``"Crédito consumo"``, ``"Tarjeta"``, ``"APV"`` u otro.

    Returns:
        ID del bucket: ``"GAS_ESE_BUCKET"``, ``"GAS_IMP_BUCKET"`` o
        ``"GAS_ASP_BUCKET"``. Los tipos no mapeados retornan
        ``"GAS_IMP_BUCKET"`` (Importantes) como valor por defecto.

    Examples:
        >>> bucket_sugerido("Hipotecario")
        'GAS_ESE_BUCKET'
        >>> bucket_sugerido("Tarjeta")
        'GAS_IMP_BUCKET'
        >>> bucket_sugerido("APV")
        'GAS_ASP_BUCKET'
        >>> bucket_sugerido("Otro")
        'GAS_IMP_BUCKET'
    """
    return _BUCKET_SUGERIDO_MAP.get(tipo_pasivo, "GAS_IMP_BUCKET")


def espacio_disponible_bucket(session_state: dict, bucket_id: str) -> float:
    """Retorna el espacio disponible en un bucket tras descontar cuotas vinculadas.

    Calcula cuánto del presupuesto del bucket queda libre después de restar
    las cuotas de pasivos ya vinculados a ese bucket
    (posiciones con ``bucket_vinculado == bucket_id``).

    Args:
        session_state: Diccionario de estado de sesión (contiene ``"positions"``).
        bucket_id: ID del bucket a consultar
            (p. ej. ``"GAS_ESE_BUCKET"``).

    Returns:
        ``monto_bucket - sum(cuotas_vinculadas)``.
        Positivo → queda espacio disponible.
        Negativo → el bucket está excedido.

    Examples:
        >>> ss = {"positions": {
        ...     "GAS_ESE_BUCKET": {"Monto_Mensual": 2_000_000},
        ...     "PAS_HIP_001": {"bucket_vinculado": "GAS_ESE_BUCKET",
        ...                     "Cuota_Vinculada_CLP": 1_500_000},
        ... }}
        >>> espacio_disponible_bucket(ss, "GAS_ESE_BUCKET")
        500000.0
    """
    positions: dict = session_state.get("positions", {})
    monto_bucket = float(positions.get(bucket_id, {}).get("Monto_Mensual", 0.0))
    cuotas_vinculadas = sum(
        float(p.get("Cuota_Vinculada_CLP", 0.0))
        for p in positions.values()
        if p.get("bucket_vinculado") == bucket_id
    )
    return monto_bucket - cuotas_vinculadas


# ---------------------------------------------------------------------------
# Capa 3 — Pensión mensual sostenible
# ---------------------------------------------------------------------------


def calcular_pension_mensual(
    total_acumulado: float,
    anos_retiro: int,
    tasa_retiro_anual: float = 0.04,
) -> float:
    """Calcula la pensión mensual sostenible usando la fórmula de anualidad.

    Retiro mensual que agota exactamente ``total_acumulado`` en ``anos_retiro``
    años, asumiendo que el fondo sigue rentando ``tasa_retiro_anual`` anual.

    Fórmula de pago de anualidad (PMT):
        tasa_mensual = (1 + tasa_retiro_anual) ** (1/12) - 1
        n = anos_retiro * 12
        PMT = total * tasa_mensual / (1 - (1 + tasa_mensual) ** (-n))

    Si la tasa es cero, se divide linealmente: total / n.

    Args:
        total_acumulado: Capital total acumulado al momento del retiro (CLP).
        anos_retiro: Años de retiro esperados. Debe ser > 0.
        tasa_retiro_anual: Tasa anual de rendimiento durante el retiro
            (default 0.04 = 4%, regla del 4 %).

    Returns:
        Pensión mensual sostenible en las mismas unidades que
        ``total_acumulado``.

    Raises:
        ValueError: Si ``anos_retiro`` <= 0.

    Examples:
        >>> calcular_pension_mensual(0, 20) == 0.0
        True
        >>> calcular_pension_mensual(1_000_000, 20, 0.04) > 6_000
        True
    """
    if anos_retiro <= 0:
        raise ValueError(
            f"anos_retiro debe ser mayor que 0, se recibió {anos_retiro}."
        )
    if total_acumulado <= 0:
        return 0.0
    n = anos_retiro * 12
    tasa_mensual = (1 + tasa_retiro_anual) ** (1 / 12) - 1
    if tasa_mensual > 0:
        pension = total_acumulado * tasa_mensual / (
            1 - (1 + tasa_mensual) ** (-n)
        )
    else:
        pension = total_acumulado / n
    return pension
