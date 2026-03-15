"""pages/02_capa1_claridad.py — Dashboard de Capa 1: Claridad.

Layout de dos columnas:
  - Izquierda: 3 tarjetas de métricas (Posición de Vida, Fondo de Reserva,
    Margen Libre) calculadas desde calculator.py.
  - Derecha: formulario de edición de parámetros (ingresos, gastos, liquidez).

Truco de renderizado:
    Las métricas se llenan con un ``st.empty()`` placeholder DESPUÉS de que los
    widgets del formulario entregan sus valores. Esto garantiza que las métricas
    siempre reflejan el valor que el usuario acaba de escribir —sin rerun extra.

Persistencia:
    Cada cambio válido llama a ``state.set_position()`` (que internamente invoca
    ``mark_dirty()``). El botón "Guardar en Drive" llama a
    ``drive.save_positions()``, que a su vez llama a ``state.mark_clean()``.
"""

from __future__ import annotations

import streamlit as st

from core import calculator, drive, state

# ── Configuración de página ───────────────────────────────────────────────────
# set_page_config() y el guard de onboarding se gestionan en app.py.
state.init_session_state()

# ── Helpers ───────────────────────────────────────────────────────────────────

_MONEDA: str = st.session_state.get("moneda_principal", "CLP")

# Etiquetas de visualización de buckets (para sugerencias y desglose)
_BUCKET_LABELS: dict[str, str] = {
    "GAS_ESE_BUCKET": "Esenciales",
    "GAS_IMP_BUCKET": "Importantes",
    "GAS_ASP_BUCKET": "Aspiraciones",
}


def _fmt(v: float) -> str:
    """Formatea un monto en la moneda principal del usuario."""
    if _MONEDA == "UF":
        return f"UF {v:,.2f}"
    if _MONEDA == "USD":
        return f"USD {v:,.0f}"
    return f"$ {int(v):,}"


def _numinput(label: str, value: float, key: str, **kwargs) -> float:
    """number_input adaptado a la moneda del usuario (tipo y paso correctos)."""
    if _MONEDA == "UF":
        return float(
            st.number_input(
                label,
                min_value=0.0,
                value=float(value),
                step=1.0,
                format="%.2f",
                key=key,
                **kwargs,
            )
        )
    _step = 100 if _MONEDA == "USD" else 10_000
    return float(
        st.number_input(
            label,
            min_value=0,
            value=int(value),
            step=_step,
            key=key,
            **kwargs,
        )
    )


def _pos(pid: str) -> dict:
    """Retorna los parámetros de una posición o {} si no existe."""
    return state.get_position(pid) or {}


def _aplicar_sugerencia(sug: dict) -> None:
    """Aplica una sugerencia: vincula la posición al bucket y ajusta si excede.

    Args:
        sug: Dict de sugerencia (de ``sugerencias_pendientes``).
    """
    id_pos = sug["id_posicion"]
    bucket_id = sug["bucket"]
    cuota_clp = float(sug["monto"])

    pos = state.get_position(id_pos)
    if pos is None:
        _sugs: list = st.session_state.get("sugerencias_pendientes", [])
        st.session_state["sugerencias_pendientes"] = [
            s for s in _sugs if s["id"] != sug["id"]
        ]
        return

    # Si la cuota excede el espacio disponible, incrementar el bucket
    if sug.get("excede_espacio", False):
        _bucket_pos = state.get_position(bucket_id) or {}
        _exceso = float(sug.get("exceso_clp", 0.0))
        _nuevo_monto = float(_bucket_pos.get("Monto_Mensual", 0.0)) + _exceso
        state.set_position(bucket_id, {**_bucket_pos, "Monto_Mensual": _nuevo_monto})

    # Vincular la posición al bucket con la cuota calculada
    state.set_position(id_pos, {
        **pos,
        "bucket_vinculado": bucket_id,
        "Cuota_Vinculada_CLP": cuota_clp,
    })

    # Remover la sugerencia de la lista
    _sugs_after: list = st.session_state.get("sugerencias_pendientes", [])
    st.session_state["sugerencias_pendientes"] = [
        s for s in _sugs_after if s["id"] != sug["id"]
    ]
    state.mark_dirty()


