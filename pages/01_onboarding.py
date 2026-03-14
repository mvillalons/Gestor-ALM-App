"""pages/01_onboarding.py — Flujo de primera sesión en 5 pasos.

Solo se muestra si ``st.session_state["onboarding_complete"] == False``.
Al completar, persiste las posiciones en session_state (sin escribir a Drive)
y redirige al dashboard principal.

Pasos:
    1. Bienvenida — nombre, moneda, explicación de capas
    2. Ingreso — monto fijo o rango variable
    3. Gastos — tres buckets + pie chart en tiempo real
    4. Colchón — saldo líquido + meta de meses + gauge de progreso
    5. Resumen — 3 números clave + botón "Ver mi dashboard"
"""

from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

from core import calculator, state

# ── Inicialización ─────────────────────────────────────────────────────────────
# set_page_config() se llama una sola vez en app.py (entrada principal).
# El layout centrado se inyecta vía CSS desde app.py cuando el onboarding está activo.
state.init_session_state()

# ── Inicializar claves temporales del onboarding ──────────────────────────────
_OB_DEFAULTS: dict = {
    "onboarding_step": 1,
    "ob_nombre": "",
    "ob_moneda": "CLP",
    "ob_ingreso_variable": False,
    "ob_ingreso": 1_500_000,
    "ob_ingreso_min": 1_000_000,
    "ob_ingreso_max": 2_000_000,
    "ob_esenciales": 0,
    "ob_importantes": 0,
    "ob_aspiraciones": 0,
    "ob_liquido": 0,
    "ob_meses_meta": 3,
}
for _k, _v in _OB_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ingreso_efectivo() -> float:
    """Retorna el ingreso a usar en los cálculos (promedio si es variable)."""
    if st.session_state["ob_ingreso_variable"]:
        return (
            float(st.session_state["ob_ingreso_min"])
            + float(st.session_state["ob_ingreso_max"])
        ) / 2
    return float(st.session_state["ob_ingreso"])


def _fmt(v: float) -> str:
    """Formatea un monto en la moneda principal del usuario."""
    moneda = st.session_state.get("ob_moneda", "CLP")
    if moneda == "UF":
        return f"UF {v:,.2f}"
    if moneda == "USD":
        return f"USD {v:,.0f}"
    return f"$ {int(v):,}"


def _step_nav(current: int) -> None:
    """Renderiza la barra de progreso de pasos arriba de cada pantalla."""
    labels = ["Bienvenida", "Ingreso", "Gastos", "Colchón", "Resumen"]
    cols = st.columns(5)
    for i, (col, label) in enumerate(zip(cols, labels), start=1):
        with col:
            if i < current:
                st.markdown(
                    f"<div style='text-align:center;color:#22c55e;font-size:0.8rem'>✓ {label}</div>",
                    unsafe_allow_html=True,
                )
            elif i == current:
                st.markdown(
                    f"<div style='text-align:center;font-weight:700;font-size:0.8rem'>● {label}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='text-align:center;color:#9ca3af;font-size:0.8rem'>○ {label}</div>",
                    unsafe_allow_html=True,
                )
    st.divider()


def _go(step: int) -> None:
    """Navega al paso indicado y fuerza un rerun."""
    st.session_state["onboarding_step"] = step
    st.rerun()


def _step_val_for_currency() -> int:
    """Retorna el incremento del number_input según la moneda."""
    m = st.session_state.get("ob_moneda", "CLP")
    if m == "UF":
        return 1
    if m == "USD":
        return 100
    return 10_000


# ── PASO 1 — Bienvenida ───────────────────────────────────────────────────────

