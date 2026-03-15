"""pages/04_capa3_crecimiento.py — Dashboard de Capa 3: Crecimiento (v2).

Layout de tres zonas verticales en ancho completo:

  ZONA 1 — Balance Patrimonial
      Tres cards en fila: Activos (verde) / Pasivos (rojo) / Net Worth (azul).
      Todos los montos normalizados a CLP sin truncar.

  ZONA 2 — Plan Financiero (Agente Planificador)
      4 cards de pasos según los lineamientos del CLAUDE.md.
      Cada paso muestra estado, diagnóstico, acción y expander de edición.
      Al editar cualquier parámetro → plan recalcula en el siguiente render.

  ZONA 3 — Registro (expander colapsado)
      Formularios de activos financieros y objetivos de ahorro.
      Lógica idéntica a la v1 — solo cambió el contenedor.

  FOOTER
      Botón Guardar en Drive + sugerencias de desagregación.

Persistencia:
    Parámetros → state.set_position()
    Tablas     → st.session_state["schedules"][id]
    Plan params → st.session_state["plan_params"]
    Drive      → drive.save_positions() + drive.save_schedule()
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
import streamlit as st

from core import calculator, drive, planner, schedule, state

# ── Inicialización ─────────────────────────────────────────────────────────────
state.init_session_state()

# ── Constantes de UI ──────────────────────────────────────────────────────────
_TIPOS_ACTIVO = ["Fondo Mutuo", "APV", "ETF", "Renta Fija", "Otro"]

_TASA_DEFAULT: dict[str, float] = {
    "Fondo Mutuo": 5.0,
    "APV":         5.0,
    "ETF":         7.0,
    "Renta Fija":  4.0,
    "Otro":        5.0,
}

_ACT_PREFIX: dict[str, str] = {
    "Fondo Mutuo": "ACT_FM",
    "APV":         "ACT_APV",
    "ETF":         "ACT_ETF",
    "Renta Fija":  "ACT_RF",
    "Otro":        "ACT_OTR",
}

# Tipos y defaults para la sección "Otras inversiones" (inversión directa)
_TIPOS_INV = ["Acciones", "ETF", "Renta Fija", "Cripto", "Otro"]

_TASA_DEFAULT_INV: dict[str, float] = {
    "Acciones":   8.0,
    "ETF":        7.0,
    "Renta Fija": 4.0,
    "Cripto":    15.0,
    "Otro":       5.0,
}

_BUCKET_LABELS: dict[str, str] = {
    "GAS_ESE_BUCKET": "Esenciales",
    "GAS_IMP_BUCKET": "Importantes",
    "GAS_ASP_BUCKET": "Aspiraciones",
}

_ESTADO_BADGE: dict[str, tuple[str, str]] = {
    "completo": ("#16a34a", "✅ COMPLETO"),
    "en_curso": ("#d97706", "🔄 EN CURSO"),
    "pendiente": ("#6b7280", "⏳ PENDIENTE"),
}

# ── Tipos de cambio ────────────────────────────────────────────────────────────
_valor_uf: float = float(st.session_state.get("valor_uf", 39_700.0))
_valor_usd: float = float(st.session_state.get("valor_usd", 950.0))

# ── Helpers básicos ────────────────────────────────────────────────────────────


def _pos(pid: str) -> dict:
    return state.get_position(pid) or {}


def _clp(flujo: float, moneda: str) -> float:
    return calculator.normalizar_a_clp(flujo, moneda, _valor_uf, _valor_usd)


def _all_activo_fin_ids() -> list[str]:
    return state.list_positions(clase="Activo_Financiero")


def _all_objetivo_ids() -> list[str]:
    return state.list_positions(clase="Objetivo_Ahorro")


def _saldo_fin_proyectado(id_pos: str, default_saldo: float) -> float:
    tabla = st.session_state.get("schedules", {}).get(id_pos)
    if tabla is not None and not tabla.empty:
        return float(tabla["Saldo_Final"].iloc[-1])
    return default_saldo


def _saldo_restante_deuda_clp(pid: str) -> float:
    """Saldo restante de una deuda en CLP (desde schedule o parámetros)."""
    p = _pos(pid)
    moneda = p.get("Moneda", "CLP")
    tabla = st.session_state.get("schedules", {}).get(pid)
    hoy = date.today().strftime("%Y-%m")
    if tabla is not None and not tabla.empty:
        futuro = tabla[tabla["Periodo"] >= hoy]
        if not futuro.empty:
            saldo_orig = float(futuro["Saldo_Inicial"].iloc[0])
        else:
            saldo_orig = float(tabla["Saldo_Final"].iloc[-1])
    else:
        saldo_orig = float(
            p.get("Capital", p.get("Monto", p.get("Deuda_Total", p.get("Saldo_Actual", 0))))
        )
    return _clp(saldo_orig, moneda)


def _next_id_activo(tipo: str) -> str:
    prefix = _ACT_PREFIX.get(tipo, "ACT_OTR")
    existing = [
        pid for pid in state.list_positions(clase="Activo_Financiero")
        if pid.startswith(prefix + "_")
    ]
    nums: list[int] = []
    for pid in existing:
        try:
            nums.append(int(pid.split("_")[-1]))
        except ValueError:
            pass
    return f"{prefix}_{max(nums, default=0) + 1:03d}"


def _next_id_inversion(descripcion: str) -> str:
    import re  # noqa: PLC0415
    slug = re.sub(r"[^A-Z0-9]", "_", descripcion.upper().strip())[:20].strip("_")
    if not slug:
        slug = "INV"
    base = f"ACT_INV_{slug}"
    existing = state.list_positions(clase="Activo_Financiero")
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _next_id_objetivo(nombre: str) -> str:
    import re  # noqa: PLC0415
    slug = re.sub(r"[^A-Z0-9]", "_", nombre.upper().strip())[:20].strip("_")
    if not slug:
        slug = "META"
    base = f"OBJ_{slug}"
    existing = state.list_positions(clase="Objetivo_Ahorro")
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


@st.cache_resource
def _drive_service():
    return drive.authenticate_drive()


def _aplicar_sugerencia(sug: dict) -> None:
    id_pos = sug["id_posicion"]
    bucket = sug["bucket"]
    monto = float(sug["monto"])
    pos = state.get_position(id_pos)
    if pos is None:
        st.session_state["sugerencias_pendientes"] = [
            s for s in st.session_state.get("sugerencias_pendientes", [])
            if s.get("id_posicion") != id_pos
        ]
        return
    pos["bucket_vinculado"] = bucket
    pos["Cuota_Vinculada_CLP"] = monto
    state.set_position(id_pos, pos)
    espacio = calculator.espacio_disponible_bucket(st.session_state, bucket)
    if espacio < 0:
        bucket_pos = state.get_position(bucket) or {}
        bucket_pos["Monto_Mensual"] = float(
            bucket_pos.get("Monto_Mensual", 0)
        ) + abs(espacio)
        state.set_position(bucket, bucket_pos)
    st.session_state["sugerencias_pendientes"] = [
        s for s in st.session_state.get("sugerencias_pendientes", [])
        if s.get("id_posicion") != id_pos
    ]
    state.mark_dirty()


# ── Callbacks de parámetros del plan ─────────────────────────────────────────


def _cb_p2_meses() -> None:
    st.session_state.setdefault("plan_params", {})["meses_reserva_meta"] = (
        st.session_state["pplan_p2_meses"]
    )


def _cb_p3_tasa() -> None:
    st.session_state.setdefault("plan_params", {})["tasa_reemplazo"] = (
        st.session_state["pplan_p3_tasa"] / 100.0
    )


def _cb_p3_anos() -> None:
    st.session_state.setdefault("plan_params", {})["anos_retiro"] = (
        st.session_state["pplan_p3_anos"]
    )


def _cb_p3_edad() -> None:
    st.session_state.setdefault("plan_params", {})["edad_jubilacion"] = float(
        st.session_state["pplan_p3_edad"]
    )


def _cb_p4_dist() -> None:
    inv = st.session_state.get("pplan_p4_inv", 50) / 100.0
    ev = st.session_state.get("pplan_p4_ev", 30) / 100.0
    libre = max(0.0, 1.0 - inv - ev)
    st.session_state.setdefault("plan_params", {})["distribucion_paso4"] = {
        "inversion": inv,
        "estilo_vida": ev,
        "libre": libre,
    }


# ── Guardia de capa ─────────────────────────────────────────────────────────
if state.get_layer() < 3:
    st.warning(
        "🔒 **Capa 3 bloqueada.** "
        "Completa los requisitos de Capa 2 (al menos un pasivo con tabla + saldo AFP)."
    )
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════
# Datos base — calculados una sola vez para todas las zonas
# ═══════════════════════════════════════════════════════════════════════════

# Activos financieros: AFP + APV + ACT_INV_* y todos los Activo_Financiero
_activos_fin_ids = _all_activo_fin_ids()
_all_fin_portfolio_ids = [
    p for p in state.list_positions()
    if (state.get_position(p) or {}).get("Clase") in ("Activo_Financiero", "Prevision_AFP")
]
_valor_portafolio_clp: float = sum(
    _clp(float(_pos(aid).get("Saldo_Actual", 0)), _pos(aid).get("Moneda", "CLP"))
    for aid in _all_fin_portfolio_ids
)

# Activos reales
_activos_reales_ids = state.list_positions(clase="Activo_Real")
_valor_real_clp: float = sum(
    _clp(float(_pos(aid).get("Valor_Comercial", 0)), _pos(aid).get("Moneda", "CLP"))
    for aid in _activos_reales_ids
)

# Activo líquido
_liq_p = _pos("ACT_LIQUIDO_PRINCIPAL")
_liq_clp = _clp(float(_liq_p.get("Saldo_Actual", 0)), _liq_p.get("Moneda", "CLP"))

# Pasivos
_hipo_ids = state.list_positions(clase="Pasivo_Estructural")
_consumo_ids = state.list_positions(clase="Pasivo_Corto_Plazo")

_total_pasivos_hipo_clp: float = sum(_saldo_restante_deuda_clp(pid) for pid in _hipo_ids)
_total_pasivos_consumo_clp: float = sum(_saldo_restante_deuda_clp(pid) for pid in _consumo_ids)

# Totales
_total_activos_clp = _liq_clp + _valor_portafolio_clp + _valor_real_clp
_total_pasivos_clp = _total_pasivos_hipo_clp + _total_pasivos_consumo_clp
_patrimonio_neto_clp = _total_activos_clp - _total_pasivos_clp

# ═══════════════════════════════════════════════════════════════════════════
# TÍTULO DE PÁGINA
# ═══════════════════════════════════════════════════════════════════════════

st.title("📈 Capa 3 — Crecimiento")

# ═══════════════════════════════════════════════════════════════════════════
# ZONA 1 — BALANCE PATRIMONIAL
# ═══════════════════════════════════════════════════════════════════════════

st.markdown("## 💼 Balance Patrimonial")

_c_act, _c_pas, _c_net = st.columns(3, gap="medium")

# ── Card ACTIVOS ──────────────────────────────────────────────────────────────
with _c_act:
    _items_html = ""
    # Activos financieros: AFP + APV + ACT_INV_*
    _all_fin_ids = [
        p for p in state.list_positions()
        if (state.get_position(p) or {}).get("Clase") in ("Activo_Financiero", "Prevision_AFP")
    ]
    if _all_fin_ids:
        _items_html += (
            "<p style='color:#86efac; font-size:0.72rem; font-weight:700; "
            "margin:8px 0 3px 0; text-transform:uppercase; letter-spacing:.05em;'>"
            "Financieros</p>"
        )
        for _fin_id in _all_fin_ids:
            _fin_p = state.get_position(_fin_id) or {}
            _fin_saldo = float(_fin_p.get("Saldo_Actual", 0))
            _fin_moneda = _fin_p.get("Moneda", "CLP")
            _fin_clp = calculator.normalizar_a_clp(_fin_saldo, _fin_moneda, _valor_uf, _valor_usd)
            _fin_desc = _fin_p.get("Descripcion", _fin_id)
            _items_html += (
                f"<p style='margin:2px 0; font-size:0.82rem;'>"
                f"· {_fin_desc}: $ {int(_fin_clp):,}</p>"
            )
    if _activos_reales_ids:
        _items_html += (
            "<p style='color:#86efac; font-size:0.72rem; font-weight:700; "
            "margin:8px 0 3px 0; text-transform:uppercase; letter-spacing:.05em;'>"
            "Reales</p>"
        )
        for _aid in _activos_reales_ids:
            _ap = _pos(_aid)
            _aval = _clp(float(_ap.get("Valor_Comercial", 0)), _ap.get("Moneda", "CLP"))
            _items_html += (
                f"<p style='margin:2px 0; font-size:0.82rem;'>"
                f"· {_ap.get('Descripcion', _aid)}: $ {int(_aval):,}</p>"
            )
    if _liq_clp > 0:
        _items_html += (
            "<p style='color:#86efac; font-size:0.72rem; font-weight:700; "
            "margin:8px 0 3px 0; text-transform:uppercase; letter-spacing:.05em;'>"
            "Líquidos</p>"
            f"<p style='margin:2px 0; font-size:0.82rem;'>"
            f"· Cuenta principal: $ {int(_liq_clp):,}</p>"
        )
    if not _items_html:
        _items_html = "<p style='color:#86efac; font-size:0.82rem;'>Sin activos registrados.</p>"

    st.markdown(
        f"""<div style='background:#14532d; border:1px solid #166534; padding:16px;
            border-radius:12px; color:white; min-height:200px;'>
            <h4 style='color:#4ade80; margin:0 0 8px 0; font-size:0.95rem;
                text-transform:uppercase; letter-spacing:.06em;'>ACTIVOS</h4>
            {_items_html}
            <hr style='border:none; border-top:1px solid #166534; margin:12px 0 8px 0;'>
            <p style='margin:0; font-size:0.72rem; color:#86efac;'>TOTAL ACTIVOS</p>
            <p style='margin:4px 0 0 0; font-size:1.4rem; font-weight:800;'>
                $ {int(_total_activos_clp):,}</p>
        </div>""",
        unsafe_allow_html=True,
    )

# ── Card PASIVOS ──────────────────────────────────────────────────────────────
with _c_pas:
    _pas_items_html = ""
    if _hipo_ids:
        _pas_items_html += (
            "<p style='color:#fca5a5; font-size:0.72rem; font-weight:700; "
            "margin:8px 0 3px 0; text-transform:uppercase; letter-spacing:.05em;'>"
            "Hipotecarios</p>"
        )
        for _pid in _hipo_ids:
            _pp = _pos(_pid)
            _psaldo = _saldo_restante_deuda_clp(_pid)
            _pas_items_html += (
                f"<p style='margin:2px 0; font-size:0.82rem;'>"
                f"· {_pp.get('Descripcion', _pid)}: $ {int(_psaldo):,}</p>"
            )
    if _consumo_ids:
        _pas_items_html += (
            "<p style='color:#fca5a5; font-size:0.72rem; font-weight:700; "
            "margin:8px 0 3px 0; text-transform:uppercase; letter-spacing:.05em;'>"
            "Consumo / Otros</p>"
        )
        for _pid in _consumo_ids:
            _pp = _pos(_pid)
            _psaldo = _saldo_restante_deuda_clp(_pid)
            _pas_items_html += (
                f"<p style='margin:2px 0; font-size:0.82rem;'>"
                f"· {_pp.get('Descripcion', _pid)}: $ {int(_psaldo):,}</p>"
            )
    if not _pas_items_html:
        _pas_items_html = "<p style='color:#fca5a5; font-size:0.82rem;'>Sin pasivos registrados.</p>"

    st.markdown(
        f"""<div style='background:#450a0a; border:1px solid #7f1d1d; padding:16px;
            border-radius:12px; color:white; min-height:200px;'>
            <h4 style='color:#f87171; margin:0 0 8px 0; font-size:0.95rem;
                text-transform:uppercase; letter-spacing:.06em;'>PASIVOS</h4>
            {_pas_items_html}
            <hr style='border:none; border-top:1px solid #7f1d1d; margin:12px 0 8px 0;'>
            <p style='margin:0; font-size:0.72rem; color:#fca5a5;'>TOTAL PASIVOS</p>
            <p style='margin:4px 0 0 0; font-size:1.4rem; font-weight:800;'>
                $ {int(_total_pasivos_clp):,}</p>
        </div>""",
        unsafe_allow_html=True,
    )

# ── Card NET WORTH ────────────────────────────────────────────────────────────
with _c_net:
    _nw_color = "#34d399" if _patrimonio_neto_clp >= 0 else "#f87171"
    st.markdown(
        f"""<div style='background:#0f172a; border:2px solid #334155; padding:16px;
            border-radius:12px; color:white; min-height:200px;'>
            <h4 style='color:#93c5fd; margin:0 0 4px 0; font-size:0.95rem;
                text-transform:uppercase; letter-spacing:.06em;'>NET WORTH</h4>
            <p style='margin:0 0 12px 0; font-size:2.2rem; font-weight:900;
                color:{_nw_color};'>$ {int(_patrimonio_neto_clp):,}</p>
            <p style='margin:0; font-size:0.72rem; color:#94a3b8;'>
                Activos $ {int(_total_activos_clp):,} − Pasivos $ {int(_total_pasivos_clp):,}</p>
        </div>""",
        unsafe_allow_html=True,
    )

    # Pie chart de distribución de activos
    _dist_labels, _dist_values, _dist_colors = [], [], []
    if _liq_clp > 0:
        _dist_labels.append("Líquido")
        _dist_values.append(_liq_clp)
        _dist_colors.append("#2ECC71")
    if _valor_portafolio_clp > 0:
        _dist_labels.append("Financiero")
        _dist_values.append(_valor_portafolio_clp)
        _dist_colors.append("#3498DB")
    if _valor_real_clp > 0:
        _dist_labels.append("Real")
        _dist_values.append(_valor_real_clp)
        _dist_colors.append("#9B59B6")
    if _dist_values:
        _fig_pie = go.Figure(
            go.Pie(
                labels=_dist_labels,
                values=_dist_values,
                marker=dict(colors=_dist_colors),
                hole=0.45,
                textinfo="label+percent",
                hovertemplate="%{label}<br>$ %{value:,.0f}<extra></extra>",
            )
        )
        _fig_pie.update_layout(
            height=200,
            margin=dict(l=0, r=0, t=12, b=0),
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(_fig_pie, use_container_width=True)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════
# ZONA 2 — PLAN FINANCIERO
# ═══════════════════════════════════════════════════════════════════════════

st.markdown("## 🎯 Tu Plan Financiero")
st.caption(
    "Basado en tu balance actual — edita cualquier meta para ajustar el plan"
)

# Generar plan desde el estado actual (incluye plan_params editados)
_plan = planner.generar_plan(st.session_state)

for _paso in _plan:
    _n = _paso["numero"]
    _estado = _paso["estado"]
    _badge_color, _badge_text = _ESTADO_BADGE.get(_estado, ("#6b7280", _estado.upper()))
    _pp_params = _paso["params"]

    with st.container(border=True):
        _hcol1, _hcol2 = st.columns([5, 1])
        with _hcol1:
            st.markdown(f"#### {_n}. {_paso['titulo']}")
        with _hcol2:
            st.markdown(
                f"<div style='background:{_badge_color}; color:white; padding:4px 8px; "
                f"border-radius:20px; font-size:0.75rem; text-align:center; "
                f"margin-top:10px; white-space:nowrap;'>{_badge_text}</div>",
                unsafe_allow_html=True,
            )

        # Diagnóstico
        for _line in _paso["diagnostico"].split("\n"):
            if _line.strip():
                st.markdown(_line)

        # Acción sugerida
        if _paso["accion"]:
            st.info(f"💡 **Acción:** {_paso['accion']}")

        # Métricas: monto/mes + plazo
        _show_monto = _paso["monto_mensual"] > 0
        _show_plazo = 0 < _paso["plazo_meses"] < 999
        if _show_monto or _show_plazo:
            _mc1, _mc2 = st.columns(2)
            with _mc1:
                if _show_monto:
                    st.metric("💰 /mes", f"$ {int(_paso['monto_mensual']):,}")
            with _mc2:
                if _show_plazo:
                    _m = _paso["plazo_meses"]
                    _plazo_str = (
                        f"{_m // 12} año{'s' if _m // 12 != 1 else ''} "
                        f"{_m % 12} mes{'es' if _m % 12 != 1 else ''}"
                        if _m >= 12
                        else f"{_m} meses"
                    )
                    st.metric("📅 Plazo estimado", _plazo_str)

        # Expander de parámetros editables
        with st.expander(f"✏️ Editar parámetros — Paso {_n}"):
            if _n == 1:
                _deudas_ord = _pp_params.get("deudas_ordenadas", [])
                if _deudas_ord:
                    st.markdown("**Orden de pago (avalanche por tasa — mayor tasa primero):**")
                    for _i, _d in enumerate(_deudas_ord):
                        st.markdown(
                            f"{_i + 1}. **{_d['descripcion']}** — "
                            f"{_d['tasa_anual'] * 100:.1f}% anual | "
                            f"Saldo: $ {int(_d['saldo_clp']):,}"
                        )
                    st.caption(
                        "El orden es automático (mayor tasa → mayor ahorro en intereses). "
                        "Para cambiar el orden, modifica las tasas de cada deuda en Capa 2."
                    )
                else:
                    st.info("Sin deudas de consumo registradas. ¡Nada que liquidar!")

            elif _n == 2:
                _cur_meses = int(
                    st.session_state.get("plan_params", {}).get(
                        "meses_reserva_meta", _pp_params.get("meses_reserva_meta", 6)
                    )
                )
                st.slider(
                    "Meses de reserva objetivo",
                    min_value=3,
                    max_value=12,
                    value=_cur_meses,
                    step=1,
                    key="pplan_p2_meses",
                    on_change=_cb_p2_meses,
                    help="Número de meses de gastos esenciales que debe cubrir el fondo.",
                )

            elif _n == 3:
                _pp3 = st.session_state.get("plan_params", {})
                _cur_tasa = int(float(_pp3.get("tasa_reemplazo", _pp_params.get("tasa_reemplazo", 0.70))) * 100)
                _cur_anos = int(_pp3.get("anos_retiro", _pp_params.get("anos_retiro", 20)))
                _cur_edad = int(_pp3.get("edad_jubilacion", _pp_params.get("edad_jubilacion", 65)))
                _cp3a, _cp3b = st.columns(2)
                with _cp3a:
                    st.slider(
                        "Tasa de reemplazo (%)",
                        min_value=50,
                        max_value=100,
                        value=_cur_tasa,
                        step=5,
                        key="pplan_p3_tasa",
                        on_change=_cb_p3_tasa,
                        help="% de tu ingreso actual que quieres como pensión mensual.",
                    )
                    st.number_input(
                        "Edad de jubilación",
                        min_value=50,
                        max_value=80,
                        value=_cur_edad,
                        step=1,
                        key="pplan_p3_edad",
                        on_change=_cb_p3_edad,
                    )
                with _cp3b:
                    st.number_input(
                        "Años de retiro",
                        min_value=5,
                        max_value=40,
                        value=_cur_anos,
                        step=1,
                        key="pplan_p3_anos",
                        on_change=_cb_p3_anos,
                        help="Cuántos años planeas vivir en retiro (determina la meta total).",
                    )

            elif _n == 4:
                _dist = _pp_params.get(
                    "distribucion_paso4",
                    {"inversion": 0.50, "estilo_vida": 0.30, "libre": 0.20},
                )
                _cur_inv = int(round(_dist.get("inversion", 0.50) * 100))
                _cur_ev = int(round(_dist.get("estilo_vida", 0.30) * 100))
                st.markdown("**Distribución del margen libre mensual**")
                st.caption("Ajusta inversión y estilo de vida — libre se calcula solo.")
                _dp4a, _dp4b = st.columns(2)
                with _dp4a:
                    st.slider(
                        "📈 Inversión largo plazo (%)",
                        min_value=0,
                        max_value=100,
                        value=_cur_inv,
                        step=5,
                        key="pplan_p4_inv",
                        on_change=_cb_p4_dist,
                    )
                with _dp4b:
                    st.slider(
                        "🏖️ Estilo de vida (%)",
                        min_value=0,
                        max_value=100,
                        value=_cur_ev,
                        step=5,
                        key="pplan_p4_ev",
                        on_change=_cb_p4_dist,
                    )
                _libre_pct = max(
                    0,
                    100
                    - st.session_state.get("pplan_p4_inv", _cur_inv)
                    - st.session_state.get("pplan_p4_ev", _cur_ev),
                )
                if _libre_pct < 0:
                    st.error("Los porcentajes superan el 100%. Ajusta los sliders.")
                else:
                    st.metric("🎯 Libre / colchón", f"{_libre_pct}%")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════
# ZONA 3 — Registro en Capa 2
# ═══════════════════════════════════════════════════════════════════════════

st.divider()
if st.button(
    "⚙️ Gestionar activos y pasivos → Capa 2",
    use_container_width=True,
    help="El registro de activos, inversiones y pasivos se hace en Capa 2",
):
    st.switch_page("pages/03_capa2_control.py")

# ═══════════════════════════════════════════════════════════════════════════
# FOOTER — Guardar + Sugerencias + Banner Capa 4
# ═══════════════════════════════════════════════════════════════════════════

st.divider()

# ── Sugerencias de desagregación ─────────────────────────────────────────────
_sugerencias = st.session_state.get("sugerencias_pendientes", [])
if _sugerencias:
    st.markdown(f"#### 💡 Sugerencias de desagregación ({len(_sugerencias)})")
    st.caption(
        "Vincula las cuotas de tus compromisos a los buckets de gasto "
        "para reflejar la realidad de tu presupuesto."
    )
    for _sug in list(_sugerencias):
        _sug_id = _sug["id"]
        _bucket_lbl = _BUCKET_LABELS.get(_sug["bucket"], _sug["bucket"])
        with st.container():
            _cs1, _cs2 = st.columns([4, 2])
            with _cs1:
                st.markdown(
                    f"**{_sug['descripcion']}** · *{_sug['tipo']}*  \n"
                    f"Vincular **$ {int(_sug['monto']):,}/mes** → {_bucket_lbl}"
                )
                if _sug.get("excede_espacio", False):
                    st.warning(
                        f"⚠️ Tu {_sug['tipo']} real es "
                        f"**$ {int(_sug.get('exceso_clp', 0)):,}** "
                        f"mayor que lo disponible en {_bucket_lbl}. "
                        "Aplicar ajustará el bucket automáticamente."
                    )
            with _cs2:
                if st.button(
                    "✓ Aplicar",
                    key=f"sug3_ap_{_sug_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    _aplicar_sugerencia(_sug)
                    st.rerun()
                if st.button(
                    "✕ Descartar",
                    key=f"sug3_dc_{_sug_id}",
                    use_container_width=True,
                ):
                    st.session_state["sugerencias_pendientes"] = [
                        s for s in st.session_state.get("sugerencias_pendientes", [])
                        if s["id"] != _sug_id
                    ]
                    st.rerun()
    st.divider()

# ── Guardar + Banner Capa 4 ───────────────────────────────────────────────────
_col_save, _col_banner = st.columns([2, 3])

with _col_save:
    _dirty = state.is_dirty()
    if st.button(
        "💾 Guardar en Drive",
        type="primary",
        disabled=not _dirty,
        use_container_width=True,
        help="Guarda posiciones, tablas y parámetros del plan en Drive."
        if _dirty
        else "No hay cambios pendientes.",
    ):
        with st.spinner("Guardando en Drive…"):
            try:
                _svc = _drive_service()
                _folders = drive.ensure_folder_structure(_svc)
                drive.save_positions(
                    _svc, _folders, st.session_state.get("positions", {})
                )
                for _pid, _tabla in st.session_state.get("schedules", {}).items():
                    drive.save_schedule(_svc, _folders, _pid, _tabla)
                from datetime import datetime  # noqa: PLC0415
                state.mark_clean(datetime.now())
                st.success("✓ Guardado en Drive")
                st.rerun()
            except Exception as _exc:
                st.error(f"Error al guardar: {_exc}")
    st.caption(state.status_label())

with _col_banner:
    if state.get_layer() >= 4:
        st.success(
            "🎉 **Capa 4 desbloqueada.** "
            "Tienes activos financieros y objetivos activos. "
            "¡ALM completo disponible!"
        )
    elif not _all_activo_fin_ids():
        st.info("💡 Agrega un activo financiero (Zona 3) para avanzar hacia Capa 4.")
    elif not _all_objetivo_ids():
        st.info("💡 Agrega al menos un objetivo de ahorro (Zona 3) para desbloquear Capa 4.")