def _bucket_desglose_md(bucket_id: str, monto: float) -> str:
    """Genera texto markdown de desglose de un bucket con sus posiciones vinculadas.

    Args:
        bucket_id: ID del bucket (``"GAS_ESE_BUCKET"`` etc.).
        monto: Monto total del bucket en la moneda principal.

    Returns:
        String markdown con el desglose (vinculado + resto) o el monto simple.
    """
    positions = st.session_state.get("positions", {})
    vinculadas = [
        (pid, p)
        for pid, p in positions.items()
        if p.get("bucket_vinculado") == bucket_id
    ]
    if not vinculadas:
        return _fmt(monto)

    label = _BUCKET_LABELS.get(bucket_id, bucket_id)
    lines = [f"**{label}: {_fmt(monto)}**"]
    total_vinculado = 0.0
    for pid, p in vinculadas:
        cuota = float(p.get("Cuota_Vinculada_CLP", 0))
        desc = p.get("Descripcion", pid)
        lines.append(f"&nbsp;&nbsp;└ {desc} *(calculado)*: $ {int(cuota):,}")
        total_vinculado += cuota
    resto = monto - total_vinculado
    lines.append(f"&nbsp;&nbsp;└ Resto estimado: {_fmt(max(0.0, resto))}")
    return "  \n".join(lines)


@st.cache_resource
def _drive_service():
    """Recurso de Drive autenticado y cacheado entre reruns."""
    return drive.authenticate_drive()


# ── Header ────────────────────────────────────────────────────────────────────

nombre = st.session_state.get("nombre_usuario", "")
col_h1, col_h2 = st.columns([5, 1])
with col_h1:
    extra = f" · Hola, {nombre} 👋" if nombre else ""
    st.title(f"Capa 1 — Claridad{extra}")
with col_h2:
    lbl = state.status_label()
    if "Sincronizado" in lbl:
        st.success(lbl)
    else:
        st.warning(lbl)

st.divider()

# ── Dos columnas ──────────────────────────────────────────────────────────────

col_left, col_right = st.columns([5, 7], gap="large")

# Placeholder en columna izquierda — se llena después de capturar los inputs
with col_left:
    _metrics_ph = st.empty()

# ────────────────────────────────────────────────────────────────────────────
# COLUMNA DERECHA — Edición de parámetros
# ────────────────────────────────────────────────────────────────────────────

with col_right:

    # ─── Mis ingresos ─────────────────────────────────────────────────────────
    st.markdown("#### 💰 Mis ingresos")

    pos_ing = _pos("ING_PRINCIPAL")
    stored_ing = float(pos_ing.get("Monto_Mensual", 1_500_000))

    live_ing = _numinput(
        "Ingreso mensual neto",
        stored_ing,
        "ci_ingreso",
        help="Ingreso después de impuestos y descuentos de ley.",
    )

    if abs(live_ing - stored_ing) > 0.01:
        state.set_position("ING_PRINCIPAL", {**pos_ing, "Monto_Mensual": live_ing})

    st.divider()

    # ─── Mis gastos ───────────────────────────────────────────────────────────
    st.markdown("#### 🧾 Mis gastos")

    pos_ese = _pos("GAS_ESE_BUCKET")
    pos_imp = _pos("GAS_IMP_BUCKET")
    pos_asp = _pos("GAS_ASP_BUCKET")
    stored_ese = float(pos_ese.get("Monto_Mensual", 0))
    stored_imp = float(pos_imp.get("Monto_Mensual", 0))
    stored_asp = float(pos_asp.get("Monto_Mensual", 0))

    ce1, ce2, ce3 = st.columns(3)
    with ce1:
        st.caption("🏠 Esenciales")
        live_ese = _numinput(
            "Esenciales", stored_ese, "ci_esenciales",
            label_visibility="collapsed",
        )
    with ce2:
        st.caption("🎓 Importantes")
        live_imp = _numinput(
            "Importantes", stored_imp, "ci_importantes",
            label_visibility="collapsed",
        )
    with ce3:
        st.caption("✈️ Aspiraciones")
        live_asp = _numinput(
            "Aspiraciones", stored_asp, "ci_aspiraciones",
            label_visibility="collapsed",
        )

    suma_gastos = live_ese + live_imp + live_asp
    gastos_ok = (suma_gastos <= live_ing) or (live_ing == 0.0)

    if not gastos_ok:
        st.error(
            f"Los gastos ({_fmt(suma_gastos)}) superan el ingreso ({_fmt(live_ing)}). "
            "Ajusta los valores — los cambios no se persisten hasta que sean válidos."
        )

    # Sync gastos → state solo si pasan la validación
    if gastos_ok:
        if abs(live_ese - stored_ese) > 0.01:
            state.set_position("GAS_ESE_BUCKET", {**pos_ese, "Monto_Mensual": live_ese})
        if abs(live_imp - stored_imp) > 0.01:
            state.set_position("GAS_IMP_BUCKET", {**pos_imp, "Monto_Mensual": live_imp})
        if abs(live_asp - stored_asp) > 0.01:
            state.set_position("GAS_ASP_BUCKET", {**pos_asp, "Monto_Mensual": live_asp})

    st.divider()

    # ─── Mi liquidez ──────────────────────────────────────────────────────────
    st.markdown("#### 🏦 Mi liquidez")

    pos_liq = _pos("ACT_LIQUIDO_PRINCIPAL")
    stored_liq = float(pos_liq.get("Saldo_Actual", 0))
    stored_meses = int(pos_liq.get("Meses_Meta_Fondo", 3))

    live_liq = _numinput(
        "Saldo líquido disponible",
        stored_liq,
        "ci_liquido",
        help="Cuenta corriente, ahorro a la vista, efectivo disponible hoy.",
    )
    live_meses = int(
        st.slider(
            "Meta de meses de reserva",
            min_value=1,
            max_value=12,
            value=stored_meses,
            key="ci_meses",
            help="Cuántos meses de gastos esenciales quieres tener como colchón.",
        )
    )

    if abs(live_liq - stored_liq) > 0.01 or live_meses != stored_meses:
        state.set_position(
            "ACT_LIQUIDO_PRINCIPAL",
            {**pos_liq, "Saldo_Actual": live_liq, "Meses_Meta_Fondo": live_meses},
        )