def _paso_1() -> None:
    _step_nav(1)
    st.title("Bienvenido a tu Gestor ALM")
    st.markdown(
        "Esta app es tu **calculadora financiera personal**. "
        "No es una planilla — tú ingresas parámetros y el motor calcula todo lo demás."
    )

    st.markdown("---")

    nombre = st.text_input(
        "¿Cómo te llamamos?",
        value=st.session_state["ob_nombre"],
        placeholder="Tu nombre o apodo",
    )
    moneda = st.selectbox(
        "Moneda principal",
        options=["CLP", "UF", "USD"],
        index=["CLP", "UF", "USD"].index(st.session_state["ob_moneda"]),
        help="Moneda en que expresarás tus ingresos y gastos principales.",
    )

    st.markdown("---")
    st.subheader("Tu plan en 4 capas")

    col1, col2 = st.columns(2)
    with col1:
        st.info(
            "**Capa 1 — Claridad** ✅  \n"
            "Ingresos, gastos y fondo de reserva. Empezamos aquí."
        )
        st.info(
            "**Capa 3 — Crecimiento** 📈  \n"
            "Portafolio de inversiones y objetivos de ahorro."
        )
    with col2:
        st.info(
            "**Capa 2 — Control** 🔒  \n"
            "Pasivos, deudas hipotecarias y saldo AFP."
        )
        st.info(
            "**Capa 4 — Pro** 🏆  \n"
            "ALM completo, stress testing y reportes."
        )

    st.caption("Cada capa se desbloquea automáticamente cuando completas la anterior.")

    if st.button("Empecemos →", type="primary", use_container_width=True):
        if not nombre.strip():
            st.error("Ingresa tu nombre para continuar.")
        else:
            st.session_state["ob_nombre"] = nombre.strip()
            st.session_state["ob_moneda"] = moneda
            _go(2)


# ── PASO 2 — Tu ingreso ───────────────────────────────────────────────────────

def _paso_2() -> None:
    _step_nav(2)
    nombre = st.session_state["ob_nombre"]
    st.title(f"¿Cuánto ganas al mes, {nombre}?")
    st.caption("Ingresa tu ingreso mensual **neto** (después de impuestos y descuentos).")

    step = _step_val_for_currency()

    variable = st.checkbox(
        "Mi ingreso es variable",
        value=st.session_state["ob_ingreso_variable"],
        help="Si tus ingresos cambian mes a mes, ingresa un rango y el motor usará el promedio.",
    )
    st.session_state["ob_ingreso_variable"] = variable

    ingreso_efectivo: float

    if variable:
        col1, col2 = st.columns(2)
        with col1:
            ing_min = st.number_input(
                "Mínimo mensual",
                min_value=0,
                value=int(st.session_state["ob_ingreso_min"]),
                step=step,
            )
        with col2:
            ing_max = st.number_input(
                "Máximo mensual",
                min_value=0,
                value=int(st.session_state["ob_ingreso_max"]),
                step=step,
            )
        if ing_max < ing_min:
            st.error("El máximo debe ser mayor que el mínimo.")
            ing_max = ing_min
        st.session_state["ob_ingreso_min"] = ing_min
        st.session_state["ob_ingreso_max"] = ing_max
        ingreso_efectivo = (ing_min + ing_max) / 2
        st.info(f"El motor usará el promedio: **{_fmt(ingreso_efectivo)} / mes**")
    else:
        ingreso = st.number_input(
            "Ingreso mensual neto",
            min_value=0,
            value=int(st.session_state["ob_ingreso"]),
            step=step,
        )
        st.session_state["ob_ingreso"] = ingreso
        ingreso_efectivo = float(ingreso)

    st.markdown("---")
    st.caption("**Distribución disponible**")
    st.progress(1.0, text=f"100% disponible — {_fmt(ingreso_efectivo)} para asignar")

    col_back, col_next = st.columns([1, 3])
    with col_back:
        if st.button("← Atrás"):
            _go(1)
    with col_next:
        if st.button("Continuar →", type="primary", use_container_width=True):
            if ingreso_efectivo <= 0:
                st.error("El ingreso debe ser mayor que cero.")
            else:
                _go(3)


# ── PASO 3 — Tus gastos ───────────────────────────────────────────────────────

