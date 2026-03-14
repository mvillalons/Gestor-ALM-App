"""pages/03_capa2_control.py — Dashboard de Capa 2: Control.

Layout de dos columnas:
  - Izquierda: 4 métricas de Capa 2 (carga financiera, Posición de Vida v2,
    gráfico de flujo neto mensual, horizonte libre).
  - Derecha: registro de pasivos (formulario, lista con editar/eliminar)
    y sección AFP con proyección.

Patrón de renderizado:
    El placeholder de métricas se llena DESPUÉS de procesar el lado derecho,
    igual que en 02_capa1_claridad.py. Las métricas siempre reflejan el
    estado actual de session_state (schedules + positions).

Persistencia:
    Parámetros → state.set_position()  → positions en session_state
    Tablas     → st.session_state["schedules"][id]   (en memoria)
    Drive      → drive.save_positions() + drive.save_schedule() al guardar
    "pasivos_con_tabla" y "afp_saldo" se mantienen sincronizados para que
    calculator.capa_desbloqueada() pueda evaluar el desbloqueo de Capa 3.
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
import streamlit as st

from core import calculator, drive, schedule, state

# ── Inicialización ────────────────────────────────────────────────────────────
# set_page_config() se llama una sola vez en app.py.
state.init_session_state()

# ── Constantes de UI ──────────────────────────────────────────────────────────
_TIPOS_PASIVO = ["Hipotecario", "Crédito consumo", "Colegio", "Tarjeta", "Otro"]
_MESES_NOMBRE = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

# Metadatos por tipo de pasivo
_TIPO_INFO: dict[str, dict] = {
    "Hipotecario":     {"clase": "Pasivo_Estructural", "prefix": "PAS_HIP"},
    "Crédito consumo": {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_CON"},
    "Colegio":         {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_COL"},
    "Tarjeta":         {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_TAR"},
    "Otro":            {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_OTR"},
}

# Keys de formulario de pasivo (se limpian al abrir un nuevo form)
_FORM_KEYS: list[str] = [
    "c2_desc", "c2_moneda_pas",
    # Hipotecario
    "c2_capital", "c2_tasa_anual_hip", "c2_plazo_meses", "c2_fecha_hip", "c2_metodo",
    # Crédito consumo
    "c2_monto_con", "c2_n_cuotas_con", "c2_tasa_anual_con", "c2_fecha_con",
    # Colegio
    "c2_monto_anual", "c2_cuotas_ano", "c2_anos_rest", "c2_meses_pago", "c2_fecha_col",
    # Tarjeta
    "c2_deuda_total", "c2_pago_mensual", "c2_tasa_mensual_tar", "c2_fecha_tar",
    # Otro
    "c2_cuota_otro", "c2_n_cuotas_otro", "c2_fecha_otro",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

_MONEDA: str = st.session_state.get("moneda_principal", "CLP")


def _fmt(v: float) -> str:
    """Formatea un monto en la moneda principal del usuario."""
    if _MONEDA == "UF":
        return f"UF {v:,.2f}"
    if _MONEDA == "USD":
        return f"USD {v:,.0f}"
    return f"$ {int(v):,}"


def _pos(pid: str) -> dict:
    """Retorna los parámetros de una posición o {} si no existe."""
    return state.get_position(pid) or {}


def _all_pasivo_ids() -> list[str]:
    """Lista todos los IDs de pasivos (Estructural + Corto_Plazo)."""
    ids = state.list_positions(clase="Pasivo_Estructural")
    ids += state.list_positions(clase="Pasivo_Corto_Plazo")
    return ids


def _cuota_actual(id_pos: str) -> float:
    """Próxima cuota mensual de un pasivo (valor absoluto)."""
    tabla = st.session_state.get("schedules", {}).get(id_pos)
    if tabla is None or tabla.empty:
        return 0.0
    hoy = date.today().strftime("%Y-%m")
    futuro = tabla[tabla["Periodo"] >= hoy]
    if futuro.empty:
        return 0.0
    return abs(float(futuro["Flujo_Periodo"].iloc[0]))


def _saldo_actual_pasivo(id_pos: str) -> float:
    """Saldo actual del pasivo (primera fila futura)."""
    tabla = st.session_state.get("schedules", {}).get(id_pos)
    if tabla is None or tabla.empty:
        return 0.0
    hoy = date.today().strftime("%Y-%m")
    futuro = tabla[tabla["Periodo"] >= hoy]
    if futuro.empty:
        return 0.0
    return float(futuro["Saldo_Inicial"].iloc[0])


def _fecha_termino(id_pos: str) -> str:
    """Último período de la tabla de desarrollo de un pasivo."""
    tabla = st.session_state.get("schedules", {}).get(id_pos)
    if tabla is None or tabla.empty:
        return "—"
    return str(tabla["Periodo"].iloc[-1])


def _next_id(tipo: str) -> str:
    """Genera el próximo ID único para el tipo de pasivo dado."""
    info = _TIPO_INFO[tipo]
    prefix = info["prefix"]
    clase = info["clase"]
    existing = [
        pid for pid in state.list_positions(clase=clase)
        if pid.startswith(prefix + "_")
    ]
    nums: list[int] = []
    for pid in existing:
        try:
            nums.append(int(pid.split("_")[-1]))
        except ValueError:
            pass
    return f"{prefix}_{max(nums, default=0) + 1:03d}"


@st.cache_resource
def _drive_service():
    """Recurso de Drive autenticado y cacheado entre reruns."""
    return drive.authenticate_drive()


def _generar_tabla(tipo: str, params: dict, id_pos: str) -> "pd.DataFrame | None":
    """Genera la tabla de desarrollo según el tipo de pasivo.

    Retorna None si hay un error, mostrando el mensaje al usuario.
    """
    import pandas as pd  # noqa: PLC0415

    try:
        if tipo == "Hipotecario":
            return schedule.gen_hipotecario(
                capital=float(params["Capital"]),
                tasa_anual=float(params["Tasa_Anual"]),
                plazo_meses=int(params["Plazo_Meses"]),
                fecha_inicio=params["Fecha_Inicio"],
                moneda=str(params["Moneda"]),
                metodo=str(params.get("Metodo", "frances")),
                id_posicion=id_pos,
            )
        if tipo == "Crédito consumo":
            return schedule.gen_credito_consumo(
                monto=float(params["Monto"]),
                n_cuotas=int(params["N_Cuotas"]),
                tasa_anual=float(params["Tasa_Anual"]),
                fecha_primer_pago=params["Fecha_Inicio"],
                moneda=str(params["Moneda"]),
                id_posicion=id_pos,
            )
        if tipo == "Colegio":
            return schedule.gen_colegio(
                monto_anual=float(params["Monto_Anual"]),
                cuotas_por_ano=int(params["Cuotas_Por_Ano"]),
                anos_restantes=int(params["Anos_Restantes"]),
                meses_de_pago=list(params["Meses_De_Pago"]),
                fecha_inicio=params["Fecha_Inicio"],
                moneda=str(params["Moneda"]),
                id_posicion=id_pos,
            )
        if tipo == "Tarjeta":
            return schedule.gen_tarjeta(
                deuda_total=float(params["Deuda_Total"]),
                pago_mensual=float(params["Pago_Mensual"]),
                tasa_mensual=float(params["Tasa_Mensual"]),
                fecha_inicio=params["Fecha_Inicio"],
                moneda=str(params["Moneda"]),
                id_posicion=id_pos,
            )
        # Otro → crédito sin interés con cuota fija
        return schedule.gen_credito_consumo(
            monto=float(params["Cuota_Mensual"]) * int(params["N_Cuotas"]),
            n_cuotas=int(params["N_Cuotas"]),
            tasa_anual=0.0,
            fecha_primer_pago=params["Fecha_Inicio"],
            moneda=str(params["Moneda"]),
            id_posicion=id_pos,
        )
    except Exception as exc:
        st.error(f"Error al generar tabla para «{tipo}»: {exc}")
        return None


def _registrar_pasivo(id_pos: str, params: dict, tabla) -> None:
    """Guarda parámetros y tabla en session_state; actualiza pasivos_con_tabla."""
    state.set_position(id_pos, params)
    st.session_state.setdefault("schedules", {})[id_pos] = tabla
    pac: list = st.session_state.setdefault("pasivos_con_tabla", [])
    if id_pos not in pac:
        pac.append(id_pos)
    state.update_layer()


def _eliminar_pasivo(id_pos: str) -> None:
    """Elimina un pasivo de positions, schedules y pasivos_con_tabla."""
    state.delete_position(id_pos)
    st.session_state.get("schedules", {}).pop(id_pos, None)
    pac: list = st.session_state.get("pasivos_con_tabla", [])
    if id_pos in pac:
        pac.remove(id_pos)
    state.update_layer()


def _abrir_formulario_pasivo(edit_id: str | None = None) -> None:
    """Limpia keys de formulario y abre el form, opcionalmente en modo edición."""
    for k in _FORM_KEYS:
        st.session_state.pop(k, None)
    st.session_state["c2_show_add_form"] = True
    st.session_state["c2_edit_id"] = edit_id
    if edit_id:
        params = state.get_position(edit_id) or {}
        st.session_state["c2_tipo_nuevo"] = params.get("Tipo", "Hipotecario")


# ── Header ────────────────────────────────────────────────────────────────────

col_h1, col_h2 = st.columns([5, 1])
with col_h1:
    nombre = st.session_state.get("nombre_usuario", "")
    extra = f" · Hola, {nombre} 👋" if nombre else ""
    st.title(f"Capa 2 — Control{extra}")
with col_h2:
    lbl = state.status_label()
    if "Sincronizado" in lbl:
        st.success(lbl)
    else:
        st.warning(lbl)

st.divider()

# ── Dos columnas ──────────────────────────────────────────────────────────────

col_left, col_right = st.columns([5, 7], gap="large")

# Placeholder en columna izquierda — se llena después de procesar la derecha
with col_left:
    _metrics_ph = st.empty()

# ────────────────────────────────────────────────────────────────────────────
# COLUMNA DERECHA — Registro de pasivos y AFP
# ────────────────────────────────────────────────────────────────────────────

with col_right:

    # ── Botón "Agregar pasivo" ────────────────────────────────────────────────
    st.markdown("#### 📋 Mis compromisos")

    if not st.session_state.get("c2_show_add_form", False):
        if st.button("➕ Agregar pasivo", use_container_width=True):
            _abrir_formulario_pasivo()
            st.rerun()

    # ── Formulario de pasivo (agregar / editar) ───────────────────────────────
    if st.session_state.get("c2_show_add_form", False):
        edit_id: str | None = st.session_state.get("c2_edit_id")
        editing_params = state.get_position(edit_id) or {} if edit_id else {}
        modo_label = "✏️ Editar pasivo" if edit_id else "➕ Nuevo pasivo"
        st.markdown(f"**{modo_label}**")

        # Selectbox FUERA del form — para recargar campos según tipo
        tipo_default = editing_params.get("Tipo", "Hipotecario")
        tipo_idx = _TIPOS_PASIVO.index(tipo_default) if tipo_default in _TIPOS_PASIVO else 0
        tipo = st.selectbox(
            "Tipo de compromiso",
            _TIPOS_PASIVO,
            index=tipo_idx,
            key="c2_tipo_nuevo",
        )

        with st.form("form_pasivo", clear_on_submit=False):
            descripcion = st.text_input(
                "Descripción",
                value=editing_params.get("Descripcion", ""),
                key="c2_desc",
                placeholder="Ej: Hipoteca departamento, Colegio hijo mayor…",
            )
            moneda_pas = st.selectbox(
                "Moneda",
                ["CLP", "UF", "USD"],
                index=["CLP", "UF", "USD"].index(
                    editing_params.get("Moneda", _MONEDA)
                    if editing_params.get("Moneda", _MONEDA) in ["CLP", "UF", "USD"]
                    else "CLP"
                ),
                key="c2_moneda_pas",
            )

            # ── Campos específicos por tipo ──────────────────────────────────
            if tipo == "Hipotecario":
                _step = 1.0 if moneda_pas == "UF" else 1_000_000
                _fmt_num = "%.2f" if moneda_pas == "UF" else "%d"
                _def_cap = editing_params.get("Capital", 2000.0 if moneda_pas == "UF" else 50_000_000)
                capital = st.number_input(
                    f"Capital ({moneda_pas})",
                    min_value=0.0, value=float(_def_cap), step=float(_step),
                    format=_fmt_num, key="c2_capital",
                )
                tasa_hip = st.number_input(
                    "Tasa anual (%)",
                    min_value=0.0, max_value=30.0,
                    value=float(editing_params.get("Tasa_Anual_Pct", 4.5)),
                    step=0.1, format="%.2f", key="c2_tasa_anual_hip",
                    help="Tasa de interés anual. Ej: 4.5 para 4,5 %",
                )
                plazo_meses = int(st.number_input(
                    "Plazo (meses)",
                    min_value=1, max_value=600,
                    value=int(editing_params.get("Plazo_Meses", 240)),
                    step=12, key="c2_plazo_meses",
                ))
                fecha_hip = st.date_input(
                    "Primera cuota",
                    value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                    key="c2_fecha_hip",
                )
                metodo = st.selectbox(
                    "Método de amortización",
                    ["frances", "aleman"],
                    index=0 if editing_params.get("Metodo", "frances") == "frances" else 1,
                    format_func=lambda x: "Francés (cuota fija)" if x == "frances" else "Alemán (amort. constante)",
                    key="c2_metodo",
                )

            elif tipo == "Crédito consumo":
                _step_c = 1.0 if moneda_pas == "UF" else 100_000
                _def_m = editing_params.get("Monto", 5_000_000)
                monto_con = st.number_input(
                    f"Monto del crédito ({moneda_pas})",
                    min_value=0.0, value=float(_def_m),
                    step=float(_step_c), key="c2_monto_con",
                )
                n_cuotas_con = int(st.number_input(
                    "N° de cuotas",
                    min_value=1, max_value=360,
                    value=int(editing_params.get("N_Cuotas", 24)),
                    step=1, key="c2_n_cuotas_con",
                ))
                tasa_con = st.number_input(
                    "Tasa anual (%)",
                    min_value=0.0, max_value=80.0,
                    value=float(editing_params.get("Tasa_Anual_Pct", 12.0)),
                    step=0.5, format="%.2f", key="c2_tasa_anual_con",
                    help="Tasa anual del crédito. Ej: 12.0 para 12 %",
                )
                fecha_con = st.date_input(
                    "Primera cuota",
                    value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                    key="c2_fecha_con",
                )

            elif tipo == "Colegio":
                _step_ma = 1.0 if moneda_pas == "UF" else 100_000
                monto_anual = st.number_input(
                    f"Monto anual ({moneda_pas})",
                    min_value=0.0,
                    value=float(editing_params.get("Monto_Anual", 3_000_000)),
                    step=float(_step_ma), key="c2_monto_anual",
                )
                cuotas_ano = int(st.number_input(
                    "Cuotas por año",
                    min_value=1, max_value=12,
                    value=int(editing_params.get("Cuotas_Por_Ano", 10)),
                    step=1, key="c2_cuotas_ano",
                ))
                anos_rest = int(st.number_input(
                    "Años restantes",
                    min_value=1, max_value=20,
                    value=int(editing_params.get("Anos_Restantes", 3)),
                    step=1, key="c2_anos_rest",
                ))
                meses_defecto = list(editing_params.get("Meses_De_Pago", [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))
                meses_pago = st.multiselect(
                    "Meses de pago",
                    options=list(range(1, 13)),
                    default=meses_defecto,
                    format_func=lambda m: _MESES_NOMBRE[m],
                    key="c2_meses_pago",
                    help="Meses del año en que se realizan los pagos.",
                )
                fecha_col = st.date_input(
                    "Fecha de inicio",
                    value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                    key="c2_fecha_col",
                )

            elif tipo == "Tarjeta":
                _step_t = 1.0 if moneda_pas == "UF" else 10_000
                deuda_total = st.number_input(
                    f"Deuda total ({moneda_pas})",
                    min_value=0.0,
                    value=float(editing_params.get("Deuda_Total", 1_000_000)),
                    step=float(_step_t), key="c2_deuda_total",
                )
                pago_mensual = st.number_input(
                    f"Pago mensual ({moneda_pas})",
                    min_value=0.0,
                    value=float(editing_params.get("Pago_Mensual", 100_000)),
                    step=float(_step_t), key="c2_pago_mensual",
                )
                tasa_tar = st.number_input(
                    "Tasa mensual (%)",
                    min_value=0.0, max_value=10.0,
                    value=float(editing_params.get("Tasa_Mensual_Pct", 2.0)),
                    step=0.1, format="%.2f", key="c2_tasa_mensual_tar",
                    help="Tasa mensual de la tarjeta. Ej: 2.0 para 2 % mensual",
                )
                fecha_tar = st.date_input(
                    "Fecha primer pago",
                    value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                    key="c2_fecha_tar",
                )

            else:  # Otro
                _step_o = 1.0 if moneda_pas == "UF" else 10_000
                cuota_otro = st.number_input(
                    f"Cuota mensual ({moneda_pas})",
                    min_value=0.0,
                    value=float(editing_params.get("Cuota_Mensual", 100_000)),
                    step=float(_step_o), key="c2_cuota_otro",
                )
                n_cuotas_otro = int(st.number_input(
                    "N° de cuotas restantes",
                    min_value=1, max_value=600,
                    value=int(editing_params.get("N_Cuotas", 12)),
                    step=1, key="c2_n_cuotas_otro",
                ))
                fecha_otro = st.date_input(
                    "Primera cuota",
                    value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                    key="c2_fecha_otro",
                )

            # Botones del form
            col_ok, col_cancel = st.columns(2)
            with col_ok:
                confirmed = st.form_submit_button(
                    "✓ Confirmar", type="primary", use_container_width=True
                )
            with col_cancel:
                cancelled = st.form_submit_button(
                    "✕ Cancelar", use_container_width=True
                )

            # ── Cancelar ────────────────────────────────────────────────────
            if cancelled:
                st.session_state["c2_show_add_form"] = False
                st.session_state.pop("c2_edit_id", None)
                st.rerun()

            # ── Confirmar ───────────────────────────────────────────────────
            if confirmed:
                # Validaciones básicas de UI
                if not descripcion.strip():
                    st.error("La descripción no puede estar vacía.")
                    st.stop()

                # Construir dict de parámetros según tipo
                params_nuevos: dict = {
                    "Tipo": tipo,
                    "Descripcion": descripcion.strip(),
                    "Clase": _TIPO_INFO[tipo]["clase"],
                    "Moneda": moneda_pas,
                    "Capa_Activacion": 2,
                }

                if tipo == "Hipotecario":
                    if capital <= 0:
                        st.error("El capital debe ser mayor que cero.")
                        st.stop()
                    params_nuevos.update({
                        "Capital": capital,
                        "Tasa_Anual": tasa_hip / 100,
                        "Tasa_Anual_Pct": tasa_hip,
                        "Plazo_Meses": plazo_meses,
                        "Fecha_Inicio": fecha_hip.isoformat(),
                        "Metodo": metodo,
                    })
                elif tipo == "Crédito consumo":
                    if monto_con <= 0:
                        st.error("El monto debe ser mayor que cero.")
                        st.stop()
                    params_nuevos.update({
                        "Monto": monto_con,
                        "N_Cuotas": n_cuotas_con,
                        "Tasa_Anual": tasa_con / 100,
                        "Tasa_Anual_Pct": tasa_con,
                        "Fecha_Inicio": fecha_con.isoformat(),
                    })
                elif tipo == "Colegio":
                    if not meses_pago:
                        st.error("Selecciona al menos un mes de pago.")
                        st.stop()
                    if cuotas_ano > len(meses_pago):
                        st.error(
                            f"Las cuotas por año ({cuotas_ano}) no pueden superar "
                            f"los meses seleccionados ({len(meses_pago)})."
                        )
                        st.stop()
                    params_nuevos.update({
                        "Monto_Anual": monto_anual,
                        "Cuotas_Por_Ano": cuotas_ano,
                        "Anos_Restantes": anos_rest,
                        "Meses_De_Pago": sorted(meses_pago),
                        "Fecha_Inicio": fecha_col.isoformat(),
                    })
                elif tipo == "Tarjeta":
                    if deuda_total <= 0:
                        st.error("La deuda total debe ser mayor que cero.")
                        st.stop()
                    if pago_mensual <= 0:
                        st.error("El pago mensual debe ser mayor que cero.")
                        st.stop()
                    params_nuevos.update({
                        "Deuda_Total": deuda_total,
                        "Pago_Mensual": pago_mensual,
                        "Tasa_Mensual": tasa_tar / 100,
                        "Tasa_Mensual_Pct": tasa_tar,
                        "Fecha_Inicio": fecha_tar.isoformat(),
                    })
                else:  # Otro
                    if cuota_otro <= 0:
                        st.error("La cuota mensual debe ser mayor que cero.")
                        st.stop()
                    params_nuevos.update({
                        "Cuota_Mensual": cuota_otro,
                        "N_Cuotas": n_cuotas_otro,
                        "Fecha_Inicio": fecha_otro.isoformat(),
                    })

                # ID: preservar el existente en edición, generar nuevo al agregar
                id_final = edit_id if edit_id else _next_id(tipo)

                # Si es edición de un ID que cambia de tipo → eliminar el viejo
                if edit_id and edit_id != id_final:
                    _eliminar_pasivo(edit_id)

                # Generar tabla de desarrollo
                tabla = _generar_tabla(tipo, params_nuevos, id_final)
                if tabla is not None:
                    _registrar_pasivo(id_final, params_nuevos, tabla)
                    st.session_state["c2_show_add_form"] = False
                    st.session_state.pop("c2_edit_id", None)
                    st.success(f"✓ {'Actualizado' if edit_id else 'Agregado'}: {descripcion}")
                    st.rerun()

    # ── Lista de pasivos registrados ──────────────────────────────────────────
    pasivo_ids = _all_pasivo_ids()
    if pasivo_ids:
        st.divider()
        for pid in pasivo_ids:
            pparams = _pos(pid)
            tipo_p = pparams.get("Tipo", "Pasivo")
            desc_p = pparams.get("Descripcion", pid)
            cuota_p = _cuota_actual(pid)
            saldo_p = _saldo_actual_pasivo(pid)
            termino_p = _fecha_termino(pid)

            with st.container():
                c1, c2, c3 = st.columns([5, 1, 1])
                with c1:
                    st.markdown(
                        f"**{desc_p}** · *{tipo_p}*  \n"
                        f"Saldo {_fmt(saldo_p)} · Cuota {_fmt(cuota_p)}/mes · "
                        f"Término {termino_p}"
                    )
                with c2:
                    if st.button("✏️", key=f"edit_{pid}", help="Editar"):
                        _abrir_formulario_pasivo(edit_id=pid)
                        st.rerun()
                with c3:
                    if st.button("🗑️", key=f"del_{pid}", help="Eliminar"):
                        _eliminar_pasivo(pid)
                        state.mark_dirty()
                        st.rerun()
    elif not st.session_state.get("c2_show_add_form", False):
        st.info(
            "Aún no registraste ningún compromiso financiero. "
            "Usa el botón ➕ para agregar hipotecas, créditos, tarjetas, etc."
        )

    # ── Sección AFP ───────────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🏦 Previsión AFP")

    pos_afp = _pos("AFP_PRINCIPAL")
    afp_guardada = bool(pos_afp)
    c2_show_afp = st.session_state.get("c2_show_afp_form", not afp_guardada)

    if afp_guardada and not c2_show_afp:
        # Mostrar resumen AFP + botón editar
        afp_saldo_val = float(pos_afp.get("Saldo_Actual", 0))
        afp_aporte = float(pos_afp.get("Aporte_Mensual", 0))
        afp_edad_jub = float(pos_afp.get("Edad_Jubilacion", 65))
        tabla_afp = st.session_state.get("schedules", {}).get("AFP_PRINCIPAL")

        c_afp1, c_afp2 = st.columns([4, 1])
        with c_afp1:
            saldo_final_afp = (
                float(tabla_afp["Saldo_Final"].iloc[-1])
                if tabla_afp is not None and not tabla_afp.empty
                else afp_saldo_val
            )
            st.markdown(
                f"Saldo actual **{_fmt(afp_saldo_val)}** · "
                f"Aporte **{_fmt(afp_aporte)}/mes** · "
                f"Proyección a los {int(afp_edad_jub)} años: **{_fmt(saldo_final_afp)}**"
            )
        with c_afp2:
            if st.button("✏️ Editar AFP", use_container_width=True):
                st.session_state["c2_show_afp_form"] = True
                st.rerun()

        # Gráfico de proyección AFP
        if tabla_afp is not None and not tabla_afp.empty:
            fig_afp = go.Figure(
                go.Scatter(
                    x=tabla_afp["Periodo"],
                    y=tabla_afp["Saldo_Final"],
                    fill="tozeroy",
                    line=dict(color="#3498db", width=2),
                    fillcolor="rgba(52,152,219,0.15)",
                    hovertemplate="%{x}<br>" + _MONEDA + " %{y:,.0f}<extra></extra>",
                )
            )
            fig_afp.update_layout(
                height=180,
                margin=dict(l=0, r=0, t=8, b=0),
                showlegend=False,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
                xaxis=dict(
                    tickmode="array",
                    tickvals=tabla_afp["Periodo"].iloc[::max(1, len(tabla_afp) // 6)].tolist(),
                ),
            )
            st.plotly_chart(fig_afp, use_container_width=True, config={"displayModeBar": False})
    else:
        # Formulario AFP
        with st.form("form_afp"):
            st.caption("Ingresa los datos de tu AFP para proyectar el saldo hasta jubilación.")
            ca1, ca2 = st.columns(2)
            with ca1:
                afp_saldo_in = st.number_input(
                    "Saldo actual (CLP)",
                    min_value=0, step=1_000_000,
                    value=int(pos_afp.get("Saldo_Actual", 0)),
                    key="c2_afp_saldo",
                )
                afp_aporte_in = st.number_input(
                    "Aporte mensual (CLP)",
                    min_value=0, step=10_000,
                    value=int(pos_afp.get("Aporte_Mensual", 0)),
                    key="c2_afp_aporte",
                    help="Incluye aporte obligatorio + voluntario (APV).",
                )
            with ca2:
                afp_edad_in = st.number_input(
                    "Edad actual",
                    min_value=18, max_value=70,
                    value=int(pos_afp.get("Edad_Actual", 35)),
                    step=1, key="c2_afp_edad",
                )
                afp_jub_in = st.number_input(
                    "Edad de jubilación",
                    min_value=55, max_value=80,
                    value=int(pos_afp.get("Edad_Jubilacion", 65)),
                    step=1, key="c2_afp_jub",
                )
            afp_tasa_in = st.number_input(
                "Rentabilidad anual esperada (%)",
                min_value=0.0, max_value=15.0,
                value=float(pos_afp.get("Tasa_Anual_Pct", 5.0)),
                step=0.5, format="%.1f", key="c2_afp_tasa",
                help="Rentabilidad real del fondo AFP. Promedio histórico CLP: ~5 %",
            )

            c_ok, c_can = st.columns(2)
            with c_ok:
                afp_ok = st.form_submit_button(
                    "✓ Guardar AFP", type="primary", use_container_width=True
                )
            with c_can:
                afp_cancel = st.form_submit_button("✕ Cancelar", use_container_width=True)

            if afp_cancel:
                st.session_state["c2_show_afp_form"] = False
                st.rerun()

            if afp_ok:
                if afp_jub_in <= afp_edad_in:
                    st.error("La edad de jubilación debe ser mayor que la edad actual.")
                    st.stop()

                params_afp = {
                    "Tipo": "AFP",
                    "Clase": "Prevision_AFP",
                    "Descripcion": "Saldo AFP",
                    "Moneda": "CLP",
                    "Capa_Activacion": 2,
                    "Saldo_Actual": afp_saldo_in,
                    "Aporte_Mensual": afp_aporte_in,
                    "Tasa_Anual": afp_tasa_in / 100,
                    "Tasa_Anual_Pct": afp_tasa_in,
                    "Edad_Actual": afp_edad_in,
                    "Edad_Jubilacion": afp_jub_in,
                    "Fecha_Inicio": date.today().isoformat(),
                }
                try:
                    tabla_afp_nueva = schedule.gen_afp(
                        saldo_actual=float(afp_saldo_in),
                        aporte_mensual=float(afp_aporte_in),
                        tasa_anual=afp_tasa_in / 100,
                        edad_actual=float(afp_edad_in),
                        edad_jubilacion=float(afp_jub_in),
                        fecha_inicio=date.today(),
                        moneda="CLP",
                        id_posicion="AFP_PRINCIPAL",
                    )
                    state.set_position("AFP_PRINCIPAL", params_afp)
                    st.session_state.setdefault("schedules", {})["AFP_PRINCIPAL"] = tabla_afp_nueva
                    st.session_state["afp_saldo"] = float(afp_saldo_in)
                    state.update_layer()
                    st.session_state["c2_show_afp_form"] = False
                    st.rerun()
                except Exception as exc:
                    st.error(f"Error al generar proyección AFP: {exc}")

# ────────────────────────────────────────────────────────────────────────────
# MÉTRICAS — calculadas desde session_state; renderizadas en el placeholder
# ────────────────────────────────────────────────────────────────────────────

# Datos base de Capa 1
_ing = float(_pos("ING_PRINCIPAL").get("Monto_Mensual", 1))
_ese = float(_pos("GAS_ESE_BUCKET").get("Monto_Mensual", 0))
_liq = float(_pos("ACT_LIQUIDO_PRINCIPAL").get("Saldo_Actual", 0))

# Cuotas actuales de todos los pasivos
_cuotas: list[float] = [_cuota_actual(pid) for pid in _all_pasivo_ids()]

# Tablas de pasivos (excluye AFP) para flujo neto
_schedules_dict = st.session_state.get("schedules", {})
_tablas_pasivos = [
    df for pid, df in _schedules_dict.items()
    if pid != "AFP_PRINCIPAL" and pid in _all_pasivo_ids()
]

# Horizonte libre — último período entre todos los pasivos
_horizonte = "—"
if _all_pasivo_ids():
    terminos = [_fecha_termino(pid) for pid in _all_pasivo_ids() if _fecha_termino(pid) != "—"]
    if terminos:
        _horizonte = max(terminos)

with _metrics_ph.container():

    # ── Métrica 1: Carga financiera ───────────────────────────────────────────
    st.markdown("#### Carga Financiera")
    if _ing > 0:
        _carga = calculator.carga_financiera(_cuotas, _ing)
        if _carga < 0.35:
            icon_c, label_c = "🟢", f"Saludable · {_carga*100:.1f}% del ingreso"
        elif _carga < 0.50:
            icon_c, label_c = "🟡", f"Precaución · {_carga*100:.1f}% del ingreso"
        else:
            icon_c, label_c = "🔴", f"Crítico · {_carga*100:.1f}% del ingreso"
        st.metric(
            label=f"{icon_c} ratio deuda / ingreso",
            value=f"{_carga*100:.1f}%",
            delta=label_c,
            delta_color="off",
        )
        st.caption("Benchmark saludable: < 35 % del ingreso")
    else:
        st.metric("Carga Financiera", "—", help="Define tu ingreso en Capa 1.")

    st.divider()

    # ── Métrica 2: Posición de Vida v2 ────────────────────────────────────────
    st.markdown("#### Posición de Vida v2")
    _denominador = _ese + sum(_cuotas)
    if _denominador > 0:
        _pv2 = calculator.posicion_vida_v2(_liq, _ese, _cuotas)
        if _pv2 >= 3:
            icon_p, d_txt, d_col = "🟢", "Saludable — ≥ 3 meses", "normal"
        elif _pv2 >= 1:
            icon_p, d_txt, d_col = "🟡", "Precaución — entre 1 y 3", "off"
        else:
            icon_p, d_txt, d_col = "🔴", "Crítico — menos de 1 mes", "inverse"
        st.metric(
            label=f"{icon_p} meses cubiertos (incl. cuotas)",
            value=f"{_pv2:.1f}",
            delta=d_txt,
            delta_color=d_col,
        )
        st.caption("Liquidez / (Esenciales + Cuotas de deuda)")
    else:
        st.metric("Posición de Vida v2", "—", help="Agrega pasivos y define esenciales.")

    st.divider()

    # ── Métrica 3: Flujo neto mensual (gráfico) ───────────────────────────────
    st.markdown("#### Flujo Neto Mensual")
    if _tablas_pasivos:
        _df_flujo = schedule.flujo_neto_mensual(_tablas_pasivos, _ing)
        # Mostrar solo los próximos 24 meses
        hoy_str = date.today().strftime("%Y-%m")
        _df_flujo = _df_flujo[_df_flujo["Periodo"] >= hoy_str].head(24)

        if not _df_flujo.empty:
            _colores = [
                "#27ae60" if v >= 0 else "#e74c3c"
                for v in _df_flujo["Flujo_Neto"]
            ]
            fig = go.Figure(
                go.Bar(
                    x=_df_flujo["Periodo"],
                    y=_df_flujo["Flujo_Neto"],
                    marker_color=_colores,
                    hovertemplate="%{x}<br>Flujo: %{y:,.0f}<extra></extra>",
                )
            )
            fig.update_layout(
                height=220,
                margin=dict(l=0, r=0, t=8, b=0),
                showlegend=False,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(showgrid=True, gridcolor="#f0f0f0", zeroline=True,
                           zerolinecolor="#888"),
                xaxis=dict(tickangle=-45, tickfont=dict(size=9)),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            _meses_stress = int((_df_flujo["Flujo_Neto"] < 0).sum())
            if _meses_stress:
                st.warning(f"⚠️ {_meses_stress} meses en déficit en los próximos 24 meses.")
            else:
                st.success("✓ Sin meses en déficit en los próximos 24 meses.")
        else:
            st.caption("Sin datos futuros disponibles.")
    else:
        st.caption("Agrega al menos un pasivo para ver el flujo neto.")

    st.divider()

    # ── Métrica 4: Horizonte libre ────────────────────────────────────────────
    st.markdown("#### Horizonte Libre")
    st.metric(
        label="🏁 Último compromiso termina",
        value=_horizonte,
        help="Período en que termina el pasivo de mayor plazo registrado.",
    )
    if _all_pasivo_ids() and _horizonte != "—":
        st.caption(f"{len(_all_pasivo_ids())} compromisos registrados")

# ── Footer — Guardar + Banner Capa 3 ─────────────────────────────────────────

st.divider()
col_save, col_banner = st.columns([2, 3])

with col_save:
    _dirty = state.is_dirty()
    if st.button(
        "💾 Guardar en Drive",
        type="primary",
        disabled=not _dirty,
        use_container_width=True,
        help="Guarda posiciones y tablas en Drive." if _dirty else "No hay cambios pendientes.",
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
                st.success("✓ Guardado en Drive")
                st.rerun()
            except Exception as exc:
                st.error(f"Error al guardar: {exc}")

with col_banner:
    if state.get_layer() >= 3:
        if st.button(
            "✓ Crecimiento disponible — ir a Capa 3 →",
            use_container_width=True,
        ):
            st.switch_page("pages/04_capa3_crecimiento.py")
    elif _all_pasivo_ids() and not st.session_state.get("afp_saldo"):
        st.info("Agrega tu AFP para desbloquear Capa 3 →")
    elif not _all_pasivo_ids():
        st.info("Agrega un pasivo con tabla para desbloquear Capa 3 →")