# ────────────────────────────────────────────────────────────────────────────
# MÉTRICAS — calculadas con valores live, renderizadas en el placeholder
# ────────────────────────────────────────────────────────────────────────────

_margen = calculator.margen_libre(live_ing, live_ese, live_imp, live_asp)

with _metrics_ph.container():

    # ── Tarjeta 1: Posición de Vida ───────────────────────────────────────────
    st.markdown("#### Posición de Vida")
    if live_ese > 0:
        pv1 = calculator.posicion_vida_v1(live_liq, live_ese)
        if pv1 >= 3:
            icon, d_color, d_text = "🟢", "normal", "Saludable — objetivo ≥ 3"
        elif pv1 >= 1:
            icon, d_color, d_text = "🟡", "off", "Precaución — entre 1 y 3 meses"
        else:
            icon, d_color, d_text = "🔴", "inverse", "Crítico — menos de 1 mes"
        st.metric(
            label=f"{icon} meses cubiertos",
            value=f"{pv1:.1f}",
            delta=d_text,
            delta_color=d_color,
        )
    else:
        st.metric(
            "Posición de Vida", "—",
            help="Define gastos esenciales para ver este indicador.",
        )
    st.caption("tus gastos esenciales cubiertos sin ingresos")

    st.divider()

    # ── Tarjeta 2: Fondo de Reserva ───────────────────────────────────────────
    st.markdown("#### Fondo de Reserva")
    if live_ese > 0:
        _meta = calculator.meta_fondo_reserva(live_ese, live_meses)
        _gap = calculator.gap_fondo(_meta, live_liq)
        _pct = min(live_liq / _meta, 1.0) if _meta > 0 else 1.0
        _plazo = calculator.meses_para_fondo(_gap, _margen)

        st.progress(_pct, text=f"{_pct * 100:.0f}%  ·  meta {_fmt(_meta)}")

        if _gap > 0:
            if _plazo is not None:
                m_str = "menos de 1 mes" if _plazo < 1 else f"{_plazo:.0f} meses"
                st.caption(f"Te faltan {_fmt(_gap)} — en {m_str} lo completas")
            else:
                st.caption(f"Te faltan {_fmt(_gap)} — revisa tu margen libre")
        else:
            st.success("✓ Colchón completo")
    else:
        st.progress(0.0, text="0% — define tus gastos esenciales")

    st.divider()

    # ── Tarjeta 3: Margen Libre ───────────────────────────────────────────────
    st.markdown("#### Margen Libre")
    _m_icon = "🟢" if _margen > 0 else "🔴"
    st.metric(
        label=f"{_m_icon} disponible / mes",
        value=_fmt(_margen),
        delta_color="normal" if _margen > 0 else "inverse",
    )

    # Mostrar desglose de buckets si alguno tiene posiciones vinculadas
    _positions_all = st.session_state.get("positions", {})
    _any_vinculado = any(p.get("bucket_vinculado") for p in _positions_all.values())
    if _any_vinculado:
        st.markdown(
            _bucket_desglose_md("GAS_ESE_BUCKET", live_ese) + "  \n" +
            _bucket_desglose_md("GAS_IMP_BUCKET", live_imp) + "  \n" +
            _bucket_desglose_md("GAS_ASP_BUCKET", live_asp),
            unsafe_allow_html=True,
        )
    else:
        st.caption(
            f"Esenciales {_fmt(live_ese)}  ·  "
            f"Importantes {_fmt(live_imp)}  ·  "
            f"Aspiraciones {_fmt(live_asp)}"
        )

