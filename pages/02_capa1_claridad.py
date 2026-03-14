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
st.set_page_config(
    page_title="Capa 1 — Claridad · Gestor ALM",
    page_icon="📊",
    layout="wide",
)

state.init_session_state()

if not st.session_state.get("onboarding_complete", False):
    st.switch_page("pages/01_onboarding.py")

# ── Helpers ───────────────────────────────────────────────────────────────────

_MONEDA: str = st.session_state.get("moneda_principal", "CLP")


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
    st.caption(
        f"Esenciales {_fmt(live_ese)}  ·  "
        f"Importantes {_fmt(live_imp)}  ·  "
        f"Aspiraciones {_fmt(live_asp)}"
    )

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
