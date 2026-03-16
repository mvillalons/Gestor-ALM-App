"""core/planner.py — Agente Planificador (motor de reglas, v1).

Genera el plan financiero de 4 pasos basado en los datos del usuario
almacenados en session_state. Sin preguntas: propone un plan completo
determinístico que el usuario puede editar parámetro a parámetro.

Arquitectura:
    - Sin dependencias de Streamlit.
    - Recibe un dict ordinario (session_state o dict de test).
    - Retorna list[dict] con 4 PasoPlan.

PasoPlan es un dict con las claves:
    numero        int         — 1, 2, 3 o 4
    titulo        str         — nombre del paso
    estado        str         — "completo" | "en_curso" | "pendiente"
    diagnostico   str         — 2 líneas explicando la situación
    accion        str         — qué hacer concretamente
    monto_mensual float       — cuánto destinar por mes (CLP)
    plazo_meses   int         — cuándo se completa (0 = indefinido)
    params        dict        — parámetros editables por el usuario

Parámetros editables (plan_params en session_state):
    tasa_reemplazo      float   — fracción del ingreso como pensión objetivo (default 0.70)
    anos_retiro         int     — años de retiro esperados (default 20)
    meses_reserva_meta  int     — meses de esenciales para el fondo (default 6)
    edad_jubilacion     float   — override de la edad de jubilación del AFP (opcional)
    distribucion_paso4  dict    — {inversion, estilo_vida, libre} (default 50/30/20)
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from core.calculator import normalizar_a_clp

# ---------------------------------------------------------------------------
# Estados posibles de un paso
# ---------------------------------------------------------------------------

ESTADO_COMPLETO: str = "completo"
ESTADO_EN_CURSO: str = "en_curso"
ESTADO_PENDIENTE: str = "pendiente"

# Tipos de pasivo que se consideran deuda financiera en Paso 1
TIPOS_DEUDA_FINANCIERA: set[str] = {"Hipotecario", "Crédito consumo", "Tarjeta"}

# ---------------------------------------------------------------------------
# Defaults del plan_params
# ---------------------------------------------------------------------------

_PLAN_PARAMS_DEFAULTS: dict[str, Any] = {
    "tasa_reemplazo": 0.70,
    "anos_retiro": 20,
    "meses_reserva_meta": 6,
    "distribucion_paso4": {
        "inversion": 0.50,
        "estilo_vida": 0.30,
        "libre": 0.20,
    },
}


def make_plan_params_defaults() -> dict[str, Any]:
    """Retorna los defaults de plan_params con objetos mutables frescos.

    Returns:
        Dict con las claves: ``tasa_reemplazo``, ``anos_retiro``,
        ``meses_reserva_meta``, ``distribucion_paso4``.
    """
    d = dict(_PLAN_PARAMS_DEFAULTS)
    d["distribucion_paso4"] = dict(_PLAN_PARAMS_DEFAULTS["distribucion_paso4"])
    return d


# ---------------------------------------------------------------------------
# Helpers internos de extracción de datos
# ---------------------------------------------------------------------------


def _saldo_restante_clp(
    pid: str,
    positions: dict,
    schedules: dict,
    valor_uf: float,
    valor_usd: float,
) -> float:
    """Saldo restante de una posición (deuda o activo) en CLP.

    Prioriza la tabla de desarrollo (período actual o siguiente).
    Si no hay tabla, cae a los campos de parámetros.

    Args:
        pid: ID de la posición.
        positions: Dict de posiciones del session_state.
        schedules: Dict de tablas de desarrollo (id → DataFrame).
        valor_uf: CLP por UF.
        valor_usd: CLP por USD.

    Returns:
        Saldo restante normalizado a CLP. Retorna 0.0 si no hay datos.
    """
    p = positions.get(pid, {})
    moneda = p.get("Moneda", "CLP")
    tabla = schedules.get(pid)
    hoy = date.today().strftime("%Y-%m")

    if tabla is not None and not tabla.empty:
        futuro = tabla[tabla["Periodo"] >= hoy]
        if not futuro.empty:
            saldo_orig = float(futuro["Saldo_Inicial"].iloc[0])
        else:
            saldo_orig = float(tabla["Saldo_Final"].iloc[-1])
    else:
        saldo_orig = float(
            p.get(
                "Capital",
                p.get("Monto", p.get("Deuda_Total", p.get("Saldo_Actual", 0))),
            )
        )

    return normalizar_a_clp(saldo_orig, moneda, valor_uf, valor_usd)


def _plazo_restante_meses(pid: str, schedules: dict) -> int:
    """Número de filas futuras en el schedule (≈ meses de deuda restantes).

    Args:
        pid: ID de la posición.
        schedules: Dict de tablas de desarrollo.

    Returns:
        Número de filas con ``Periodo >= hoy``. 0 si no hay tabla.
    """
    tabla = schedules.get(pid)
    if tabla is None or tabla.empty:
        return 0
    hoy = date.today().strftime("%Y-%m")
    futuro = tabla[tabla["Periodo"] >= hoy]
    return len(futuro)


def _tasa_anual_decimal(pid: str, positions: dict) -> float:
    """Tasa anual de una posición como decimal (0.12 = 12%).

    Intenta los campos ``Tasa_Anual``, ``Tasa_Anual_Pct`` y
    ``Tasa_Mensual`` en ese orden.

    Args:
        pid: ID de la posición.
        positions: Dict de posiciones.

    Returns:
        Tasa anual como decimal. 0.0 si no hay datos.
    """
    p = positions.get(pid, {})
    if "Tasa_Anual" in p:
        return float(p["Tasa_Anual"])
    if "Tasa_Anual_Pct" in p:
        return float(p["Tasa_Anual_Pct"]) / 100.0
    if "Tasa_Mensual" in p:
        return float(p["Tasa_Mensual"]) * 12.0
    return 0.0


def _saldo_proyectado_clp(
    pid: str,
    positions: dict,
    schedules: dict,
    valor_uf: float,
    valor_usd: float,
) -> float:
    """Saldo final proyectado de un activo (última fila del schedule) en CLP.

    Args:
        pid: ID de la posición.
        positions: Dict de posiciones.
        schedules: Dict de tablas de desarrollo.
        valor_uf: CLP por UF.
        valor_usd: CLP por USD.

    Returns:
        Saldo final proyectado en CLP. Cae a saldo actual si no hay tabla.
    """
    p = positions.get(pid, {})
    moneda = p.get("Moneda", "CLP")
    tabla = schedules.get(pid)

    if tabla is not None and not tabla.empty:
        saldo_orig = float(tabla["Saldo_Final"].iloc[-1])
    else:
        saldo_orig = float(
            p.get("Saldo_Actual", p.get("Capital", p.get("Monto", 0)))
        )

    return normalizar_a_clp(saldo_orig, moneda, valor_uf, valor_usd)


# ---------------------------------------------------------------------------
# Extracción de contexto
# ---------------------------------------------------------------------------


def _extraer_contexto(session_state: dict) -> dict:
    """Extrae y normaliza todos los datos necesarios del session_state.

    Args:
        session_state: Dict de estado de sesión.

    Returns:
        Dict con variables normalizadas listas para usar en los pasos.
    """
    positions: dict = session_state.get("positions", {})
    schedules: dict = session_state.get("schedules", {})
    plan_params: dict = session_state.get("plan_params", make_plan_params_defaults())
    valor_uf: float = float(session_state.get("valor_uf", 39_700.0))
    valor_usd: float = float(session_state.get("valor_usd", 950.0))

    # ── Ingresos y gastos base (CLP) ─────────────────────────────────────────
    def _p(pid: str) -> dict:
        return positions.get(pid, {})

    def _clp(flujo: float, moneda: str) -> float:
        return normalizar_a_clp(flujo, moneda, valor_uf, valor_usd)

    ing_p = _p("ING_PRINCIPAL")
    ing_clp = _clp(float(ing_p.get("Monto_Mensual", 0)), ing_p.get("Moneda", "CLP"))

    ese_p = _p("GAS_ESE_BUCKET")
    ese_clp = _clp(float(ese_p.get("Monto_Mensual", 0)), ese_p.get("Moneda", "CLP"))

    imp_p = _p("GAS_IMP_BUCKET")
    imp_clp = _clp(float(imp_p.get("Monto_Mensual", 0)), imp_p.get("Moneda", "CLP"))

    asp_p = _p("GAS_ASP_BUCKET")
    asp_clp = _clp(float(asp_p.get("Monto_Mensual", 0)), asp_p.get("Moneda", "CLP"))

    margen_clp = ing_clp - (ese_clp + imp_clp + asp_clp)

    # ── Activo líquido ────────────────────────────────────────────────────────
    liq_p = _p("ACT_LIQUIDO_PRINCIPAL")
    liq_clp = _clp(float(liq_p.get("Saldo_Actual", 0)), liq_p.get("Moneda", "CLP"))

    # ── Deudas a liquidar (Pasivo_Corto_Plazo) ────────────────────────────────
    deuda_ids = [
        pid for pid, p in positions.items()
        if p.get("Clase") == "Pasivo_Corto_Plazo"
    ]
    deudas: list[dict] = []
    for pid in deuda_ids:
        p = positions[pid]
        tipo = p.get("Tipo_Pasivo", p.get("Tipo", ""))
        if tipo not in TIPOS_DEUDA_FINANCIERA:
            continue  # colegio, jardín, arriendo → no es deuda financiera
        saldo = _saldo_restante_clp(pid, positions, schedules, valor_uf, valor_usd)
        if saldo <= 0:
            continue
        deudas.append({
            "id": pid,
            "descripcion": p.get("Descripcion", pid),
            "tipo": p.get("Tipo", ""),
            "saldo_clp": saldo,
            "tasa_anual": _tasa_anual_decimal(pid, positions),
            "plazo_restante": _plazo_restante_meses(pid, schedules),
        })
    # Ordenar por tasa descendente — método avalanche
    deudas.sort(key=lambda d: d["tasa_anual"], reverse=True)

    # ── Proyección pensional (AFP + APV) ─────────────────────────────────────
    afp_ids = [
        pid for pid, p in positions.items()
        if p.get("Clase") == "Prevision_AFP"
    ]
    proyeccion_afp_clp = sum(
        _saldo_proyectado_clp(pid, positions, schedules, valor_uf, valor_usd)
        for pid in afp_ids
    )

    apv_ids = [
        pid for pid, p in positions.items()
        if p.get("Clase") == "Activo_Financiero" and p.get("Tipo") == "APV"
    ]
    proyeccion_apv_clp = sum(
        _saldo_proyectado_clp(pid, positions, schedules, valor_uf, valor_usd)
        for pid in apv_ids
    )

    # Edad de jubilación desde posición AFP (primera encontrada)
    edad_actual: float | None = None
    edad_jubilacion: float = 65.0
    for pid in afp_ids:
        p = positions[pid]
        if "Edad_Actual" in p:
            edad_actual = float(p["Edad_Actual"])
        if "Edad_Jubilacion" in p:
            edad_jubilacion = float(p["Edad_Jubilacion"])
        break

    return {
        "positions": positions,
        "schedules": schedules,
        "plan_params": plan_params,
        "valor_uf": valor_uf,
        "valor_usd": valor_usd,
        "ing_clp": ing_clp,
        "ese_clp": ese_clp,
        "imp_clp": imp_clp,
        "asp_clp": asp_clp,
        "margen_clp": margen_clp,
        "liq_clp": liq_clp,
        "deudas": deudas,
        "proyeccion_pension_clp": proyeccion_afp_clp + proyeccion_apv_clp,
        "edad_actual": edad_actual,
        "edad_jubilacion": edad_jubilacion,
        "afp_ids": afp_ids,
        "apv_ids": apv_ids,
    }


# ---------------------------------------------------------------------------
# Paso 1 — Liquidar deudas no hipotecarias
# ---------------------------------------------------------------------------


def _generar_paso1(ctx: dict) -> dict:
    """Paso 1 — Liquidar deudas de consumo (método avalanche).

    Args:
        ctx: Contexto extraído por :func:`_extraer_contexto`.

    Returns:
        PasoPlan dict con estado, diagnóstico y acción.
    """
    deudas: list[dict] = ctx["deudas"]
    margen: float = ctx["margen_clp"]
    ese_clp: float = ctx["ese_clp"]
    liq_clp: float = ctx["liq_clp"]

    total_deuda = sum(d["saldo_clp"] for d in deudas)

    if total_deuda <= 0 or not deudas:
        return {
            "numero": 1,
            "titulo": "Liquidar deudas de consumo",
            "estado": ESTADO_COMPLETO,
            "diagnostico": (
                "Sin deudas de consumo — excelente punto de partida.\n"
                "Tu margen libre puede destinarse directamente a tus metas."
            ),
            "accion": "",
            "monto_mensual": 0.0,
            "plazo_meses": 0,
            "params": {"deudas_ordenadas": []},
        }

    # Mínimo paralelo: si fondo < 3 meses, reserva 20% del margen para Paso 2
    fondo_meses = liq_clp / max(ese_clp, 1.0)
    margen_para_deudas = margen * 0.80 if fondo_meses < 3.0 else margen

    if margen_para_deudas <= 0:
        return {
            "numero": 1,
            "titulo": "Liquidar deudas de consumo",
            "estado": ESTADO_EN_CURSO,
            "diagnostico": (
                f"Tienes {len(deudas)} deuda{'s' if len(deudas) > 1 else ''} "
                f"por ${int(total_deuda):,} en total.\n"
                f"⚠️ Margen libre insuficiente — revisa tus gastos."
            ),
            "accion": (
                f"Libera capacidad de ahorro recortando gastos. "
                f"Tienes ${int(total_deuda):,} en deudas pendientes."
            ),
            "monto_mensual": 0.0,
            "plazo_meses": 999,
            "params": {"deudas_ordenadas": deudas},
        }

    # Plazo estimado (simplificado, sin intereses acumulados — estimación conservadora)
    plazo = math.ceil(total_deuda / margen_para_deudas)
    primera = deudas[0]

    return {
        "numero": 1,
        "titulo": "Liquidar deudas de consumo",
        "estado": ESTADO_EN_CURSO,
        "diagnostico": (
            f"Tienes {len(deudas)} deuda{'s' if len(deudas) > 1 else ''} "
            f"por ${int(total_deuda):,} en total.\n"
            f"Con ${int(margen_para_deudas):,}/mes las liquidas en ~{plazo} meses."
        ),
        "accion": (
            f"Destina ${int(margen_para_deudas):,}/mes comenzando por "
            f"{primera['descripcion']} "
            f"({primera['tasa_anual'] * 100:.1f}% anual)."
        ),
        "monto_mensual": margen_para_deudas,
        "plazo_meses": plazo,
        "params": {"deudas_ordenadas": deudas},
    }


# ---------------------------------------------------------------------------
# Paso 2 — Fondo de reserva
# ---------------------------------------------------------------------------


def _generar_paso2(ctx: dict, paso1: dict) -> dict:
    """Paso 2 — Fondo de reserva (mínimo N meses de esenciales).

    Corre en paralelo con Paso 1 cuando el fondo tiene menos de 3 meses
    (destina 20% del margen libre mínimo al fondo aunque haya deudas).

    Args:
        ctx: Contexto extraído por :func:`_extraer_contexto`.
        paso1: Resultado de :func:`_generar_paso1`.

    Returns:
        PasoPlan dict.
    """
    ese_clp: float = ctx["ese_clp"]
    liq_clp: float = ctx["liq_clp"]
    margen: float = ctx["margen_clp"]
    plan_params: dict = ctx["plan_params"]

    meses_reserva_meta = int(plan_params.get("meses_reserva_meta", 6))
    meta_fondo = ese_clp * meses_reserva_meta
    fondo_actual_meses = liq_clp / max(ese_clp, 1.0)
    gap = max(0.0, meta_fondo - liq_clp)

    if gap <= 0:
        return {
            "numero": 2,
            "titulo": f"Fondo de reserva ({meses_reserva_meta} meses)",
            "estado": ESTADO_COMPLETO,
            "diagnostico": (
                f"Tienes {fondo_actual_meses:.1f} meses de reserva. "
                f"¡Meta de {meses_reserva_meta} meses cubierta!\n"
                f"Fondo: ${int(liq_clp):,} / Meta: ${int(meta_fondo):,}."
            ),
            "accion": "",
            "monto_mensual": 0.0,
            "plazo_meses": 0,
            "params": {"meses_reserva_meta": meses_reserva_meta},
        }

    paso1_completo = paso1["estado"] == ESTADO_COMPLETO
    if paso1_completo:
        margen_para_fondo = max(0.0, margen)
        paralelo_note = ""
    else:
        margen_para_fondo = max(0.0, margen * 0.20)
        paralelo_note = "\n*Corre en paralelo con Paso 1 (20% del margen libre mínimo).*"

    if margen_para_fondo <= 0:
        return {
            "numero": 2,
            "titulo": f"Fondo de reserva ({meses_reserva_meta} meses)",
            "estado": ESTADO_EN_CURSO,
            "diagnostico": (
                f"Tienes {fondo_actual_meses:.1f} meses de reserva. "
                f"Meta: {meses_reserva_meta} meses = ${int(meta_fondo):,}.\n"
                f"Margen libre insuficiente para aportar al fondo.{paralelo_note}"
            ),
            "accion": "Libera capacidad de ahorro recortando gastos para construir tu fondo.",
            "monto_mensual": 0.0,
            "plazo_meses": 999,
            "params": {"meses_reserva_meta": meses_reserva_meta},
        }

    plazo = math.ceil(gap / margen_para_fondo)

    return {
        "numero": 2,
        "titulo": f"Fondo de reserva ({meses_reserva_meta} meses)",
        "estado": ESTADO_EN_CURSO,
        "diagnostico": (
            f"Tienes {fondo_actual_meses:.1f} meses de reserva. "
            f"Meta: {meses_reserva_meta} meses = ${int(meta_fondo):,}.\n"
            f"Faltan ${int(gap):,} para completar el fondo.{paralelo_note}"
        ),
        "accion": (
            f"Aparta ${int(margen_para_fondo):,}/mes en tu cuenta de ahorro → "
            f"completo en ~{plazo} meses."
        ),
        "monto_mensual": margen_para_fondo,
        "plazo_meses": plazo,
        "params": {"meses_reserva_meta": meses_reserva_meta},
    }


# ---------------------------------------------------------------------------
# Paso 3 — Pensión asegurada
# ---------------------------------------------------------------------------


def _generar_paso3(ctx: dict, margen_disponible_p3: float = 0.0) -> dict:
    """Paso 3 — Pensión asegurada (siempre activo desde el inicio).

    Calcula la brecha entre la meta de acumulación y la proyección actual
    (AFP + APV). Sugiere el aporte adicional mensual para cerrarla, capped
    por el margen disponible tras Pasos 1 y 2.

    Args:
        ctx: Contexto extraído por :func:`_extraer_contexto`.
        margen_disponible_p3: Margen libre restante después de P1 y P2 (CLP).
            Si es 0, el aporte efectivo es 0 pero se muestra el ideal.

    Returns:
        PasoPlan dict.
    """
    ing_clp: float = ctx["ing_clp"]
    proyeccion_pension_clp: float = ctx["proyeccion_pension_clp"]
    edad_actual: float | None = ctx["edad_actual"]
    edad_jubilacion_afp: float = ctx["edad_jubilacion"]
    plan_params: dict = ctx["plan_params"]

    tasa_reemplazo = float(plan_params.get("tasa_reemplazo", 0.70))
    anos_retiro = int(plan_params.get("anos_retiro", 20))
    # Override de edad de jubilación desde plan_params (si el usuario la editó)
    edad_jubilacion = float(plan_params.get("edad_jubilacion", edad_jubilacion_afp))

    meta_acumulacion = ing_clp * tasa_reemplazo * 12 * anos_retiro
    brecha = max(0.0, meta_acumulacion - proyeccion_pension_clp)

    # Meses restantes hasta jubilación
    meses_hasta_jub = 0
    if edad_actual is not None and edad_jubilacion > edad_actual:
        meses_hasta_jub = round((edad_jubilacion - edad_actual) * 12)

    params_paso3 = {
        "tasa_reemplazo": tasa_reemplazo,
        "anos_retiro": anos_retiro,
        "edad_jubilacion": edad_jubilacion,
    }

    if brecha <= 0:
        return {
            "numero": 3,
            "titulo": "Pensión asegurada",
            "estado": ESTADO_COMPLETO,
            "diagnostico": (
                f"Proyectas ${int(proyeccion_pension_clp):,} "
                f"a los {int(edad_jubilacion)} años.\n"
                f"Meta: ${int(meta_acumulacion):,} "
                f"({int(tasa_reemplazo * 100)}% de tu ingreso por {anos_retiro} años). ✅"
            ),
            "accion": "Mantén los aportes actuales — meta pensional cubierta.",
            "monto_mensual": 0.0,
            "plazo_meses": meses_hasta_jub,
            "params": params_paso3,
        }

    # Aporte ideal mensual lineal para cerrar la brecha
    # Fallback: 120 meses (10 años) si no hay edad AFP registrada
    divisor = meses_hasta_jub if meses_hasta_jub > 0 else 120
    aporte_ideal = brecha / divisor

    if proyeccion_pension_clp > 0:
        diagnostico = (
            f"Proyectas ${int(proyeccion_pension_clp):,} "
            f"a los {int(edad_jubilacion)} años.\n"
            f"Meta: ${int(meta_acumulacion):,} "
            f"({int(tasa_reemplazo * 100)}% de tu ingreso por {anos_retiro} años)."
        )
    else:
        diagnostico = (
            f"Meta pensional: ${int(meta_acumulacion):,} "
            f"({int(tasa_reemplazo * 100)}% de tu ingreso por {anos_retiro} años).\n"
            "Registra tu AFP en Capa 2 para ver tu proyección actual."
        )

    # Coordinación de margen: capped por lo disponible tras P1 y P2
    if margen_disponible_p3 <= 0:
        accion = (
            f"Cuando liberes margen, necesitarás ${int(aporte_ideal):,}/mes "
            f"para alcanzar tu meta pensional."
        )
        aporte_efectivo = 0.0
    elif aporte_ideal <= margen_disponible_p3:
        accion = (
            f"Brecha de ${int(brecha):,} — aumenta tu APV en "
            f"${int(aporte_ideal):,}/mes."
        )
        aporte_efectivo = aporte_ideal
    else:
        # Margen insuficiente: mostrar cuánto se puede aportar y en cuánto cierra
        anos_brecha = math.ceil(brecha / (margen_disponible_p3 * 12))
        accion = (
            f"Con tu margen disponible de ${int(margen_disponible_p3):,}/mes "
            f"puedes aportar a tu APV — cerrarás la brecha en ~{anos_brecha} años."
        )
        aporte_efectivo = margen_disponible_p3

    return {
        "numero": 3,
        "titulo": "Pensión asegurada",
        "estado": ESTADO_EN_CURSO if proyeccion_pension_clp > 0 else ESTADO_PENDIENTE,
        "diagnostico": diagnostico,
        "accion": accion,
        "monto_mensual": aporte_efectivo,
        "plazo_meses": meses_hasta_jub,
        "params": params_paso3,
    }


# ---------------------------------------------------------------------------
# Paso 4 — Acumulación y estilo de vida
# ---------------------------------------------------------------------------


def _generar_paso4(
    ctx: dict,
    paso1: dict,
    paso2: dict,
    margen_disponible_p4: float = 0.0,
) -> dict:
    """Paso 4 — Acumulación y estilo de vida.

    Se activa únicamente cuando Paso 1 y Paso 2 están completos.
    Distribuye el margen libre restante (tras P1, P2 y P3) según la
    distribución configurada.

    Args:
        ctx: Contexto extraído por :func:`_extraer_contexto`.
        paso1: Resultado de :func:`_generar_paso1`.
        paso2: Resultado de :func:`_generar_paso2`.
        margen_disponible_p4: Margen libre restante después de P1, P2 y P3 (CLP).

    Returns:
        PasoPlan dict.
    """
    plan_params: dict = ctx["plan_params"]

    distribucion: dict = plan_params.get(
        "distribucion_paso4",
        {"inversion": 0.50, "estilo_vida": 0.30, "libre": 0.20},
    )
    # Normalizar para que siempre sumen 1.0
    _total = (
        distribucion.get("inversion", 0.50)
        + distribucion.get("estilo_vida", 0.30)
        + distribucion.get("libre", 0.20)
    )
    if _total > 0:
        distribucion = {k: v / _total for k, v in distribucion.items()}

    paso1_completo = paso1["estado"] == ESTADO_COMPLETO
    paso2_completo = paso2["estado"] == ESTADO_COMPLETO

    if not (paso1_completo and paso2_completo):
        faltantes = []
        if not paso1_completo:
            faltantes.append("Paso 1 (deudas)")
        if not paso2_completo:
            faltantes.append("Paso 2 (fondo de reserva)")
        plazo_activacion = max(
            paso1.get("plazo_meses", 0),
            paso2.get("plazo_meses", 0),
        )
        if plazo_activacion >= 999:
            plazo_activacion = 0
        return {
            "numero": 4,
            "titulo": "Acumulación y estilo de vida",
            "estado": ESTADO_PENDIENTE,
            "diagnostico": (
                f"Se activa cuando completes: {' y '.join(faltantes)}.\n"
                + (
                    f"Estimado: en ~{plazo_activacion} meses liberarás tu margen para invertir."
                    if plazo_activacion > 0
                    else "Completa los pasos previos para activar la acumulación."
                )
            ),
            "accion": "",
            "monto_mensual": 0.0,
            "plazo_meses": plazo_activacion,
            "params": {"distribucion_paso4": distribucion},
        }

    margen_disponible = max(0.0, margen_disponible_p4)
    inv = margen_disponible * distribucion.get("inversion", 0.50)
    est = margen_disponible * distribucion.get("estilo_vida", 0.30)
    lib = margen_disponible * distribucion.get("libre", 0.20)

    accion = (
        f"Destina ${inv:,.0f}/mes a inversión de largo plazo "
        f"(ETFs o fondos), ${est:,.0f}/mes a fondo de estilo "
        f"de vida, y ${lib:,.0f}/mes como colchón libre."
    )

    return {
        "numero": 4,
        "titulo": "Acumulación y estilo de vida",
        "estado": ESTADO_EN_CURSO,
        "diagnostico": (
            f"Con deudas liquidadas y reserva completa, tienes "
            f"${int(margen_disponible):,}/mes para hacer crecer tu patrimonio.\n"
            f"Distribución: ${int(inv):,} inversión | "
            f"${int(est):,} estilo de vida | ${int(lib):,} libre."
        ),
        "accion": accion,
        "monto_mensual": margen_disponible,
        "plazo_meses": 0,
        "params": {"distribucion_paso4": distribucion},
    }


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------


def generar_plan(session_state: dict) -> list[dict]:
    """Genera el plan financiero de 4 pasos basado en el session_state actual.

    Lee el estado de sesión completo y aplica los lineamientos del CLAUDE.md
    (avalanche, reserva 6 meses, pensión 70%, acumulación 50/30/20) para
    producir una lista ordenada de 4 PasoPlan.

    Los parámetros editables provienen de ``session_state["plan_params"]``.
    Si la clave no existe, se usan los defaults de :func:`make_plan_params_defaults`.

    Args:
        session_state: Diccionario de estado de sesión (``st.session_state``
            o dict ordinario en tests).

    Returns:
        Lista de 4 dicts (PasoPlan), uno por paso del plan en orden.

    Example:
        >>> ss = {}
        >>> from core import state
        >>> state.init_session_state(_ss=ss)
        >>> plan = generar_plan(ss)
        >>> len(plan)
        4
        >>> plan[0]["numero"]
        1
        >>> plan[0]["estado"]  # sin deudas registradas
        'completo'
    """
    ctx = _extraer_contexto(session_state)
    paso1 = _generar_paso1(ctx)
    paso2 = _generar_paso2(ctx, paso1)
    # Cascada de margen: cada paso recibe solo lo que queda tras los anteriores.
    # Si el paso está completo, su consumo de margen es 0.
    margen_p1 = paso1["monto_mensual"] if paso1["estado"] != ESTADO_COMPLETO else 0.0
    margen_p2 = paso2["monto_mensual"] if paso2["estado"] != ESTADO_COMPLETO else 0.0
    margen_restante_p3 = max(0.0, ctx["margen_clp"] - margen_p1 - margen_p2)
    paso3 = _generar_paso3(ctx, margen_restante_p3)
    margen_p3 = paso3["monto_mensual"] if paso3["estado"] != ESTADO_COMPLETO else 0.0
    margen_restante_p4 = max(0.0, ctx["margen_clp"] - margen_p1 - margen_p2 - margen_p3)
    paso4 = _generar_paso4(ctx, paso1, paso2, margen_restante_p4)
    return [paso1, paso2, paso3, paso4]