# ── Sugerencias pendientes ────────────────────────────────────────────────────

_sugerencias_c1 = st.session_state.get("sugerencias_pendientes", [])
if _sugerencias_c1:
    st.divider()
    st.markdown(f"#### 💡 Sugerencias pendientes ({len(_sugerencias_c1)})")
    st.caption(
        "Estas sugerencias te ayudan a desglosar tus buckets de gasto "
        "con los compromisos reales que registraste en Capa 2."
    )
    for _sug_c1 in list(_sugerencias_c1):
        _sug_id_c1 = _sug_c1["id"]
        _bucket_lbl_c1 = _BUCKET_LABELS.get(_sug_c1["bucket"], _sug_c1["bucket"])
        _monto_clp_c1 = f"$ {int(_sug_c1['monto']):,}"
        with st.container():
            _col_sug_c1, _col_btns_c1 = st.columns([4, 2])
            with _col_sug_c1:
                st.markdown(
                    f"**{_sug_c1['descripcion']}** · *{_sug_c1['tipo']}*  \n"
                    f"Vincular cuota **{_monto_clp_c1}/mes** → {_bucket_lbl_c1}"
                )
                if _sug_c1.get("excede_espacio", False):
                    st.warning(
                        f"⚠️ Tu {_sug_c1['tipo']} real es "
                        f"**$ {int(_sug_c1.get('exceso_clp', 0)):,}** "
                        f"mayor que lo disponible en {_bucket_lbl_c1}. "
                        "Aplicar ajustará el bucket automáticamente."
                    )
            with _col_btns_c1:
                if st.button(
                    "✓ Aplicar",
                    key=f"sug1_ap_{_sug_id_c1}",
                    type="primary",
                    use_container_width=True,
                ):
                    _aplicar_sugerencia(_sug_c1)
                    st.rerun()
                if st.button(
                    "✕ Descartar",
                    key=f"sug1_dc_{_sug_id_c1}",
                    use_container_width=True,
                ):
                    st.session_state["sugerencias_pendientes"] = [
                        s for s in st.session_state.get("sugerencias_pendientes", [])
                        if s["id"] != _sug_id_c1
                    ]
                    st.rerun()

# ── Footer — Guardar + Banner Capa 2 ─────────────────────────────────────────

st.divider()
col_save, col_banner = st.columns([2, 3])

with col_save:
    _dirty = state.is_dirty()
    _btn_label = "💾 Guardar en Drive"
    _btn_help = "Guarda todos los cambios en Drive." if _dirty else "No hay cambios pendientes."

    if st.button(
        _btn_label,
        type="primary",
        disabled=not _dirty,
        use_container_width=True,
        help=_btn_help,
    ):
        with st.spinner("Guardando en Drive…"):
            try:
                _svc = _drive_service()
                _folders = drive.ensure_folder_structure(_svc)
                drive.save_positions(
                    _svc,
                    _folders,
                    st.session_state.get("positions", {}),
                )
                st.success("✓ Guardado en Drive")
                st.rerun()
            except Exception as exc:
                st.error(f"Error al guardar: {exc}")

with col_banner:
    if state.get_layer() >= 2:
        if st.button(
            "✓ Compromisos disponibles — ir a Capa 2 →",
            use_container_width=True,
        ):
            st.switch_page("pages/03_capa2_control.py")