def _paso_3() -> None:
    _step_nav(3)
    st.title("¿En qué gastas tu dinero?")
    st.caption(
        "Clasifica tus gastos en tres categorías. "
        "El resto es tu **margen libre** — para ahorrar e invertir."
    )

    step = _step_val_for_currency()
    ingreso = _ingreso_efectivo()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**Esenciales 🏠**")
        st.caption("Arriendo, servicios básicos, alimentación, salud")
        esenciales = st.number_input(
            "Esenciales",
            min_value=0,
            value=int(st.session_state["ob_esenciales"]),
            step=step,
            label_visibility="collapsed",
        )
    with col2:
        st.markdown("**Importantes 🎓**")
        st.caption("Colegio, transporte, celular, seguros")
        importantes = st.number_input(
            "Importantes",
            min_value=0,
            value=int(st.session_state["ob_importantes"]),
            step=step,
            label_visibility="collapsed",
        )
    with col3:
        st.markdown("**Aspiraciones ✈️**")
        st.caption("Viajes, restaurantes, ocio, ropa no esencial")
        aspiraciones = st.number_input(
            "Aspiraciones",
            min_value=0,
            value=int(st.session_state["ob_aspiraciones"]),
            step=step,
            label_visibility="collapsed",
        )

    # Persistir valores
    st.session_state["ob_esenciales"] = esenciales
    st.session_state["ob_importantes"] = importantes
    st.session_state["ob_aspiraciones"] = aspiraciones

    total_gastos = esenciales + importantes + aspiraciones
    margen = calculator.margen_libre(
        ingreso,
        float(esenciales),
        float(importantes),
        float(aspiraciones),
    )

    # Validación en tiempo real
    if total_gastos > ingreso:
        st.error(
            f"Tus gastos ({_fmt(total_gastos)}) superan tu ingreso ({_fmt(ingreso)}). "
            "Ajusta los valores."
        )

    # Pie chart en tiempo real
    libre = max(margen, 0.0)
    values = [float(esenciales), float(importantes), float(aspiraciones), libre]
    if sum(values) > 0:
        labels = ["Esenciales", "Importantes", "Aspiraciones", "Libre"]
        colors = ["#ef4444", "#f97316", "#a855f7", "#22c55e"]
        fig = go.Figure(
            go.Pie(
                labels=labels,
                values=values,
                marker_colors=colors,
                hole=0.42,
                textinfo="label+percent",
                hovertemplate="%{label}: %{value:,.0f}<extra></extra>",
            )
        )
        fig.update_layout(
            margin=dict(t=16, b=16, l=16, r=16),
            showlegend=False,
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)

    # Margen libre
    if margen >= 0:
        st.success(f"**Margen libre: {_fmt(margen)} / mes**")
    else:
        st.error(f"**Déficit: {_fmt(abs(margen))} / mes** — revisa tus gastos")

    col_back, col_next = st.columns([1, 3])
    with col_back:
        if st.button("← Atrás"):
            _go(2)
    with col_next:
        if st.button(
            "Continuar →",
            type="primary",
            use_container_width=True,
            disabled=(total_gastos > ingreso),
        ):
            _go(4)


# ── PASO 4 — Tu colchón ───────────────────────────────────────────────────────

