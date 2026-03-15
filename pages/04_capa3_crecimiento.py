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

# Activos financieros (Capa 3)
_activos_fin_ids = _all_activo_fin_ids()
_valor_portafolio_clp: float = sum(
    _clp(float(_pos(aid).get("Saldo_Actual", 0)), _pos(aid).get("Moneda", "CLP"))
    for aid in _activos_fin_ids
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
    if _activos_fin_ids:
        _items_html += (
            "<p style='color:#86efac; font-size:0.72rem; font-weight:700; "
            "margin:8px 0 3px 0; text-transform:uppercase; letter-spacing:.05em;'>"
            "Financieros</p>"
        )
        for _aid in _activos_fin_ids:
            _ap = _pos(_aid)
            _asaldo = _clp(float(_ap.get("Saldo_Actual", 0)), _ap.get("Moneda", "CLP"))
            _items_html += (
                f"<p style='margin:2px 0; font-size:0.82rem;'>"
                f"· {_ap.get('Descripcion', _aid)}: $ {int(_asaldo):,}</p>"
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
# ZONA 3 — REGISTRO (colapsado por defecto)
# ═══════════════════════════════════════════════════════════════════════════

_zona3_expanded = (
    st.session_state.get("c3_show_activo_form", False)
    or st.session_state.get("c3_show_obj_form", False)
    or st.session_state.get("c3_show_inv_form", False)
    or bool(st.session_state.pop("c3_auto_open_zona3", False))
)

with st.expander("⚙️ Gestionar activos y objetivos", expanded=_zona3_expanded):

    # ── Tipos de cambio ───────────────────────────────────────────────────────
    with st.expander("💱 Tipos de cambio", expanded=False):
        _c_uf_inp, _c_usd_inp = st.columns(2)
        with _c_uf_inp:
            _uf_inp = st.number_input(
                "UF (CLP)", min_value=1.0, value=_valor_uf, step=100.0, format="%.0f",
            )
        with _c_usd_inp:
            _usd_inp = st.number_input(
                "USD (CLP)", min_value=1.0, value=_valor_usd, step=10.0, format="%.0f",
            )
        if _uf_inp != _valor_uf or _usd_inp != _valor_usd:
            st.session_state["valor_uf"] = _uf_inp
            st.session_state["valor_usd"] = _usd_inp
            state.mark_dirty()
            st.rerun()

    # ── Activos financieros ───────────────────────────────────────────────────
    st.markdown("### 💼 Mis activos financieros")
    activo_ids = _all_activo_fin_ids()

    if not st.session_state.get("c3_show_activo_form", False):
        if st.button("➕ Agregar activo", use_container_width=True, key="btn_add_activo"):
            for _k in [
                "c3_act_tipo", "c3_act_desc", "c3_act_saldo", "c3_act_aporte",
                "c3_act_tasa", "c3_act_moneda", "c3_act_horizonte",
            ]:
                st.session_state.pop(_k, None)
            st.session_state["c3_show_activo_form"] = True
            st.session_state.pop("c3_edit_activo_id", None)
            st.rerun()

    if activo_ids:
        for _act_id in activo_ids:
            _act_p = _pos(_act_id)
            _act_saldo = float(_act_p.get("Saldo_Actual", 0))
            _act_moneda = _act_p.get("Moneda", "CLP")
            _act_proy = _saldo_fin_proyectado(_act_id, _act_saldo)
            _act_proy_clp = _clp(_act_proy, _act_moneda)
            _act_saldo_clp = _clp(_act_saldo, _act_moneda)
            _horizonte_anos = int(_act_p.get("Horizonte_Meses", 12)) // 12
            c_a1, c_a2, c_a3 = st.columns([5, 1, 1])
            with c_a1:
                _saldo_str = (
                    f"{_act_moneda} {_act_saldo:,.2f} → $ {int(_act_saldo_clp):,}"
                    if _act_moneda != "CLP"
                    else f"$ {int(_act_saldo_clp):,}"
                )
                _proy_str = (
                    f"{_act_moneda} {_act_proy:,.2f} → $ {int(_act_proy_clp):,}"
                    if _act_moneda != "CLP"
                    else f"$ {int(_act_proy_clp):,}"
                )
                st.markdown(
                    f"**{_act_p.get('Descripcion', _act_id)}** · "
                    f"*{_act_p.get('Tipo', '')}*  \n"
                    f"Saldo {_saldo_str} · Proy. {_proy_str} ({_horizonte_anos} años)"
                )
                st.caption(f"Rendimiento esperado {_act_p.get('Tasa_Anual_Pct', 5.0):.1f}% anual")
            with c_a2:
                if st.button("✏️", key=f"edit_act_{_act_id}", help="Editar"):
                    for _k in [
                        "c3_act_tipo", "c3_act_desc", "c3_act_saldo", "c3_act_aporte",
                        "c3_act_tasa", "c3_act_moneda", "c3_act_horizonte",
                    ]:
                        st.session_state.pop(_k, None)
                    st.session_state["c3_show_activo_form"] = True
                    st.session_state["c3_edit_activo_id"] = _act_id
                    st.rerun()
            with c_a3:
                if st.button("🗑️", key=f"del_act_{_act_id}", help="Eliminar"):
                    state.delete_position(_act_id)
                    st.session_state.get("schedules", {}).pop(_act_id, None)
                    _act_lista: list = st.session_state.setdefault("activos_con_tabla", [])
                    if _act_id in _act_lista:
                        _act_lista.remove(_act_id)
                    state.update_layer()
                    state.mark_dirty()
                    st.rerun()
    elif not st.session_state.get("c3_show_activo_form", False):
        st.info(
            "Aún no registraste activos financieros. "
            "Usa ➕ para agregar fondos mutuos, ETFs, renta fija, etc."
        )

    # Formulario de activo financiero
    if st.session_state.get("c3_show_activo_form", False):
        _edit_act_id: str | None = st.session_state.get("c3_edit_activo_id")
        _edit_act_p = state.get_position(_edit_act_id) or {} if _edit_act_id else {}
        _modo_act = "✏️ Editar activo" if _edit_act_id else "➕ Nuevo activo financiero"

        with st.form("form_activo"):
            st.markdown(f"**{_modo_act}**")
            _tipos_idx = (
                _TIPOS_ACTIVO.index(_edit_act_p.get("Tipo", _TIPOS_ACTIVO[0]))
                if _edit_act_p.get("Tipo") in _TIPOS_ACTIVO
                else 0
            )
            _tipo_act = st.selectbox(
                "Tipo de activo", _TIPOS_ACTIVO, index=_tipos_idx, key="c3_act_tipo"
            )
            _desc_act = st.text_input(
                "Descripción",
                value=_edit_act_p.get("Descripcion", ""),
                placeholder="Ej: Fondo Moderado BCI, iShares S&P500…",
                key="c3_act_desc",
            )
            _col_fa1, _col_fa2 = st.columns(2)
            with _col_fa1:
                _moneda_act = st.selectbox(
                    "Moneda",
                    ["CLP", "UF", "USD"],
                    index=["CLP", "UF", "USD"].index(_edit_act_p.get("Moneda", "CLP")),
                    key="c3_act_moneda",
                )
                _step_a = 1.0 if _moneda_act in ("UF", "USD") else 100_000
                _saldo_act = st.number_input(
                    f"Saldo actual ({_moneda_act})",
                    min_value=0.0,
                    value=float(_edit_act_p.get("Saldo_Actual", 0)),
                    step=float(_step_a),
                    key="c3_act_saldo",
                )
                _aporte_act = st.number_input(
                    f"Aporte mensual ({_moneda_act})",
                    min_value=0.0,
                    value=float(_edit_act_p.get("Aporte_Mensual", 0)),
                    step=float(_step_a),
                    key="c3_act_aporte",
                    help="Puede ser 0 si no realizas aportes periódicos.",
                )
            with _col_fa2:
                _tasa_def = _TASA_DEFAULT.get(_tipo_act, 5.0)
                _tasa_act = st.number_input(
                    "Tasa esperada anual (%)",
                    min_value=0.0,
                    max_value=50.0,
                    value=float(_edit_act_p.get("Tasa_Anual_Pct", _tasa_def)),
                    step=0.5,
                    format="%.1f",
                    key="c3_act_tasa",
                )
                _horiz_years = st.slider(
                    "Horizonte (años)",
                    min_value=1,
                    max_value=40,
                    value=int(_edit_act_p.get("Horizonte_Meses", 60)) // 12,
                    key="c3_act_horizonte",
                )
            _col_ok_a, _col_can_a = st.columns(2)
            with _col_ok_a:
                _act_ok = st.form_submit_button("✓ Confirmar", type="primary", use_container_width=True)
            with _col_can_a:
                _act_cancel = st.form_submit_button("✕ Cancelar", use_container_width=True)

        if _act_cancel:
            st.session_state["c3_show_activo_form"] = False
            st.session_state.pop("c3_edit_activo_id", None)
            st.rerun()

        if _act_ok:
            if not _desc_act.strip():
                st.error("La descripción no puede estar vacía.")
                st.stop()
            _horiz_meses = _horiz_years * 12
            _id_final_act = _edit_act_id if _edit_act_id else _next_id_activo(_tipo_act)
            _params_act: dict = {
                "Tipo": _tipo_act,
                "Descripcion": _desc_act.strip(),
                "Clase": "Activo_Financiero",
                "Moneda": _moneda_act,
                "Capa_Activacion": 3,
                "Saldo_Actual": _saldo_act,
                "Aporte_Mensual": _aporte_act,
                "Tasa_Anual": _tasa_act / 100,
                "Tasa_Anual_Pct": _tasa_act,
                "Horizonte_Meses": _horiz_meses,
                "Fecha_Inicio": date.today().isoformat(),
            }
            try:
                _tabla_act = schedule.gen_fondo_inversion(
                    saldo=float(_saldo_act),
                    aporte_mensual=float(_aporte_act),
                    tasa_anual=_tasa_act / 100,
                    horizonte_meses=_horiz_meses,
                    fecha_inicio=date.today(),
                    moneda=_moneda_act,
                    id_posicion=_id_final_act,
                )
                state.set_position(_id_final_act, _params_act)
                st.session_state.setdefault("schedules", {})[_id_final_act] = _tabla_act
                _act_list: list = st.session_state.setdefault("activos_con_tabla", [])
                if _id_final_act not in _act_list:
                    _act_list.append(_id_final_act)
                state.update_layer()
                state.mark_dirty()
                st.session_state["c3_show_activo_form"] = False
                st.session_state.pop("c3_edit_activo_id", None)
                st.rerun()
            except Exception as _exc:
                st.error(f"Error al generar proyección: {_exc}")

    # ── Otras inversiones ────────────────────────────────────────────────────
    st.divider()
    st.markdown("### 💹 Otras inversiones")
    st.caption("Acciones, ETFs, renta fija, cripto u otros activos de inversión directa.")
    _inv_ids = [
        pid for pid in state.list_positions(clase="Activo_Financiero")
        if pid.startswith("ACT_INV_")
    ]

    if not st.session_state.get("c3_show_inv_form", False):
        if st.button("➕ Agregar inversión", use_container_width=True, key="btn_add_inv"):
            for _k in [
                "c3_inv_tipo", "c3_inv_desc", "c3_inv_saldo",
                "c3_inv_aporte", "c3_inv_tasa", "c3_inv_horizonte",
            ]:
                st.session_state.pop(_k, None)
            st.session_state["c3_show_inv_form"] = True
            st.session_state.pop("c3_edit_inv_id", None)
            st.rerun()

    if _inv_ids:
        for _inv_id in _inv_ids:
            _inv_p = _pos(_inv_id)
            _inv_saldo = float(_inv_p.get("Saldo_Actual", 0))
            _inv_proy = _saldo_fin_proyectado(_inv_id, _inv_saldo)
            _inv_proy_clp = _clp(_inv_proy, "CLP")
            _inv_saldo_clp = _clp(_inv_saldo, "CLP")
            _inv_horiz_anos = int(_inv_p.get("Horizonte_Meses", 60)) // 12
            c_iv1, c_iv2, c_iv3 = st.columns([5, 1, 1])
            with c_iv1:
                st.markdown(
                    f"**{_inv_p.get('Descripcion', _inv_id)}** · "
                    f"*{_inv_p.get('Tipo', '')}*  \n"
                    f"Saldo $ {int(_inv_saldo_clp):,} · Proy. $ {int(_inv_proy_clp):,} "
                    f"({_inv_horiz_anos} años)"
                )
                st.caption(
                    f"Rendimiento esperado {_inv_p.get('Tasa_Anual_Pct', 7.0):.1f}% anual"
                )
            with c_iv2:
                if st.button("✏️", key=f"edit_inv_{_inv_id}", help="Editar"):
                    for _k in [
                        "c3_inv_tipo", "c3_inv_desc", "c3_inv_saldo",
                        "c3_inv_aporte", "c3_inv_tasa", "c3_inv_horizonte",
                    ]:
                        st.session_state.pop(_k, None)
                    st.session_state["c3_show_inv_form"] = True
                    st.session_state["c3_edit_inv_id"] = _inv_id
                    st.rerun()
            with c_iv3:
                if st.button("🗑️", key=f"del_inv_{_inv_id}", help="Eliminar"):
                    state.delete_position(_inv_id)
                    st.session_state.get("schedules", {}).pop(_inv_id, None)
                    _act_lista_inv: list = st.session_state.setdefault("activos_con_tabla", [])
                    if _inv_id in _act_lista_inv:
                        _act_lista_inv.remove(_inv_id)
                    state.update_layer()
                    state.mark_dirty()
                    st.rerun()
    elif not st.session_state.get("c3_show_inv_form", False):
        st.info("Sin inversiones directas registradas. Usa ➕ para agregar acciones, ETFs, cripto, etc.")

    if st.session_state.get("c3_show_inv_form", False):
        _edit_inv_id: str | None = st.session_state.get("c3_edit_inv_id")
        _edit_inv_p = state.get_position(_edit_inv_id) or {} if _edit_inv_id else {}
        _modo_inv = "✏️ Editar inversión" if _edit_inv_id else "➕ Nueva inversión directa"

        with st.form("form_inversion"):
            st.markdown(f"**{_modo_inv}**")
            _tipos_inv_idx = (
                _TIPOS_INV.index(_edit_inv_p.get("Tipo", _TIPOS_INV[0]))
                if _edit_inv_p.get("Tipo") in _TIPOS_INV
                else 0
            )
            _tipo_inv = st.selectbox(
                "Tipo de inversión", _TIPOS_INV, index=_tipos_inv_idx, key="c3_inv_tipo"
            )
            _desc_inv = st.text_input(
                "Descripción",
                value=_edit_inv_p.get("Descripcion", ""),
                placeholder="Ej: Apple AAPL, Bitcoin, US Treasury 2032…",
                key="c3_inv_desc",
            )
            _col_fi1, _col_fi2 = st.columns(2)
            with _col_fi1:
                _saldo_inv = st.number_input(
                    "Saldo actual (CLP)",
                    min_value=0,
                    value=int(_edit_inv_p.get("Saldo_Actual", 0)),
                    step=1_000,
                    key="c3_inv_saldo",
                )
                _aporte_inv = st.number_input(
                    "Aporte mensual (CLP)",
                    min_value=0,
                    value=int(_edit_inv_p.get("Aporte_Mensual", 0)),
                    step=1_000,
                    key="c3_inv_aporte",
                    help="0 si no realizas aportes periódicos.",
                )
            with _col_fi2:
                _tasa_def_inv = _TASA_DEFAULT_INV.get(_tipo_inv, 7.0)
                _tasa_inv = st.number_input(
                    "Tasa esperada anual (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(_edit_inv_p.get("Tasa_Anual_Pct", _tasa_def_inv)),
                    step=0.5,
                    format="%.1f",
                    key="c3_inv_tasa",
                )
                _horiz_inv_years = st.slider(
                    "Horizonte (años)",
                    min_value=1,
                    max_value=40,
                    value=int(_edit_inv_p.get("Horizonte_Meses", 60)) // 12,
                    key="c3_inv_horizonte",
                )
            _col_ok_inv, _col_can_inv = st.columns(2)
            with _col_ok_inv:
                _inv_ok = st.form_submit_button(
                    "✓ Confirmar", type="primary", use_container_width=True
                )
            with _col_can_inv:
                _inv_cancel = st.form_submit_button("✕ Cancelar", use_container_width=True)

        if _inv_cancel:
            st.session_state["c3_show_inv_form"] = False
            st.session_state.pop("c3_edit_inv_id", None)
            st.rerun()

        if _inv_ok:
            if not _desc_inv.strip():
                st.error("La descripción no puede estar vacía.")
                st.stop()
            _horiz_inv_meses = _horiz_inv_years * 12
            _id_final_inv = _edit_inv_id if _edit_inv_id else _next_id_inversion(_desc_inv)
            _params_inv: dict = {
                "Tipo": _tipo_inv,
                "Descripcion": _desc_inv.strip(),
                "Clase": "Activo_Financiero",
                "Moneda": "CLP",
                "Capa_Activacion": 3,
                "Saldo_Actual": float(_saldo_inv),
                "Aporte_Mensual": float(_aporte_inv),
                "Tasa_Anual": _tasa_inv / 100,
                "Tasa_Anual_Pct": _tasa_inv,
                "Horizonte_Meses": _horiz_inv_meses,
                "Fecha_Inicio": date.today().isoformat(),
            }
            try:
                _tabla_inv = schedule.gen_fondo_inversion(
                    saldo=float(_saldo_inv),
                    aporte_mensual=float(_aporte_inv),
                    tasa_anual=_tasa_inv / 100,
                    horizonte_meses=_horiz_inv_meses,
                    fecha_inicio=date.today(),
                    moneda="CLP",
                    id_posicion=_id_final_inv,
                )
                state.set_position(_id_final_inv, _params_inv)
                st.session_state.setdefault("schedules", {})[_id_final_inv] = _tabla_inv
                _act_list_inv: list = st.session_state.setdefault("activos_con_tabla", [])
                if _id_final_inv not in _act_list_inv:
                    _act_list_inv.append(_id_final_inv)
                state.update_layer()
                state.mark_dirty()
                st.session_state["c3_show_inv_form"] = False
                st.session_state.pop("c3_edit_inv_id", None)
                st.rerun()
            except Exception as _exc_inv:
                st.error(f"Error al generar proyección: {_exc_inv}")

    # ── Objetivos de ahorro ───────────────────────────────────────────────────
    st.divider()
    st.markdown("### 🎯 Mis objetivos de ahorro")
    objetivo_ids = _all_objetivo_ids()

    if not st.session_state.get("c3_show_obj_form", False):
        if st.button("➕ Agregar objetivo", use_container_width=True, key="btn_add_obj"):
            for _k in [
                "c3_obj_nombre", "c3_obj_meta", "c3_obj_moneda",
                "c3_obj_plazo", "c3_obj_saldo", "c3_obj_tasa",
            ]:
                st.session_state.pop(_k, None)
            st.session_state["c3_show_obj_form"] = True
            st.session_state.pop("c3_edit_obj_id", None)
            st.rerun()

    if objetivo_ids:
        for _obj_id in objetivo_ids:
            _obj_p = _pos(_obj_id)
            _obj_meta = float(_obj_p.get("Meta", 0))
            _obj_saldo = float(_obj_p.get("Saldo_Actual", 0))
            _obj_moneda = _obj_p.get("Moneda", "CLP")
            _obj_plazo = int(_obj_p.get("Plazo_Meses", 12))
            _obj_aporte = float(_obj_p.get("Aporte_Requerido", 0))
            _progreso_pct = min(100.0, (_obj_saldo / _obj_meta * 100) if _obj_meta > 0 else 0)
            from dateutil.relativedelta import relativedelta  # noqa: PLC0415
            _fecha_cumpl = (date.today() + relativedelta(months=_obj_plazo)).strftime("%b %Y")
            _obj_meta_clp = _clp(_obj_meta, _obj_moneda)
            _obj_aporte_clp = _clp(_obj_aporte, _obj_moneda)
            c_o1, c_o2, c_o3 = st.columns([5, 1, 1])
            with c_o1:
                _meta_str = (
                    f"{_obj_moneda} {_obj_meta:,.2f} → $ {int(_obj_meta_clp):,}"
                    if _obj_moneda != "CLP"
                    else f"$ {int(_obj_meta_clp):,}"
                )
                st.markdown(
                    f"**{_obj_p.get('Nombre', _obj_id)}**  \n"
                    f"Meta {_meta_str} · {_obj_plazo} meses · "
                    f"Cumplimiento est. **{_fecha_cumpl}**"
                )
                st.progress(
                    int(_progreso_pct),
                    text=f"{_progreso_pct:.1f}% completado · "
                         f"Aporte req. **$ {int(_obj_aporte_clp):,}/mes**",
                )
            with c_o2:
                if st.button("✏️", key=f"edit_obj_{_obj_id}", help="Editar"):
                    for _k in [
                        "c3_obj_nombre", "c3_obj_meta", "c3_obj_moneda",
                        "c3_obj_plazo", "c3_obj_saldo", "c3_obj_tasa",
                    ]:
                        st.session_state.pop(_k, None)
                    st.session_state["c3_show_obj_form"] = True
                    st.session_state["c3_edit_obj_id"] = _obj_id
                    st.rerun()
            with c_o3:
                if st.button("🗑️", key=f"del_obj_{_obj_id}", help="Eliminar"):
                    state.delete_position(_obj_id)
                    st.session_state.get("schedules", {}).pop(_obj_id, None)
                    _obj_lista: list = st.session_state.setdefault("objetivos_activos", [])
                    if _obj_id in _obj_lista:
                        _obj_lista.remove(_obj_id)
                    state.update_layer()
                    state.mark_dirty()
                    st.rerun()
    elif not st.session_state.get("c3_show_obj_form", False):
        st.info(
            "Sin objetivos registrados. "
            "Agrega metas como 'Viaje Europa', 'Auto', 'Fondo de emergencia', etc."
        )

    # Formulario de objetivo de ahorro
    if st.session_state.get("c3_show_obj_form", False):
        _edit_obj_id: str | None = st.session_state.get("c3_edit_obj_id")
        _edit_obj_p = state.get_position(_edit_obj_id) or {} if _edit_obj_id else {}
        _modo_obj = "✏️ Editar objetivo" if _edit_obj_id else "➕ Nuevo objetivo de ahorro"

        with st.form("form_objetivo"):
            st.markdown(f"**{_modo_obj}**")
            _nombre_obj = st.text_input(
                "Nombre del objetivo",
                value=_edit_obj_p.get("Nombre", ""),
                placeholder="Ej: Viaje Europa, Fondo emergencia, Auto…",
                key="c3_obj_nombre",
            )
            _col_fo1, _col_fo2 = st.columns(2)
            with _col_fo1:
                _moneda_obj = st.selectbox(
                    "Moneda",
                    ["CLP", "UF", "USD"],
                    index=["CLP", "UF", "USD"].index(_edit_obj_p.get("Moneda", "CLP")),
                    key="c3_obj_moneda",
                )
                _step_o = 1.0 if _moneda_obj in ("UF", "USD") else 100_000
                _meta_obj = st.number_input(
                    f"Monto meta ({_moneda_obj})",
                    min_value=0.0,
                    value=float(_edit_obj_p.get("Meta", 0)),
                    step=float(_step_o),
                    key="c3_obj_meta",
                )
                _saldo_obj = st.number_input(
                    f"Saldo actual asignado ({_moneda_obj})",
                    min_value=0.0,
                    value=float(_edit_obj_p.get("Saldo_Actual", 0)),
                    step=float(_step_o),
                    key="c3_obj_saldo",
                    help="Cuánto ya tienes ahorrado para este objetivo.",
                )
            with _col_fo2:
                _plazo_obj = st.number_input(
                    "Plazo (meses)",
                    min_value=1,
                    max_value=600,
                    value=int(_edit_obj_p.get("Plazo_Meses", 24)),
                    step=1,
                    key="c3_obj_plazo",
                )
                _tasa_obj = st.number_input(
                    "Tasa esperada anual (%)",
                    min_value=0.0,
                    max_value=50.0,
                    value=float(_edit_obj_p.get("Tasa_Anual_Pct", 4.0)),
                    step=0.5,
                    format="%.1f",
                    key="c3_obj_tasa",
                )
            # Aporte en tiempo real
            if _meta_obj > 0 and _plazo_obj > 0:
                try:
                    _aporte_preview = schedule.calcular_aporte_requerido(
                        meta=float(_meta_obj),
                        plazo_meses=int(_plazo_obj),
                        saldo_actual=float(_saldo_obj),
                        tasa_anual=float(_tasa_obj) / 100,
                    )
                    _aporte_clp_preview = _clp(_aporte_preview, _moneda_obj)
                    st.info(
                        f"💡 Aporte mensual requerido: "
                        f"**{_moneda_obj} {_aporte_preview:,.0f}**"
                        + (
                            f" ≈ **$ {int(_aporte_clp_preview):,}**"
                            if _moneda_obj != "CLP"
                            else ""
                        )
                    )
                except Exception:
                    pass
            _col_ok_o, _col_can_o = st.columns(2)
            with _col_ok_o:
                _obj_ok = st.form_submit_button("✓ Confirmar", type="primary", use_container_width=True)
            with _col_can_o:
                _obj_cancel = st.form_submit_button("✕ Cancelar", use_container_width=True)

        if _obj_cancel:
            st.session_state["c3_show_obj_form"] = False
            st.session_state.pop("c3_edit_obj_id", None)
            st.rerun()

        if _obj_ok:
            if not _nombre_obj.strip():
                st.error("El nombre del objetivo no puede estar vacío.")
                st.stop()
            if _meta_obj <= 0:
                st.error("El monto meta debe ser mayor que cero.")
                st.stop()
            _id_final_obj = _edit_obj_id if _edit_obj_id else _next_id_objetivo(_nombre_obj)
            try:
                _aporte_req = schedule.calcular_aporte_requerido(
                    meta=float(_meta_obj),
                    plazo_meses=int(_plazo_obj),
                    saldo_actual=float(_saldo_obj),
                    tasa_anual=float(_tasa_obj) / 100,
                )
                _tabla_obj = schedule.gen_objetivo_ahorro(
                    meta=float(_meta_obj),
                    plazo_meses=int(_plazo_obj),
                    saldo_actual=float(_saldo_obj),
                    tasa_anual=float(_tasa_obj) / 100,
                    fecha_inicio=date.today(),
                    moneda=_moneda_obj,
                    id_posicion=_id_final_obj,
                )
                _params_obj: dict = {
                    "Nombre": _nombre_obj.strip(),
                    "Clase": "Objetivo_Ahorro",
                    "Moneda": _moneda_obj,
                    "Capa_Activacion": 3,
                    "Meta": float(_meta_obj),
                    "Plazo_Meses": int(_plazo_obj),
                    "Saldo_Actual": float(_saldo_obj),
                    "Tasa_Anual": float(_tasa_obj) / 100,
                    "Tasa_Anual_Pct": float(_tasa_obj),
                    "Aporte_Requerido": _aporte_req,
                    "Fecha_Inicio": date.today().isoformat(),
                }
                state.set_position(_id_final_obj, _params_obj)
                st.session_state.setdefault("schedules", {})[_id_final_obj] = _tabla_obj
                _obj_activos: list = st.session_state.setdefault("objetivos_activos", [])
                if _id_final_obj not in _obj_activos:
                    _obj_activos.append(_id_final_obj)
                state.update_layer()
                state.mark_dirty()
                st.session_state["c3_show_obj_form"] = False
                st.session_state.pop("c3_edit_obj_id", None)
                st.rerun()
            except Exception as _exc:
                st.error(f"Error al generar tabla de objetivo: {_exc}")

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