def _paso_4() -> None:
    _step_nav(4)
    st.title("Tu fondo de reserva")
    st.caption(
        "El fondo de reserva es tu red de seguridad ante imprevistos. "
        "Recomendamos entre **3 y 6 meses** de gastos esenciales."
    )

    step = _step_val_for_currency()
    esenciales = float(st.session_state["ob_esenciales"])

    liquido = st.number_input(
        "Saldo líquido disponible hoy (cuenta corriente, ahorro a la vista, etc.)",
        min_value=0,
        value=int(st.session_state["ob_liquido"]),
        step=step,
    )
    meses_meta = st.slider(
        "Meta de meses de reserva",
        min_value=1,
        max_value=12,
        value=int(st.session_state["ob_meses_meta"]),
        step=1,
    )

    st.session_state["ob_liquido"] = liquido
    st.session_state["ob_meses_meta"] = meses_meta

    if esenciales > 0:
        meta = calculator.meta_fondo_reserva(esenciales, meses_meta)
        gap = calculator.gap_fondo(meta, float(liquido))
        pct = min(float(liquido) / meta, 1.0) if meta > 0 else 1.0
        margen = calculator.margen_libre(
            _ingreso_efectivo(),
            esenciales,
            float(st.session_state["ob_importantes"]),
            float(st.session_state["ob_aspiraciones"]),
        )
        meses_plazo = calculator.meses_para_fondo(gap, margen)

        # Gauge de progreso
        bar_color = "#22c55e" if pct >= 1.0 else "#3b82f6"
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=round(pct * 100, 1),
                number={"suffix": "%", "font": {"size": 44}},
                gauge={
                    "axis": {"range": [0, 100], "ticksuffix": "%"},
                    "bar": {"color": bar_color},
                    "steps": [
                        {"range": [0, 33], "color": "#fee2e2"},
                        {"range": [33, 66], "color": "#fef3c7"},
                        {"range": [66, 100], "color": "#dcfce7"},
                    ],
                    "threshold": {
                        "line": {"color": "#16a34a", "width": 4},
                        "thickness": 0.75,
                        "value": 100,
                    },
                },
                title={"text": f"Fondo de reserva · meta {_fmt(meta)}"},
            )
        )
        fig.update_layout(height=280, margin=dict(t=40, b=0, l=40, r=40))
        st.plotly_chart(fig, use_container_width=True)

        # Texto informativo
        if gap > 0:
            msg = f"Te faltan **{_fmt(gap)}** para tu colchón de {meses_meta} meses."
            if meses_plazo is not None:
                meses_str = "menos de 1 mes" if meses_plazo < 1 else f"**{meses_plazo:.0f} meses**"
                msg += f" A tu ritmo actual lo logras en {meses_str}."
            else:
                msg += " Con tu margen actual no es posible estimar el plazo."
            st.warning(msg)
        else:
            st.success(
                f"Tu fondo de reserva ya cubre {meses_meta} meses de gastos esenciales."
            )
    else:
        st.info("Define tus gastos esenciales en el paso anterior para ver el medidor.")

    col_back, col_next = st.columns([1, 3])
    with col_back:
        if st.button("← Atrás"):
            _go(3)
    with col_next:
        if st.button("Continuar →", type="primary", use_container_width=True):
            _go(5)


# ── PASO 5 — Primera vista ────────────────────────────────────────────────────

def _paso_5() -> None:
    _step_nav(5)

    nombre = st.session_state["ob_nombre"]
    moneda = st.session_state["ob_moneda"]
    ingreso = _ingreso_efectivo()
    esenciales = float(st.session_state["ob_esenciales"])
    importantes = float(st.session_state["ob_importantes"])
    aspiraciones = float(st.session_state["ob_aspiraciones"])
    liquido = float(st.session_state["ob_liquido"])
    meses_meta = int(st.session_state["ob_meses_meta"])

    st.title(f"Todo listo, {nombre}")
    st.markdown("Aquí tienes tu **primera fotografía financiera**.")

    # Resumen de lo ingresado
    with st.expander("Datos ingresados", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"- **Ingreso neto:** {_fmt(ingreso)}")
            st.markdown(f"- **Esenciales:** {_fmt(esenciales)}")
            st.markdown(f"- **Importantes:** {_fmt(importantes)}")
            st.markdown(f"- **Aspiraciones:** {_fmt(aspiraciones)}")
        with col2:
            st.markdown(f"- **Saldo líquido:** {_fmt(liquido)}")
            st.markdown(f"- **Meta fondo:** {meses_meta} meses")
            st.markdown(f"- **Moneda principal:** {moneda}")

    st.subheader("Tus 3 números clave")

    margen = calculator.margen_libre(ingreso, esenciales, importantes, aspiraciones)

    col1, col2, col3 = st.columns(3)

    with col1:
        if esenciales > 0:
            pv1 = calculator.posicion_vida_v1(liquido, esenciales)
            st.metric(
                "Posición de Vida",
                f"{pv1:.1f} meses",
                delta="≥ 3 recomendado",
                delta_color="normal" if pv1 >= 3 else "inverse",
                help="Cuántos meses de gastos esenciales cubre tu saldo líquido.",
            )
        else:
            st.metric("Posición de Vida", "—", help="Define esenciales en el paso anterior.")

    with col2:
        if esenciales > 0:
            meta = calculator.meta_fondo_reserva(esenciales, meses_meta)
            pct = min(liquido / meta * 100, 100.0) if meta > 0 else 100.0
            st.metric(
                "Fondo de Reserva",
                f"{pct:.0f}%",
                delta=f"Meta: {_fmt(meta)}",
                delta_color="off",
                help="Porcentaje completado de tu meta de fondo de reserva.",
            )
        else:
            st.metric("Fondo de Reserva", "—")

    with col3:
        st.metric(
            "Margen Libre",
            _fmt(margen),
            delta="/ mes",
            delta_color="normal" if margen >= 0 else "inverse",
            help="Ingreso menos todos los gastos. Disponible para ahorrar e invertir.",
        )

    st.divider()

    col_back, col_final = st.columns([1, 3])
    with col_back:
        if st.button("← Atrás"):
            _go(4)
    with col_final:
        if st.button(
            "Ver mi dashboard →",
            type="primary",
            use_container_width=True,
        ):
            _finalizar_onboarding()


# ── Persistencia al completar ─────────────────────────────────────────────────

def _finalizar_onboarding() -> None:
    """Persiste las posiciones en session_state y marca el onboarding completo.

    No escribe a Drive — eso lo hace el botón Guardar del dashboard.
    """
    ingreso = _ingreso_efectivo()
    moneda = st.session_state["ob_moneda"]
    variable = st.session_state["ob_ingreso_variable"]

    params_ingreso: dict = {
        "Descripcion": "Ingreso principal",
        "Clase": "Ingreso_Recurrente",
        "Moneda": moneda,
        "Capa_Activacion": 1,
        "Monto_Mensual": ingreso,
        "Variable": variable,
    }
    if variable:
        params_ingreso["Monto_Min"] = float(st.session_state["ob_ingreso_min"])
        params_ingreso["Monto_Max"] = float(st.session_state["ob_ingreso_max"])

    state.set_position("ING_PRINCIPAL", params_ingreso)

    state.set_position(
        "GAS_ESE_BUCKET",
        {
            "Descripcion": "Gastos esenciales",
            "Clase": "Gasto_Esencial",
            "Moneda": moneda,
            "Capa_Activacion": 1,
            "Monto_Mensual": float(st.session_state["ob_esenciales"]),
        },
    )
    state.set_position(
        "GAS_IMP_BUCKET",
        {
            "Descripcion": "Gastos importantes",
            "Clase": "Gasto_Importante",
            "Moneda": moneda,
            "Capa_Activacion": 1,
            "Monto_Mensual": float(st.session_state["ob_importantes"]),
        },
    )
    state.set_position(
        "GAS_ASP_BUCKET",
        {
            "Descripcion": "Gastos de aspiración",
            "Clase": "Gasto_Aspiracion",
            "Moneda": moneda,
            "Capa_Activacion": 1,
            "Monto_Mensual": float(st.session_state["ob_aspiraciones"]),
        },
    )
    state.set_position(
        "ACT_LIQUIDO_PRINCIPAL",
        {
            "Descripcion": "Activo líquido principal",
            "Clase": "Activo_Liquido",
            "Moneda": moneda,
            "Capa_Activacion": 1,
            "Saldo_Actual": float(st.session_state["ob_liquido"]),
            "Meses_Meta_Fondo": int(st.session_state["ob_meses_meta"]),
        },
    )

    # Metadatos del usuario en session_state
    st.session_state["nombre_usuario"] = st.session_state["ob_nombre"]
    st.session_state["moneda_principal"] = moneda

    # Condiciones de desbloqueo de Capa 2
    st.session_state["meta_fondo_definida"] = True
    st.session_state["buckets_confirmados"] = True

    # Marcar onboarding completo y recalcular capa
    st.session_state["onboarding_complete"] = True
    state.update_layer()

    # st.navigation() en app.py verá onboarding_complete=True y mostrará el dashboard.
    st.rerun()


# ── Router ────────────────────────────────────────────────────────────────────

_STEPS = {
    1: _paso_1,
    2: _paso_2,
    3: _paso_3,
    4: _paso_4,
    5: _paso_5,
}

_STEPS[int(st.session_state["onboarding_step"])]()
