"""pages/03_capa2_control.py — Dashboard de Capa 2: Control.

Layout de dos columnas:
  - Izquierda: 4 métricas de Capa 2 (carga financiera, Posición de Vida v2,
    gráfico de flujo neto mensual, horizonte libre) + expander con indicadores extra.
  - Derecha: 4 tabs (Hipotecarios, Otros créditos, Previsional, Inversiones).

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
_TIPOS_PASIVO = ["Hipotecario", "Crédito consumo", "Colegio", "Jardín", "Arriendo", "Tarjeta", "Otro"]
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
    "Jardín":          {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_JAR"},
    "Arriendo":        {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_ARR"},
    "Tarjeta":         {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_TAR"},
    "Otro":            {"clase": "Pasivo_Corto_Plazo", "prefix": "PAS_OTR"},
}

# Keys de formulario de pasivo (se limpian al abrir un nuevo form)
_FORM_KEYS: list[str] = [
    "c2_desc", "c2_moneda_pas",
    # Hipotecario
    "c2_capital", "c2_tasa_anual_hip", "c2_plazo_meses", "c2_fecha_hip", "c2_metodo",
    # Hipotecario — activo asociado (opcional)
    "c2_activo_desc", "c2_activo_valor", "c2_activo_fecha",
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

# Etiquetas de visualización de buckets (para sugerencias y desglose)
_BUCKET_LABELS: dict[str, str] = {
    "GAS_ESE_BUCKET": "📦 Esenciales",
    "GAS_IMP_BUCKET": "📦 Importantes",
    "GAS_ASP_BUCKET": "📦 Aspiraciones",
}

# Tipos de cambio leídos temprano — disponibles en TODA la página (columnas izq/der).
# El usuario los actualiza en el expander "⚙️ Tipo de cambio" de la columna izquierda.
# El cambio toma efecto en el siguiente rerun (patrón estándar de Streamlit).
_valor_uf: float = float(st.session_state.get("valor_uf", 39_700.0))
_valor_usd: float = float(st.session_state.get("valor_usd", 950.0))


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


def _all_apv_ids() -> list[str]:
    """Lista todos los IDs de APVs registrados (clase Activo_Financiero, prefijo APV_)."""
    return [
        pid
        for pid in state.list_positions(clase="Activo_Financiero")
        if pid.startswith("APV_")
    ]


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
    st.session_state["schedules"][id_pos] = tabla
    if id_pos not in st.session_state["pasivos_con_tabla"]:
        st.session_state["pasivos_con_tabla"].append(id_pos)
    state.update_layer()


def _eliminar_pasivo(id_pos: str) -> None:
    """Elimina un pasivo de positions, schedules y pasivos_con_tabla."""
    state.delete_position(id_pos)
    st.session_state.get("schedules", {}).pop(id_pos, None)
    pac: list = st.session_state.get("pasivos_con_tabla", [])
    if id_pos in pac:
        pac.remove(id_pos)
    # Eliminar también cualquier sugerencia pendiente para esta posición
    sugs: list = st.session_state.get("sugerencias_pendientes", [])
    st.session_state["sugerencias_pendientes"] = [
        s for s in sugs if s.get("id_posicion") != id_pos
    ]
    state.update_layer()


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
        # La posición ya no existe — limpiar silenciosamente
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


def _abrir_formulario_pasivo(edit_id: str | None = None, tab_key: str = "") -> None:
    """Limpia keys de formulario y abre el form, opcionalmente en modo edición."""
    for k in _FORM_KEYS:
        st.session_state.pop(k, None)
    st.session_state["c2_show_add_form"] = True
    st.session_state["c2_edit_id"] = edit_id
    st.session_state["c2_active_tab"] = tab_key
    if edit_id:
        params = state.get_position(edit_id) or {}
        st.session_state["c2_tipo_nuevo"] = params.get("Tipo", "Hipotecario")
        # Pre-fill moneda (fuera del form) para que el selectbox muestre la moneda guardada
        moneda_guardada = params.get("Moneda", _MONEDA)
        if moneda_guardada in ["CLP", "UF", "USD"]:
            st.session_state["c2_moneda_pas"] = moneda_guardada


def _bucket_badge(id_pos: str) -> str:
    """Retorna la etiqueta del bucket vinculado a una posición."""
    p = state.get_position(id_pos) or {}
    bv = p.get("bucket_vinculado", "")
    return _BUCKET_LABELS.get(bv, "Sin bucket")


def _next_id_inversion_c2(tipo: str) -> str:
    """Genera el próximo ID para una inversión del tipo dado."""
    tipo_key = tipo.upper().replace(" ", "_")
    existing = [p for p in state.list_positions() if p.startswith(f"ACT_INV_{tipo_key}_")]
    n = len(existing) + 1
    return f"ACT_INV_{tipo_key}_{n:03d}"


def _next_id_activo_real() -> str:
    """Genera el próximo ID para un Activo_Real standalone."""
    existing = state.list_positions(clase="Activo_Real")
    nums: list[int] = []
    for p in existing:
        try:
            nums.append(int(p.split("_")[-1]))
        except ValueError:
            pass
    return f"ACT_REAL_{max(nums, default=0) + 1:03d}"


def _render_pasivo_form(tipo_forzado: str | None, tipos_disponibles: list[str],
                        bucket_auto: dict[str, str], form_key: str) -> None:
    """Renderiza el formulario de agregar/editar pasivo dentro de un tab.

    Args:
        tipo_forzado: Si se especifica, el selectbox de tipo no se muestra y se usa este.
        tipos_disponibles: Lista de tipos que el usuario puede seleccionar (si tipo_forzado es None).
        bucket_auto: Mapa tipo → bucket_id para asignación automática al crear.
        form_key: Sufijo único para las keys de Streamlit (evita colisiones entre tabs).
    """
    edit_id: str | None = st.session_state.get("c2_edit_id")
    # Sólo mostrar form si corresponde a este tab
    if edit_id:
        edit_tipo = (state.get_position(edit_id) or {}).get("Tipo", "")
        if tipo_forzado and edit_tipo != tipo_forzado:
            return
        if not tipo_forzado and edit_tipo not in tipos_disponibles:
            return

    editing_params = state.get_position(edit_id) or {} if edit_id else {}
    modo_label = "✏️ Editar pasivo" if edit_id else "➕ Nuevo pasivo"
    st.markdown(f"**{modo_label}**")

    if tipo_forzado:
        tipo = tipo_forzado
    else:
        tipo_default = editing_params.get("Tipo", tipos_disponibles[0])
        tipo_idx = tipos_disponibles.index(tipo_default) if tipo_default in tipos_disponibles else 0
        tipo = st.selectbox(
            "Tipo de compromiso",
            tipos_disponibles,
            index=tipo_idx,
            key=f"c2_tipo_{form_key}",
        )

    # Moneda también FUERA del form
    _mon_opts = ["CLP", "UF", "USD"]
    _mon_default = st.session_state.get(
        "c2_moneda_pas",
        editing_params.get("Moneda", _MONEDA),
    )
    if _mon_default not in _mon_opts:
        _mon_default = "CLP"
    moneda_pas = st.selectbox(
        "Moneda",
        _mon_opts,
        index=_mon_opts.index(_mon_default),
        key=f"c2_moneda_{form_key}",
    )

    with st.form(f"form_pasivo_{form_key}", clear_on_submit=False):
        descripcion = st.text_input(
            "Descripción",
            value=editing_params.get("Descripcion", ""),
            key=f"c2_desc_{form_key}",
            placeholder="Ej: Hipoteca departamento, Colegio hijo mayor…",
        )

        # ── Campos específicos por tipo ──────────────────────────────────
        if tipo == "Hipotecario":
            _step = 1.0 if moneda_pas == "UF" else 1_000_000
            _fmt_num = "%.2f" if moneda_pas == "UF" else "%.0f"
            _def_cap = editing_params.get("Capital", 2000.0 if moneda_pas == "UF" else 50_000_000)
            capital = st.number_input(
                f"Capital ({moneda_pas})",
                min_value=0.0, value=float(_def_cap), step=float(_step),
                format=_fmt_num, key=f"c2_capital_{form_key}",
            )
            tasa_hip = st.number_input(
                "Tasa anual (%)",
                min_value=0.0, max_value=30.0,
                value=float(editing_params.get("Tasa_Anual_Pct", 4.5)),
                step=0.1, format="%.2f", key=f"c2_tasa_anual_hip_{form_key}",
                help="Tasa de interés anual. Ej: 4.5 para 4,5 %",
            )
            plazo_meses = int(st.number_input(
                "Plazo (meses)",
                min_value=1, max_value=600,
                value=int(editing_params.get("Plazo_Meses", 240)),
                step=12, key=f"c2_plazo_meses_{form_key}",
            ))
            fecha_hip = st.date_input(
                "Primera cuota",
                value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                key=f"c2_fecha_hip_{form_key}",
            )
            metodo = st.selectbox(
                "Método de amortización",
                ["frances", "aleman"],
                index=0 if editing_params.get("Metodo", "frances") == "frances" else 1,
                format_func=lambda x: "Francés (cuota fija)" if x == "frances" else "Alemán (amort. constante)",
                key=f"c2_metodo_{form_key}",
            )

            # ── Activo hipotecado (opcional) ─────────────────────────────
            st.markdown("---")
            st.markdown("**🏠 Activo hipotecado** *(opcional)*")
            st.caption(
                "Si registras el valor comercial de la propiedad, "
                "calcularemos el LTV y tu patrimonio neto inmobiliario."
            )
            _act_suffix = edit_id.rsplit("_", 1)[-1] if edit_id else None
            _act_real_params = (
                state.get_position(f"ACT_REAL_{_act_suffix}") or {}
                if _act_suffix else {}
            )
            activo_desc = st.text_input(
                "Descripción del activo",
                value=_act_real_params.get("Descripcion", ""),
                key=f"c2_activo_desc_{form_key}",
                placeholder="Ej: Depto. Las Condes, Casa Providencia…",
            )
            activo_valor = st.number_input(
                f"Valor comercial ({moneda_pas})",
                min_value=0.0,
                value=float(_act_real_params.get("Valor_Comercial", 0.0)),
                step=float(_step),
                format=_fmt_num,
                key=f"c2_activo_valor_{form_key}",
                help="Estimación del valor de mercado actual. Deja en 0 para omitir.",
            )
            activo_fecha = st.date_input(
                "Fecha de última estimación",
                value=date.fromisoformat(
                    str(_act_real_params.get("Fecha_Valoracion", date.today().isoformat()))
                ),
                key=f"c2_activo_fecha_{form_key}",
            )

        elif tipo == "Crédito consumo":
            _step_c = 1.0 if moneda_pas == "UF" else 100_000
            _def_m = editing_params.get("Monto", 5_000_000)
            monto_con = st.number_input(
                f"Monto del crédito ({moneda_pas})",
                min_value=0.0, value=float(_def_m),
                step=float(_step_c), key=f"c2_monto_con_{form_key}",
            )
            n_cuotas_con = int(st.number_input(
                "N° de cuotas",
                min_value=1, max_value=360,
                value=int(editing_params.get("N_Cuotas", 24)),
                step=1, key=f"c2_n_cuotas_con_{form_key}",
            ))
            tasa_con = st.number_input(
                "Tasa anual (%)",
                min_value=0.0, max_value=80.0,
                value=float(editing_params.get("Tasa_Anual_Pct", 12.0)),
                step=0.5, format="%.2f", key=f"c2_tasa_anual_con_{form_key}",
                help="Tasa anual del crédito. Ej: 12.0 para 12 %",
            )
            fecha_con = st.date_input(
                "Primera cuota",
                value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                key=f"c2_fecha_con_{form_key}",
            )

        elif tipo == "Colegio":
            _step_ma = 1.0 if moneda_pas == "UF" else 100_000
            monto_anual = st.number_input(
                f"Monto anual ({moneda_pas})",
                min_value=0.0,
                value=float(editing_params.get("Monto_Anual", 3_000_000)),
                step=float(_step_ma), key=f"c2_monto_anual_{form_key}",
            )
            cuotas_ano = int(st.number_input(
                "Cuotas por año",
                min_value=1, max_value=12,
                value=int(editing_params.get("Cuotas_Por_Ano", 10)),
                step=1, key=f"c2_cuotas_ano_{form_key}",
            ))
            anos_rest = int(st.number_input(
                "Años restantes",
                min_value=1, max_value=20,
                value=int(editing_params.get("Anos_Restantes", 3)),
                step=1, key=f"c2_anos_rest_{form_key}",
            ))
            meses_defecto = list(editing_params.get("Meses_De_Pago", [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))
            meses_pago = st.multiselect(
                "Meses de pago",
                options=list(range(1, 13)),
                default=meses_defecto,
                format_func=lambda m: _MESES_NOMBRE[m],
                key=f"c2_meses_pago_{form_key}",
                help="Meses del año en que se realizan los pagos.",
            )
            fecha_col = st.date_input(
                "Fecha de inicio",
                value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                key=f"c2_fecha_col_{form_key}",
            )

        elif tipo == "Tarjeta":
            _step_t = 1.0 if moneda_pas == "UF" else 10_000
            deuda_total = st.number_input(
                f"Deuda total ({moneda_pas})",
                min_value=0.0,
                value=float(editing_params.get("Deuda_Total", 1_000_000)),
                step=float(_step_t), key=f"c2_deuda_total_{form_key}",
            )
            pago_mensual = st.number_input(
                f"Pago mensual ({moneda_pas})",
                min_value=0.0,
                value=float(editing_params.get("Pago_Mensual", 100_000)),
                step=float(_step_t), key=f"c2_pago_mensual_{form_key}",
            )
            tasa_tar = st.number_input(
                "Tasa mensual (%)",
                min_value=0.0, max_value=10.0,
                value=float(editing_params.get("Tasa_Mensual_Pct", 2.0)),
                step=0.1, format="%.2f", key=f"c2_tasa_mensual_tar_{form_key}",
                help="Tasa mensual de la tarjeta. Ej: 2.0 para 2 % mensual",
            )
            fecha_tar = st.date_input(
                "Fecha primer pago",
                value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                key=f"c2_fecha_tar_{form_key}",
            )

        else:  # Otro
            _step_o = 1.0 if moneda_pas == "UF" else 10_000
            cuota_otro = st.number_input(
                f"Cuota mensual ({moneda_pas})",
                min_value=0.0,
                value=float(editing_params.get("Cuota_Mensual", 100_000)),
                step=float(_step_o), key=f"c2_cuota_otro_{form_key}",
            )
            n_cuotas_otro = int(st.number_input(
                "N° de cuotas restantes",
                min_value=1, max_value=600,
                value=int(editing_params.get("N_Cuotas", 12)),
                step=1, key=f"c2_n_cuotas_otro_{form_key}",
            ))
            fecha_otro = st.date_input(
                "Primera cuota",
                value=date.fromisoformat(str(editing_params.get("Fecha_Inicio", date.today().isoformat()))),
                key=f"c2_fecha_otro_{form_key}",
            )

        # Bucket selectbox en modo edición
        if edit_id:
            _bucket_opts = {"Esenciales": "GAS_ESE_BUCKET", "Importantes": "GAS_IMP_BUCKET", "Aspiraciones": "GAS_ASP_BUCKET"}
            _current_bucket_label = {v: k for k, v in _bucket_opts.items()}.get(
                (state.get_position(edit_id) or {}).get("bucket_vinculado", ""), "Esenciales"
            )
            _nuevo_bucket_label = st.selectbox(
                "Asignar a bucket",
                list(_bucket_opts.keys()),
                index=list(_bucket_opts.keys()).index(_current_bucket_label),
                key=f"bucket_sel_{edit_id or 'new'}_{form_key}",
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

            # Activo hipotecado — guardar/eliminar DESPUÉS de conocer id_final
            if tipo == "Hipotecario":
                _act_suf = id_final.rsplit("_", 1)[-1]
                _act_real_id = f"ACT_REAL_{_act_suf}"
                if activo_valor > 0:
                    state.set_position(_act_real_id, {
                        "Tipo": "Activo_Real",
                        "Clase": "Activo_Real",
                        "Descripcion": activo_desc.strip() or descripcion.strip(),
                        "Moneda": moneda_pas,
                        "Capa_Activacion": 2,
                        "Valor_Comercial": activo_valor,
                        "Fecha_Valoracion": activo_fecha.isoformat(),
                        "Pasivo_Asociado": id_final,
                    })
                else:
                    # Si el usuario dejó valor=0, eliminar el activo si existía
                    state.delete_position(_act_real_id)

            # Generar tabla de desarrollo
            tabla = _generar_tabla(tipo, params_nuevos, id_final)
            if tabla is not None:
                # ── Cuota en CLP para sugerencias y vinculación ──────────
                _hoy_sug = date.today().strftime("%Y-%m")
                _fut_sug = tabla[tabla["Periodo"] >= _hoy_sug]
                _cuota_mon_sug = (
                    abs(float(_fut_sug["Flujo_Periodo"].iloc[0]))
                    if not _fut_sug.empty else 0.0
                )
                _cuota_clp_sug = calculator.normalizar_a_clp(
                    _cuota_mon_sug, moneda_pas, _valor_uf, _valor_usd
                )

                # Carry over vinculación existente en edición (no perder el link)
                if edit_id:
                    _old_pos_sug = state.get_position(id_final) or {}
                    if _old_pos_sug.get("bucket_vinculado"):
                        params_nuevos["bucket_vinculado"] = _old_pos_sug["bucket_vinculado"]
                        params_nuevos["Cuota_Vinculada_CLP"] = _cuota_clp_sug
                    # Aplicar bucket del selectbox si estaba visible
                    _bk_key = f"bucket_sel_{edit_id}_{form_key}"
                    if _bk_key in st.session_state:
                        _bucket_opts_c = {"Esenciales": "GAS_ESE_BUCKET", "Importantes": "GAS_IMP_BUCKET", "Aspiraciones": "GAS_ASP_BUCKET"}
                        params_nuevos["bucket_vinculado"] = _bucket_opts_c[st.session_state[_bk_key]]
                        params_nuevos["Cuota_Vinculada_CLP"] = _cuota_clp_sug

                # Auto-bucket en nuevo hipotecario
                if not edit_id and tipo == "Hipotecario":
                    params_nuevos["bucket_vinculado"] = "GAS_ESE_BUCKET"
                    params_nuevos["Cuota_Vinculada_CLP"] = _cuota_clp_sug

                # Auto-bucket en nuevos otros créditos
                if not edit_id and tipo in bucket_auto:
                    params_nuevos["bucket_vinculado"] = bucket_auto[tipo]
                    params_nuevos["Cuota_Vinculada_CLP"] = _cuota_clp_sug

                _registrar_pasivo(id_final, params_nuevos, tabla)

                # Crear sugerencia solo si la posición no está ya vinculada
                if not params_nuevos.get("bucket_vinculado") and _cuota_clp_sug > 0:
                    _bucket_sug = calculator.bucket_sugerido(tipo)
                    _espacio_sug = calculator.espacio_disponible_bucket(
                        st.session_state, _bucket_sug
                    )
                    _sugs_list: list = st.session_state.setdefault(
                        "sugerencias_pendientes", []
                    )
                    # Actualizar si ya existe una sugerencia para esta posición
                    _sugs_list[:] = [
                        s for s in _sugs_list if s.get("id_posicion") != id_final
                    ]
                    _sugs_list.append({
                        "id": id_final,
                        "tipo": tipo,
                        "descripcion": descripcion.strip(),
                        "monto": _cuota_clp_sug,
                        "bucket": _bucket_sug,
                        "id_posicion": id_final,
                        "excede_espacio": _cuota_clp_sug > _espacio_sug,
                        "exceso_clp": max(0.0, _cuota_clp_sug - _espacio_sug),
                    })

                st.session_state["c2_show_add_form"] = False
                st.session_state.pop("c2_edit_id", None)
                st.success(f"✓ {'Actualizado' if edit_id else 'Agregado'}: {descripcion}")
                if not edit_id and tipo == "Hipotecario":
                    st.info("✓ Asignado a Esenciales — edita en la lista si quieres cambiarlo")
                st.rerun()


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

# Activo liquido total — necesario en tabs e métricas
_activo_liq_total = sum(
    calculator.normalizar_a_clp(
        float((state.get_position(p) or {}).get("Saldo_Actual", 0)),
        (state.get_position(p) or {}).get("Moneda", "CLP"),
        _valor_uf, _valor_usd,
    )
    for p in state.list_positions(clase="Activo_Liquido")
)

# Esenciales — necesario en tab Activos Líquidos (fondo de reserva)
_ese = calculator.normalizar_a_clp(
    float(_pos("GAS_ESE_BUCKET").get("Monto_Mensual", 0)),
    _pos("GAS_ESE_BUCKET").get("Moneda", _MONEDA),
    _valor_uf, _valor_usd,
)

col_left, col_right = st.columns([5, 7], gap="large")

# Placeholder en columna izquierda — se llena después de procesar la derecha
with col_left:
    _metrics_ph = st.empty()

# ────────────────────────────────────────────────────────────────────────────
# COLUMNA DERECHA — 2 grupos de tabs
# ────────────────────────────────────────────────────────────────────────────

with col_right:
    st.markdown("#### 🔴 Pasivos y Compromisos")
    tab1, tab2, tab3 = st.tabs([
        "🏠 Hipotecarios", "💳 Créditos", "📚 Otros compromisos",
    ])
    st.markdown("---")
    st.markdown("#### 🟢 Activos e Inversiones")
    tab4, tab5, tab6 = st.tabs([
        "🔵 Previsional", "📈 Inversiones", "🏡 Otros activos",
    ])

    # ── TAB 1 — Hipotecarios ──────────────────────────────────────────────────
    with tab1:
        st.markdown("#### 🔴 Hipotecarios")

        show_form = st.session_state.get("c2_show_add_form", False)
        active_tab = st.session_state.get("c2_active_tab", "")

        if not show_form or active_tab != "hip":
            if st.button("➕ Agregar hipotecario", use_container_width=True, key="btn_add_hip"):
                _abrir_formulario_pasivo(tab_key="hip")
                st.rerun()

        if show_form and active_tab == "hip":
            _render_pasivo_form(
                tipo_forzado="Hipotecario",
                tipos_disponibles=["Hipotecario"],
                bucket_auto={},
                form_key="hip",
            )

        # Lista hipotecarios
        hip_ids = [p for p in _all_pasivo_ids() if _pos(p).get("Tipo") == "Hipotecario"]
        if hip_ids:
            st.divider()
            for pid in hip_ids:
                pparams = _pos(pid)
                desc_p = pparams.get("Descripcion", pid)
                cuota_p = _cuota_actual(pid)
                saldo_p = _saldo_actual_pasivo(pid)
                termino_p = _fecha_termino(pid)
                moneda_p = pparams.get("Moneda", "CLP")

                with st.container():
                    c1, c2, c3 = st.columns([5, 1, 1])
                    with c1:
                        if moneda_p == "UF":
                            _saldo_clp = int(calculator.normalizar_a_clp(saldo_p, "UF", _valor_uf, _valor_usd))
                            _cuota_clp = int(calculator.normalizar_a_clp(cuota_p, "UF", _valor_uf, _valor_usd))
                            _saldo_str = f"UF {saldo_p:,.2f} → $ {_saldo_clp:,}"
                            _cuota_str = f"UF {cuota_p:,.2f} → $ {_cuota_clp:,}"
                        elif moneda_p == "USD":
                            _saldo_clp = int(calculator.normalizar_a_clp(saldo_p, "USD", _valor_uf, _valor_usd))
                            _cuota_clp = int(calculator.normalizar_a_clp(cuota_p, "USD", _valor_uf, _valor_usd))
                            _saldo_str = f"USD {saldo_p:,.0f} → $ {_saldo_clp:,}"
                            _cuota_str = f"USD {cuota_p:,.0f} → $ {_cuota_clp:,}"
                        else:
                            _saldo_str = _fmt(saldo_p)
                            _cuota_str = _fmt(cuota_p)
                        st.markdown(
                            f"**{desc_p}** · *Hipotecario*  \n"
                            f"Saldo {_saldo_str} · Cuota {_cuota_str}/mes · "
                            f"Término {termino_p}  \n"
                            f"Bucket: {_bucket_badge(pid)}"
                        )
                        # LTV y patrimonio neto
                        if pid.startswith("PAS_HIP"):
                            _ltv_suffix = pid.rsplit("_", 1)[-1]
                            _act_real = state.get_position(f"ACT_REAL_{_ltv_suffix}") or {}
                            _valor_com = float(_act_real.get("Valor_Comercial", 0))
                            if _valor_com > 0:
                                _ltv_pct = (saldo_p / _valor_com) * 100
                                _patr_neto = _valor_com - saldo_p
                                if _ltv_pct < 70:
                                    _ltv_icon, _ltv_color = "🟢", "green"
                                elif _ltv_pct < 90:
                                    _ltv_icon, _ltv_color = "🟡", "orange"
                                else:
                                    _ltv_icon, _ltv_color = "🔴", "red"
                                st.caption(
                                    f"{_ltv_icon} LTV: :{_ltv_color}[**{_ltv_pct:.1f}%**] · "
                                    f"Patrimonio neto: **{_fmt(_patr_neto)}**"
                                )
                    with c2:
                        if st.button("✏️", key=f"edit_hip_{pid}", help="Editar"):
                            _abrir_formulario_pasivo(edit_id=pid, tab_key="hip")
                            st.rerun()
                    with c3:
                        if st.button("🗑️", key=f"del_hip_{pid}", help="Eliminar"):
                            _eliminar_pasivo(pid)
                            if pid.startswith("PAS_HIP"):
                                state.delete_position(f"ACT_REAL_{pid.rsplit('_', 1)[-1]}")
                            state.mark_dirty()
                            st.rerun()
        elif not show_form:
            st.info("Aún no registraste ninguna hipoteca. Usa el botón ➕ para agregar.")

    # ── TAB 2 — Créditos ──────────────────────────────────────────────────────
    with tab2:
        st.markdown("#### 💳 Créditos")
        _tipos_creditos = ["Crédito consumo", "Tarjeta"]
        _bucket_auto_creditos = {
            "Crédito consumo": "GAS_IMP_BUCKET",
            "Tarjeta": "GAS_IMP_BUCKET",
        }

        show_form_cred = st.session_state.get("c2_show_add_form", False)
        active_tab_cred = st.session_state.get("c2_active_tab", "")

        if not show_form_cred or active_tab_cred != "cred":
            if st.button("➕ Agregar crédito", use_container_width=True, key="btn_add_cred"):
                _abrir_formulario_pasivo(tab_key="cred")
                st.rerun()

        if show_form_cred and active_tab_cred == "cred":
            _render_pasivo_form(
                tipo_forzado=None,
                tipos_disponibles=_tipos_creditos,
                bucket_auto=_bucket_auto_creditos,
                form_key="cred",
            )

        # Lista créditos
        cred_ids = [p for p in _all_pasivo_ids() if _pos(p).get("Tipo") in _tipos_creditos]
        if cred_ids:
            st.divider()
            for pid in cred_ids:
                pparams = _pos(pid)
                tipo_p = pparams.get("Tipo", "Pasivo")
                desc_p = pparams.get("Descripcion", pid)
                cuota_p = _cuota_actual(pid)
                saldo_p = _saldo_actual_pasivo(pid)
                termino_p = _fecha_termino(pid)
                moneda_p = pparams.get("Moneda", "CLP")

                with st.container():
                    c1, c2, c3 = st.columns([5, 1, 1])
                    with c1:
                        if moneda_p == "UF":
                            _saldo_clp = int(calculator.normalizar_a_clp(saldo_p, "UF", _valor_uf, _valor_usd))
                            _cuota_clp = int(calculator.normalizar_a_clp(cuota_p, "UF", _valor_uf, _valor_usd))
                            _saldo_str = f"UF {saldo_p:,.2f} → $ {_saldo_clp:,}"
                            _cuota_str = f"UF {cuota_p:,.2f} → $ {_cuota_clp:,}"
                        elif moneda_p == "USD":
                            _saldo_clp = int(calculator.normalizar_a_clp(saldo_p, "USD", _valor_uf, _valor_usd))
                            _cuota_clp = int(calculator.normalizar_a_clp(cuota_p, "USD", _valor_uf, _valor_usd))
                            _saldo_str = f"USD {saldo_p:,.0f} → $ {_saldo_clp:,}"
                            _cuota_str = f"USD {cuota_p:,.0f} → $ {_cuota_clp:,}"
                        else:
                            _saldo_str = _fmt(saldo_p)
                            _cuota_str = _fmt(cuota_p)
                        st.markdown(
                            f"**{desc_p}** · *{tipo_p}*  \n"
                            f"Saldo {_saldo_str} · Cuota {_cuota_str}/mes · "
                            f"Término {termino_p}  \n"
                            f"Bucket: {_bucket_badge(pid)}"
                        )
                    with c2:
                        if st.button("✏️", key=f"edit_cred_{pid}", help="Editar"):
                            _abrir_formulario_pasivo(edit_id=pid, tab_key="cred")
                            st.rerun()
                    with c3:
                        if st.button("🗑️", key=f"del_cred_{pid}", help="Eliminar"):
                            _eliminar_pasivo(pid)
                            state.mark_dirty()
                            st.rerun()
        elif not show_form_cred:
            st.info("Aún no registraste créditos de consumo ni tarjetas.")

    # ── TAB 3 — Otros compromisos ─────────────────────────────────────────────
    with tab3:
        st.markdown("#### 📚 Otros compromisos")
        _tipos_otros_c = ["Colegio", "Jardín", "Arriendo", "Otro"]
        _bucket_auto_otros_c = {
            "Colegio": "GAS_ESE_BUCKET",
            "Jardín": "GAS_ESE_BUCKET",
            "Arriendo": "GAS_ESE_BUCKET",
            "Otro": "GAS_IMP_BUCKET",
        }

        show_form_otros = st.session_state.get("c2_show_add_form", False)
        active_tab_otros = st.session_state.get("c2_active_tab", "")

        if not show_form_otros or active_tab_otros != "otros":
            if st.button("➕ Agregar compromiso", use_container_width=True, key="btn_add_otros"):
                _abrir_formulario_pasivo(tab_key="otros")
                st.rerun()

        if show_form_otros and active_tab_otros == "otros":
            _render_pasivo_form(
                tipo_forzado=None,
                tipos_disponibles=_tipos_otros_c,
                bucket_auto=_bucket_auto_otros_c,
                form_key="otros",
            )

        # Lista otros compromisos
        otros_ids_c = [p for p in _all_pasivo_ids() if _pos(p).get("Tipo") in _tipos_otros_c]
        if otros_ids_c:
            st.divider()
            for pid in otros_ids_c:
                pparams = _pos(pid)
                tipo_p = pparams.get("Tipo", "Pasivo")
                desc_p = pparams.get("Descripcion", pid)
                cuota_p = _cuota_actual(pid)
                saldo_p = _saldo_actual_pasivo(pid)
                termino_p = _fecha_termino(pid)
                moneda_p = pparams.get("Moneda", "CLP")

                with st.container():
                    c1, c2, c3 = st.columns([5, 1, 1])
                    with c1:
                        if moneda_p == "UF":
                            _saldo_clp = int(calculator.normalizar_a_clp(saldo_p, "UF", _valor_uf, _valor_usd))
                            _cuota_clp = int(calculator.normalizar_a_clp(cuota_p, "UF", _valor_uf, _valor_usd))
                            _saldo_str = f"UF {saldo_p:,.2f} → $ {_saldo_clp:,}"
                            _cuota_str = f"UF {cuota_p:,.2f} → $ {_cuota_clp:,}"
                        elif moneda_p == "USD":
                            _saldo_clp = int(calculator.normalizar_a_clp(saldo_p, "USD", _valor_uf, _valor_usd))
                            _cuota_clp = int(calculator.normalizar_a_clp(cuota_p, "USD", _valor_uf, _valor_usd))
                            _saldo_str = f"USD {saldo_p:,.0f} → $ {_saldo_clp:,}"
                            _cuota_str = f"USD {cuota_p:,.0f} → $ {_cuota_clp:,}"
                        else:
                            _saldo_str = _fmt(saldo_p)
                            _cuota_str = _fmt(cuota_p)
                        st.markdown(
                            f"**{desc_p}** · *{tipo_p}*  \n"
                            f"Saldo {_saldo_str} · Cuota {_cuota_str}/mes · "
                            f"Término {termino_p}  \n"
                            f"Bucket: {_bucket_badge(pid)}"
                        )
                    with c2:
                        if st.button("✏️", key=f"edit_otro_{pid}", help="Editar"):
                            _abrir_formulario_pasivo(edit_id=pid, tab_key="otros")
                            st.rerun()
                    with c3:
                        if st.button("🗑️", key=f"del_otro_{pid}", help="Eliminar"):
                            _eliminar_pasivo(pid)
                            state.mark_dirty()
                            st.rerun()
        elif not show_form_otros:
            st.info("Aún no registraste colegios, arriendos u otros compromisos.")

    # ── TAB 4 — Previsional ───────────────────────────────────────────────────
    with tab4:
        # ── Sección AFP ───────────────────────────────────────────────────────
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
                        st.session_state["schedules"]["AFP_PRINCIPAL"] = tabla_afp_nueva
                        st.session_state["afp_saldo"] = float(afp_saldo_in)
                        state.update_layer()
                        st.session_state["c2_show_afp_form"] = False
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Error al generar proyección AFP: {exc}")

        # ── Sección APV ───────────────────────────────────────────────────────
        st.divider()
        st.markdown("#### 📈 APV (Ahorro Previsional Voluntario)")

        apv_ids = _all_apv_ids()

        if not st.session_state.get("c2_show_apv_form", False):
            if st.button("➕ Agregar APV", use_container_width=True, key="btn_add_apv"):
                for _k in ["c2_apv_inst", "c2_apv_saldo", "c2_apv_aporte",
                           "c2_apv_regimen", "c2_apv_tasa"]:
                    st.session_state.pop(_k, None)
                st.session_state["c2_show_apv_form"] = True
                st.session_state.pop("c2_edit_apv_id", None)
                st.rerun()

        # Lista de APVs registrados
        if apv_ids:
            for apv_id in apv_ids:
                apv_p = _pos(apv_id)
                apv_saldo_val = float(apv_p.get("Saldo_Actual", 0))
                apv_tabla = st.session_state.get("schedules", {}).get(apv_id)
                apv_proy = (
                    float(apv_tabla["Saldo_Final"].iloc[-1])
                    if apv_tabla is not None and not apv_tabla.empty
                    else apv_saldo_val
                )
                c_apv1, c_apv2, c_apv3 = st.columns([5, 1, 1])
                with c_apv1:
                    st.markdown(
                        f"**{apv_p.get('Descripcion', apv_id)}** · "
                        f"Saldo {_fmt(apv_saldo_val)} · "
                        f"Proyección {_fmt(apv_proy)}"
                    )
                    _reg = apv_p.get("Regimen_Tributario", "")
                    if _reg:
                        st.caption(f"Régimen {_reg} · Tasa {apv_p.get('Tasa_Anual_Pct', 5.0):.1f}% anual")
                with c_apv2:
                    if st.button("✏️", key=f"edit_apv_{apv_id}", help="Editar"):
                        st.session_state["c2_show_apv_form"] = True
                        st.session_state["c2_edit_apv_id"] = apv_id
                        st.rerun()
                with c_apv3:
                    if st.button("🗑️", key=f"del_apv_{apv_id}", help="Eliminar"):
                        state.delete_position(apv_id)
                        st.session_state.get("schedules", {}).pop(apv_id, None)
                        state.mark_dirty()
                        st.rerun()
        elif not st.session_state.get("c2_show_apv_form", False):
            st.caption("Sin APVs registrados. El APV complementa tu AFP con ahorro voluntario.")

        # Formulario APV
        if st.session_state.get("c2_show_apv_form", False):
            edit_apv_id: str | None = st.session_state.get("c2_edit_apv_id")
            apv_edit_p = state.get_position(edit_apv_id) or {} if edit_apv_id else {}
            modo_apv = "✏️ Editar APV" if edit_apv_id else "➕ Nuevo APV"

            with st.form("form_apv"):
                st.markdown(f"**{modo_apv}**")
                ca1_apv, ca2_apv = st.columns(2)
                with ca1_apv:
                    apv_inst = st.text_input(
                        "Institución",
                        value=apv_edit_p.get("Descripcion", ""),
                        key="c2_apv_inst",
                        placeholder="Ej: Habitat, Cuprum, BCI…",
                    )
                    apv_saldo_in = st.number_input(
                        "Saldo actual (CLP)",
                        min_value=0, step=500_000,
                        value=int(apv_edit_p.get("Saldo_Actual", 0)),
                        key="c2_apv_saldo",
                    )
                    apv_aporte_in = st.number_input(
                        "Aporte mensual (CLP)",
                        min_value=0, step=10_000,
                        value=int(apv_edit_p.get("Aporte_Mensual", 0)),
                        key="c2_apv_aporte",
                    )
                with ca2_apv:
                    _regimenes = ["A", "B", "Sin régimen"]
                    _reg_def = apv_edit_p.get("Regimen_Tributario", "A")
                    if _reg_def not in _regimenes:
                        _reg_def = "A"
                    apv_regimen = st.selectbox(
                        "Régimen tributario",
                        _regimenes,
                        index=_regimenes.index(_reg_def),
                        key="c2_apv_regimen",
                        help="A: retiro exento de impuesto. B: descuenta base imponible al aportar.",
                    )
                    apv_tasa_in = st.number_input(
                        "Rentabilidad anual esperada (%)",
                        min_value=0.0, max_value=20.0,
                        value=float(apv_edit_p.get("Tasa_Anual_Pct", 5.0)),
                        step=0.5, format="%.1f", key="c2_apv_tasa",
                        help="Promedio histórico APV conservador en CLP: ~4–6 %",
                    )

                c_ok_apv, c_can_apv = st.columns(2)
                with c_ok_apv:
                    apv_ok = st.form_submit_button(
                        "✓ Guardar APV", type="primary", use_container_width=True
                    )
                with c_can_apv:
                    apv_can = st.form_submit_button("✕ Cancelar", use_container_width=True)

                if apv_can:
                    st.session_state["c2_show_apv_form"] = False
                    st.session_state.pop("c2_edit_apv_id", None)
                    st.rerun()

                if apv_ok:
                    if not apv_inst.strip():
                        st.error("La institución no puede estar vacía.")
                        st.stop()

                    # Horizonte: AFP registrada → plan_params → default conservador (40→65)
                    afp_ids = state.list_positions(clase="Prevision_AFP")
                    if afp_ids:
                        afp_pos = state.get_position(afp_ids[0]) or {}
                        edad_actual = int(afp_pos.get("Edad_Actual", 40))
                        edad_jubilacion = int(afp_pos.get("Edad_Jubilacion", 65))
                    else:
                        edad_jubilacion = int(
                            st.session_state.get("plan_params", {})
                            .get("edad_jubilacion", 65)
                        )
                        edad_actual = 40
                    horizonte_meses = max((edad_jubilacion - edad_actual) * 12, 1)
                    if horizonte_meses <= 12:
                        st.warning(
                            f"⚠️ Horizonte APV muy corto ({horizonte_meses} meses). "
                            "Verifica que tu AFP tenga Edad_Actual y Edad_Jubilacion registradas."
                        )
                    elif not afp_ids:
                        st.info(
                            f"ℹ️ Sin AFP registrada — horizonte estimado a {horizonte_meses} meses "
                            f"(edad {edad_actual} → {edad_jubilacion} años). "
                            "Registra tu AFP en la sección de previsión para un cálculo exacto."
                        )

                    _apv_suffix = (
                        apv_inst.strip().upper()
                        .replace(" ", "_").replace(".", "").replace(",", "")[:20]
                    )
                    apv_id_final = edit_apv_id if edit_apv_id else f"APV_{_apv_suffix}"

                    params_apv = {
                        "Tipo": "APV",
                        "Clase": "Activo_Financiero",
                        "Descripcion": apv_inst.strip(),
                        "Moneda": "CLP",
                        "Capa_Activacion": 2,
                        "Saldo_Actual": apv_saldo_in,
                        "Aporte_Mensual": apv_aporte_in,
                        "Tasa_Anual": apv_tasa_in / 100,
                        "Tasa_Anual_Pct": apv_tasa_in,
                        "Regimen_Tributario": apv_regimen,
                        "Fecha_Inicio": date.today().isoformat(),
                        "Horizonte_Meses": horizonte_meses,
                    }
                    try:
                        print(
                            f"DEBUG APV: horizonte_meses={horizonte_meses}, "
                            f"edad_actual={edad_actual}, edad_jubilacion={edad_jubilacion}"
                        )
                        tabla_apv_nueva = schedule.gen_fondo_inversion(
                            saldo=float(apv_saldo_in),
                            aporte_mensual=float(apv_aporte_in),
                            tasa_anual=apv_tasa_in / 100,
                            horizonte_meses=horizonte_meses,
                            fecha_inicio=date.today(),
                            moneda="CLP",
                            id_posicion=apv_id_final,
                        )
                        state.set_position(apv_id_final, params_apv)
                        st.session_state["schedules"][apv_id_final] = tabla_apv_nueva
                        state.mark_dirty()
                        state.update_layer()
                        st.session_state["c2_show_apv_form"] = False
                        st.session_state.pop("c2_edit_apv_id", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Error al generar proyección APV: {exc}")

        # ── Resumen previsional consolidado (AFP + APVs) ──────────────────────
        _afp_res = _pos("AFP_PRINCIPAL")
        if _afp_res:
            st.divider()
            st.markdown("##### 📊 Resumen Previsional Consolidado")

            _edad_jub_res = float(_afp_res.get("Edad_Jubilacion", 65))
            _rows_resumen: list[dict] = []

            # AFP
            _tabla_afp_res = st.session_state.get("schedules", {}).get("AFP_PRINCIPAL")
            _proy_afp_res = (
                float(_tabla_afp_res["Saldo_Final"].iloc[-1])
                if _tabla_afp_res is not None and not _tabla_afp_res.empty
                else float(_afp_res.get("Saldo_Actual", 0))
            )
            _total_proy = _proy_afp_res
            _rows_resumen.append({
                "Instrumento": "AFP Principal",
                "Saldo Actual": f"$ {int(float(_afp_res.get('Saldo_Actual', 0))):,}",
                "Proyección jubilación": f"$ {int(_proy_afp_res):,}",
            })

            # APVs
            for _apv_id_res in _all_apv_ids():
                _apv_p_res = _pos(_apv_id_res)
                _apv_tab_res = st.session_state.get("schedules", {}).get(_apv_id_res)
                _proy_apv_res = (
                    float(_apv_tab_res["Saldo_Final"].iloc[-1])
                    if _apv_tab_res is not None and not _apv_tab_res.empty
                    else float(_apv_p_res.get("Saldo_Actual", 0))
                )
                _total_proy += _proy_apv_res
                _rows_resumen.append({
                    "Instrumento": f"APV {_apv_p_res.get('Descripcion', _apv_id_res)}",
                    "Saldo Actual": f"$ {int(float(_apv_p_res.get('Saldo_Actual', 0))):,}",
                    "Proyección jubilación": f"$ {int(_proy_apv_res):,}",
                })

            import pandas as _pd  # noqa: PLC0415
            st.dataframe(
                _pd.DataFrame(_rows_resumen),
                use_container_width=True,
                hide_index=True,
            )

            col_tot, col_pen = st.columns(2)
            with col_tot:
                st.metric("💰 Total proyectado", f"$ {int(_total_proy):,}")
            with col_pen:
                _anos_pension = max(1.0, 85.0 - _edad_jub_res)
                _pension_men = _total_proy / (_anos_pension * 12)
                st.metric(
                    "📅 Pensión mensual estimada",
                    f"$ {int(_pension_men):,}",
                    help=f"Total ÷ ({85} − {int(_edad_jub_res)} años) ÷ 12 meses",
                )

    # ── TAB 5 — Inversiones ───────────────────────────────────────────────────
    with tab5:
        st.markdown("#### 📈 Inversiones")

        # ── Sub-sección: Activos Líquidos ─────────────────────────────────────
        st.markdown("##### 💵 Activos Líquidos")

        _TIPOS_LIQ = ["Cuenta corriente", "Cuenta vista", "Depósito a plazo",
                       "Fondo mutuo líquido", "Otro"]

        show_liq_form = st.session_state.get("c2_show_liq_form", False)

        if not show_liq_form:
            if st.button("➕ Agregar cuenta / depósito", use_container_width=True, key="btn_add_liq"):
                st.session_state["c2_show_liq_form"] = True
                st.session_state.pop("c2_edit_liq_id", None)
                st.rerun()

        # Lista activos líquidos
        liq_ids = state.list_positions(clase="Activo_Liquido")
        if liq_ids:
            for liq_id in liq_ids:
                liq_p = state.get_position(liq_id) or {}
                liq_desc = liq_p.get("Descripcion", liq_id)
                liq_tipo = liq_p.get("Tipo_Activo", liq_p.get("Tipo", ""))
                liq_saldo = float(liq_p.get("Saldo_Actual", 0))
                liq_moneda = liq_p.get("Moneda", "CLP")
                liq_venc = liq_p.get("Fecha_Vencimiento", "")

                _liq_saldo_str = (
                    f"UF {liq_saldo:,.2f}" if liq_moneda == "UF"
                    else f"USD {liq_saldo:,.0f}" if liq_moneda == "USD"
                    else f"$ {int(liq_saldo):,}"
                )

                c_liq1, c_liq2, c_liq3 = st.columns([5, 1, 1])
                with c_liq1:
                    _liq_detail = f"**{liq_desc}**"
                    if liq_tipo:
                        _liq_detail += f" · *{liq_tipo}*"
                    _liq_detail += f"  \nSaldo: {_liq_saldo_str}"
                    if liq_venc:
                        _liq_detail += f" · Vence: {liq_venc}"
                    st.markdown(_liq_detail)
                with c_liq2:
                    if liq_id != "ACT_LIQUIDO_PRINCIPAL":
                        if st.button("✏️", key=f"edit_liq_{liq_id}", help="Editar"):
                            st.session_state["c2_show_liq_form"] = True
                            st.session_state["c2_edit_liq_id"] = liq_id
                            st.rerun()
                with c_liq3:
                    if liq_id != "ACT_LIQUIDO_PRINCIPAL":
                        if st.button("🗑️", key=f"del_liq_{liq_id}", help="Eliminar"):
                            state.delete_position(liq_id)
                            state.mark_dirty()
                            st.rerun()

        # Total líquido y fondo de reserva
        _total_liq_tab = _activo_liq_total
        _meses_fondo = (_total_liq_tab / _ese) if _ese > 0 else 0
        _fondo_icon = "✅" if _meses_fondo >= 3 else "⚠️"
        st.caption(
            f"**Total líquido:** $ {int(_total_liq_tab):,}  ·  "
            f"**Fondo de reserva:** {_meses_fondo:.1f} meses {_fondo_icon} "
            f"(meta: 3 meses esenciales)"
        )

        # Formulario nuevo activo líquido
        if show_liq_form:
            edit_liq_id: str | None = st.session_state.get("c2_edit_liq_id")
            liq_edit_p = state.get_position(edit_liq_id) or {} if edit_liq_id else {}
            modo_liq = "✏️ Editar activo líquido" if edit_liq_id else "➕ Nuevo activo líquido"
            st.markdown(f"**{modo_liq}**")

            _mon_liq_opts = ["CLP", "UF", "USD"]
            _mon_liq_def = liq_edit_p.get("Moneda", "CLP")
            if _mon_liq_def not in _mon_liq_opts:
                _mon_liq_def = "CLP"
            liq_moneda_in = st.selectbox(
                "Moneda", _mon_liq_opts,
                index=_mon_liq_opts.index(_mon_liq_def),
                key="c2_liq_moneda",
            )

            with st.form("form_activo_liq", clear_on_submit=False):
                tipo_liq = st.selectbox(
                    "Tipo",
                    _TIPOS_LIQ,
                    index=_TIPOS_LIQ.index(liq_edit_p.get("Tipo_Activo", "Cuenta corriente"))
                          if liq_edit_p.get("Tipo_Activo", "Cuenta corriente") in _TIPOS_LIQ else 0,
                    key="c2_liq_tipo",
                )
                liq_inst_in = st.text_input(
                    "Institución",
                    value=liq_edit_p.get("Descripcion", ""),
                    key="c2_liq_inst",
                    placeholder="Ej: Banco BCI, BSANTANDER, Fintual…",
                )
                _step_liq = 1.0 if liq_moneda_in == "UF" else 100_000
                _fmt_liq = "%.2f" if liq_moneda_in == "UF" else "%.0f"
                liq_saldo_in = st.number_input(
                    f"Saldo actual ({liq_moneda_in})",
                    min_value=0.0,
                    value=float(liq_edit_p.get("Saldo_Actual", 0.0)),
                    step=float(_step_liq),
                    format=_fmt_liq,
                    key="c2_liq_saldo",
                )
                liq_venc_in: str | None = None
                if tipo_liq == "Depósito a plazo":
                    liq_venc_date = st.date_input(
                        "Fecha de vencimiento",
                        value=date.fromisoformat(
                            str(liq_edit_p.get("Fecha_Vencimiento", date.today().isoformat()))
                        ),
                        key="c2_liq_venc",
                    )
                    liq_venc_in = liq_venc_date.isoformat()

                cl_ok, cl_can = st.columns(2)
                with cl_ok:
                    liq_ok = st.form_submit_button(
                        "✓ Guardar", type="primary", use_container_width=True
                    )
                with cl_can:
                    liq_can = st.form_submit_button("✕ Cancelar", use_container_width=True)

                if liq_can:
                    st.session_state["c2_show_liq_form"] = False
                    st.session_state.pop("c2_edit_liq_id", None)
                    st.rerun()

                if liq_ok:
                    if not liq_inst_in.strip():
                        st.error("La institución no puede estar vacía.")
                        st.stop()
                    # Generate ID
                    if edit_liq_id:
                        liq_id_final = edit_liq_id
                    else:
                        _tipo_liq_key = tipo_liq.upper().replace(" ", "_")[:6]
                        _existing_liq = [
                            p for p in state.list_positions(clase="Activo_Liquido")
                            if p.startswith(f"ACT_LIQ_{_tipo_liq_key}")
                        ]
                        liq_id_final = f"ACT_LIQ_{_tipo_liq_key}_{len(_existing_liq) + 1:03d}"
                    liq_params = {
                        "Tipo_Activo": tipo_liq,
                        "Clase": "Activo_Liquido",
                        "Descripcion": liq_inst_in.strip(),
                        "Moneda": liq_moneda_in,
                        "Capa_Activacion": 2,
                        "Saldo_Actual": liq_saldo_in,
                    }
                    if liq_venc_in:
                        liq_params["Fecha_Vencimiento"] = liq_venc_in
                    state.set_position(liq_id_final, liq_params)
                    state.mark_dirty()
                    st.session_state["c2_show_liq_form"] = False
                    st.session_state.pop("c2_edit_liq_id", None)
                    st.rerun()

        st.markdown("---")
        st.markdown("##### 📈 Inversiones de largo plazo")

        _TIPOS_INV = ["Fondo Mutuo", "ETF", "Acciones", "Renta Fija", "Cripto", "Otro"]
        _TASAS_DEFAULT_INV = {
            "Fondo Mutuo": 5.0,
            "ETF": 7.0,
            "Acciones": 8.0,
            "Renta Fija": 4.0,
            "Cripto": 15.0,
            "Otro": 5.0,
        }

        show_inv_form = st.session_state.get("c2_show_inv_form", False)

        if not show_inv_form:
            if st.button("➕ Agregar inversión", use_container_width=True, key="btn_add_inv"):
                for _k in ["c2_inv_tipo", "c2_inv_desc", "c2_inv_saldo",
                           "c2_inv_aporte", "c2_inv_tasa", "c2_inv_moneda", "c2_inv_horizonte"]:
                    st.session_state.pop(_k, None)
                st.session_state["c2_show_inv_form"] = True
                st.session_state.pop("c2_edit_inv_id", None)
                st.rerun()

        # Lista inversiones registradas
        inv_ids = [
            p for p in state.list_positions()
            if p.startswith("ACT_INV_")
        ]

        if inv_ids:
            for inv_id in inv_ids:
                inv_p = state.get_position(inv_id) or {}
                inv_saldo = float(inv_p.get("Saldo_Actual", 0))
                inv_aporte = float(inv_p.get("Aporte_Mensual", 0))
                inv_desc = inv_p.get("Descripcion", inv_id)
                inv_tipo = inv_p.get("Tipo_Inversion", "")
                inv_horizonte = int(inv_p.get("Horizonte_Meses", 120))
                inv_tasa = float(inv_p.get("Tasa_Anual", 0.05))
                inv_moneda = inv_p.get("Moneda", "CLP")

                # Proyección al horizonte
                inv_tabla = st.session_state.get("schedules", {}).get(inv_id)
                inv_proy = (
                    float(inv_tabla["Saldo_Final"].iloc[-1])
                    if inv_tabla is not None and not inv_tabla.empty
                    else inv_saldo
                )

                c_inv1, c_inv2, c_inv3 = st.columns([5, 1, 1])
                with c_inv1:
                    st.markdown(
                        f"**{inv_desc}** · *{inv_tipo}*  \n"
                        f"Saldo {_fmt(inv_saldo)} · Proyección {_fmt(inv_proy)} "
                        f"(horizonte {inv_horizonte // 12} años)"
                    )
                    st.caption(f"Tasa esperada: {inv_tasa*100:.1f}% anual · Moneda: {inv_moneda}")
                with c_inv2:
                    if st.button("✏️", key=f"edit_inv_{inv_id}", help="Editar"):
                        st.session_state["c2_show_inv_form"] = True
                        st.session_state["c2_edit_inv_id"] = inv_id
                        st.rerun()
                with c_inv3:
                    if st.button("🗑️", key=f"del_inv_{inv_id}", help="Eliminar"):
                        state.delete_position(inv_id)
                        st.session_state.get("schedules", {}).pop(inv_id, None)
                        _atc: list = st.session_state.get("activos_con_tabla", [])
                        if inv_id in _atc:
                            _atc.remove(inv_id)
                        state.mark_dirty()
                        st.rerun()
        elif not show_inv_form:
            st.info("Sin inversiones registradas. Agrega fondos mutuos, ETFs, acciones u otros.")

        # Formulario inversión
        if show_inv_form:
            edit_inv_id: str | None = st.session_state.get("c2_edit_inv_id")
            inv_edit_p = state.get_position(edit_inv_id) or {} if edit_inv_id else {}
            modo_inv = "✏️ Editar inversión" if edit_inv_id else "➕ Nueva inversión"

            st.markdown(f"**{modo_inv}**")

            _tipo_inv_def = inv_edit_p.get("Tipo_Inversion", "Fondo Mutuo")
            if _tipo_inv_def not in _TIPOS_INV:
                _tipo_inv_def = "Fondo Mutuo"
            tipo_inv = st.selectbox(
                "Tipo de instrumento",
                _TIPOS_INV,
                index=_TIPOS_INV.index(_tipo_inv_def),
                key="c2_inv_tipo",
            )
            _tasa_def_inv = _TASAS_DEFAULT_INV.get(tipo_inv, 5.0)

            with st.form("form_inv", clear_on_submit=False):
                inv_desc_in = st.text_input(
                    "Descripción",
                    value=inv_edit_p.get("Descripcion", ""),
                    key="c2_inv_desc",
                    placeholder="Ej: FMXX Renta Fija, iShares S&P500…",
                )
                ci1, ci2 = st.columns(2)
                with ci1:
                    inv_saldo_in = st.number_input(
                        "Saldo actual (moneda)",
                        min_value=0.0, step=100_000.0,
                        value=float(inv_edit_p.get("Saldo_Actual", 0)),
                        key="c2_inv_saldo",
                    )
                    inv_aporte_in = st.number_input(
                        "Aporte mensual (moneda)",
                        min_value=0.0, step=10_000.0,
                        value=float(inv_edit_p.get("Aporte_Mensual", 0)),
                        key="c2_inv_aporte",
                    )
                with ci2:
                    inv_tasa_in = st.number_input(
                        "Tasa anual esperada (%)",
                        min_value=0.0, max_value=100.0,
                        value=float(inv_edit_p.get("Tasa_Anual_Pct", _tasa_def_inv)),
                        step=0.5, format="%.1f", key="c2_inv_tasa",
                    )
                    _mon_opts_inv = ["CLP", "UF", "USD"]
                    _mon_def_inv = inv_edit_p.get("Moneda", "CLP")
                    if _mon_def_inv not in _mon_opts_inv:
                        _mon_def_inv = "CLP"
                    inv_moneda_in = st.selectbox(
                        "Moneda",
                        _mon_opts_inv,
                        index=_mon_opts_inv.index(_mon_def_inv),
                        key="c2_inv_moneda",
                    )
                inv_horizonte_in = st.slider(
                    "Horizonte (años)",
                    min_value=1, max_value=40,
                    value=int(inv_edit_p.get("Horizonte_Meses", 120)) // 12,
                    key="c2_inv_horizonte",
                )

                ci_ok, ci_can = st.columns(2)
                with ci_ok:
                    inv_ok = st.form_submit_button(
                        "✓ Guardar inversión", type="primary", use_container_width=True
                    )
                with ci_can:
                    inv_can = st.form_submit_button("✕ Cancelar", use_container_width=True)

                if inv_can:
                    st.session_state["c2_show_inv_form"] = False
                    st.session_state.pop("c2_edit_inv_id", None)
                    st.rerun()

                if inv_ok:
                    if not inv_desc_in.strip():
                        st.error("La descripción no puede estar vacía.")
                        st.stop()

                    inv_id_final = edit_inv_id if edit_inv_id else _next_id_inversion_c2(tipo_inv)
                    _horizonte_meses_inv = inv_horizonte_in * 12

                    params_inv = {
                        "Tipo_Inversion": tipo_inv,
                        "Clase": "Activo_Financiero",
                        "Descripcion": inv_desc_in.strip(),
                        "Moneda": inv_moneda_in,
                        "Capa_Activacion": 2,
                        "Saldo_Actual": inv_saldo_in,
                        "Aporte_Mensual": inv_aporte_in,
                        "Tasa_Anual": inv_tasa_in / 100,
                        "Tasa_Anual_Pct": inv_tasa_in,
                        "Horizonte_Meses": _horizonte_meses_inv,
                        "Fecha_Inicio": date.today().isoformat(),
                    }
                    try:
                        tabla_inv_nueva = schedule.gen_fondo_inversion(
                            saldo=float(inv_saldo_in),
                            aporte_mensual=float(inv_aporte_in),
                            tasa_anual=inv_tasa_in / 100,
                            horizonte_meses=_horizonte_meses_inv,
                            fecha_inicio=date.today(),
                            moneda=inv_moneda_in,
                            id_posicion=inv_id_final,
                        )
                        state.set_position(inv_id_final, params_inv)
                        st.session_state["schedules"][inv_id_final] = tabla_inv_nueva
                        _atc2: list = st.session_state.setdefault("activos_con_tabla", [])
                        if inv_id_final not in _atc2:
                            _atc2.append(inv_id_final)
                        state.mark_dirty()
                        state.update_layer()
                        st.session_state["c2_show_inv_form"] = False
                        st.session_state.pop("c2_edit_inv_id", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Error al generar tabla de inversión: {exc}")

    # ── TAB 6 — Otros activos ─────────────────────────────────────────────────
    with tab6:
        st.markdown("#### 🏡 Otros activos reales")
        st.caption("Propiedades, vehículos u otros activos no asociados a hipotecas.")

        _TIPOS_ACT_REAL = ["Propiedad", "Vehículo", "Otro"]

        show_ar_form = st.session_state.get("c2_show_ar_form", False)

        if not show_ar_form:
            if st.button("➕ Agregar activo real", use_container_width=True, key="btn_add_ar"):
                st.session_state["c2_show_ar_form"] = True
                st.session_state.pop("c2_edit_ar_id", None)
                st.rerun()

        # Lista activos reales standalone (sin Pasivo_Asociado)
        ar_ids = [
            p for p in state.list_positions(clase="Activo_Real")
            if not (state.get_position(p) or {}).get("Pasivo_Asociado")
        ]

        if ar_ids:
            st.divider()
            for ar_id in ar_ids:
                ar_p = state.get_position(ar_id) or {}
                ar_desc = ar_p.get("Descripcion", ar_id)
                ar_tipo = ar_p.get("Tipo_Activo", "")
                ar_valor = float(ar_p.get("Valor_Comercial", 0))
                ar_moneda = ar_p.get("Moneda", "CLP")
                ar_ingreso = float(ar_p.get("Ingreso_Mensual", 0))
                ar_fecha = ar_p.get("Fecha_Valoracion", "—")

                c_ar1, c_ar2, c_ar3 = st.columns([5, 1, 1])
                with c_ar1:
                    _val_str = (
                        f"UF {ar_valor:,.2f}" if ar_moneda == "UF"
                        else f"USD {ar_valor:,.0f}" if ar_moneda == "USD"
                        else f"$ {int(ar_valor):,}"
                    )
                    _ing_str = (
                        f"UF {ar_ingreso:,.2f}" if ar_moneda == "UF"
                        else f"USD {ar_ingreso:,.0f}" if ar_moneda == "USD"
                        else f"$ {int(ar_ingreso):,}"
                    )
                    st.markdown(
                        f"**{ar_desc}** · *{ar_tipo}*  \n"
                        f"Valor {_val_str} · Ingreso mensual {_ing_str}  \n"
                        f"Valoración: {ar_fecha}"
                    )
                with c_ar2:
                    if st.button("✏️", key=f"edit_ar_{ar_id}", help="Editar"):
                        st.session_state["c2_show_ar_form"] = True
                        st.session_state["c2_edit_ar_id"] = ar_id
                        st.rerun()
                with c_ar3:
                    if st.button("🗑️", key=f"del_ar_{ar_id}", help="Eliminar"):
                        state.delete_position(ar_id)
                        state.mark_dirty()
                        st.rerun()
        elif not show_ar_form:
            st.info("Sin activos reales registrados. Agrega propiedades, vehículos u otros.")

        # Formulario activo real
        if show_ar_form:
            edit_ar_id: str | None = st.session_state.get("c2_edit_ar_id")
            ar_edit_p = state.get_position(edit_ar_id) or {} if edit_ar_id else {}
            modo_ar = "✏️ Editar activo" if edit_ar_id else "➕ Nuevo activo real"
            st.markdown(f"**{modo_ar}**")

            _tipo_ar_def = ar_edit_p.get("Tipo_Activo", "Propiedad")
            if _tipo_ar_def not in _TIPOS_ACT_REAL:
                _tipo_ar_def = "Propiedad"

            _mon_ar_opts = ["CLP", "UF", "USD"]
            _mon_ar_def = ar_edit_p.get("Moneda", "CLP")
            if _mon_ar_def not in _mon_ar_opts:
                _mon_ar_def = "CLP"
            ar_moneda_in = st.selectbox(
                "Moneda",
                _mon_ar_opts,
                index=_mon_ar_opts.index(_mon_ar_def),
                key="c2_ar_moneda",
            )

            with st.form("form_activo_real", clear_on_submit=False):
                tipo_ar = st.selectbox(
                    "Tipo de activo",
                    _TIPOS_ACT_REAL,
                    index=_TIPOS_ACT_REAL.index(_tipo_ar_def),
                    key="c2_ar_tipo",
                )
                ar_desc_in = st.text_input(
                    "Descripción",
                    value=ar_edit_p.get("Descripcion", ""),
                    key="c2_ar_desc",
                    placeholder="Ej: Depto. Providencia, Auto Toyota Yaris…",
                )
                c_ar_f1, c_ar_f2 = st.columns(2)
                with c_ar_f1:
                    _step_ar = 1.0 if ar_moneda_in == "UF" else 1_000_000
                    _fmt_ar = "%.2f" if ar_moneda_in == "UF" else "%.0f"
                    ar_valor_in = st.number_input(
                        f"Valor comercial ({ar_moneda_in})",
                        min_value=0.0,
                        value=float(ar_edit_p.get("Valor_Comercial", 0.0)),
                        step=float(_step_ar),
                        format=_fmt_ar,
                        key="c2_ar_valor",
                    )
                    ar_fecha_in = st.date_input(
                        "Fecha de valoración",
                        value=date.fromisoformat(
                            str(ar_edit_p.get("Fecha_Valoracion", date.today().isoformat()))
                        ),
                        key="c2_ar_fecha",
                    )
                with c_ar_f2:
                    _step_ing_ar = 1.0 if ar_moneda_in == "UF" else 10_000
                    ar_ingreso_in = st.number_input(
                        f"Ingreso mensual ({ar_moneda_in})",
                        min_value=0.0,
                        value=float(ar_edit_p.get("Ingreso_Mensual", 0.0)),
                        step=float(_step_ing_ar),
                        format=_fmt_ar,
                        key="c2_ar_ingreso",
                        help="Arriendo u otro ingreso mensual. Deja en 0 si no aplica.",
                    )

                c_ar_ok, c_ar_can = st.columns(2)
                with c_ar_ok:
                    ar_ok = st.form_submit_button(
                        "✓ Guardar activo", type="primary", use_container_width=True
                    )
                with c_ar_can:
                    ar_can = st.form_submit_button("✕ Cancelar", use_container_width=True)

                if ar_can:
                    st.session_state["c2_show_ar_form"] = False
                    st.session_state.pop("c2_edit_ar_id", None)
                    st.rerun()

                if ar_ok:
                    if not ar_desc_in.strip():
                        st.error("La descripción no puede estar vacía.")
                        st.stop()

                    ar_id_final = edit_ar_id if edit_ar_id else _next_id_activo_real()
                    state.set_position(ar_id_final, {
                        "Tipo_Activo": tipo_ar,
                        "Clase": "Activo_Real",
                        "Descripcion": ar_desc_in.strip(),
                        "Moneda": ar_moneda_in,
                        "Capa_Activacion": 2,
                        "Valor_Comercial": ar_valor_in,
                        "Fecha_Valoracion": ar_fecha_in.isoformat(),
                        "Ingreso_Mensual": ar_ingreso_in,
                    })
                    state.mark_dirty()
                    st.session_state["c2_show_ar_form"] = False
                    st.session_state.pop("c2_edit_ar_id", None)
                    st.rerun()

# ────────────────────────────────────────────────────────────────────────────
# MÉTRICAS — calculadas desde session_state; renderizadas en el placeholder
# ────────────────────────────────────────────────────────────────────────────

# Datos base de Capa 1 — normalizados a CLP para comparabilidad entre monedas
_ing = calculator.normalizar_a_clp(
    float(_pos("ING_PRINCIPAL").get("Monto_Mensual", 1)),
    _pos("ING_PRINCIPAL").get("Moneda", _MONEDA),
    _valor_uf, _valor_usd,
)
_ese = calculator.normalizar_a_clp(
    float(_pos("GAS_ESE_BUCKET").get("Monto_Mensual", 0)),
    _pos("GAS_ESE_BUCKET").get("Moneda", _MONEDA),
    _valor_uf, _valor_usd,
)
_liq = calculator.normalizar_a_clp(
    float(_pos("ACT_LIQUIDO_PRINCIPAL").get("Saldo_Actual", 0)),
    _pos("ACT_LIQUIDO_PRINCIPAL").get("Moneda", _MONEDA),
    _valor_uf, _valor_usd,
)

# Cuotas normalizadas a CLP (cada pasivo puede estar en UF, USD o CLP)
_cuotas_clp: list[float] = [
    calculator.normalizar_a_clp(
        _cuota_actual(pid),
        _pos(pid).get("Moneda", "CLP"),
        _valor_uf, _valor_usd,
    )
    for pid in _all_pasivo_ids()
]

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

# Pasivos financieros (solo para carga financiera y cobertura de deuda)
_TIPOS_FINANCIEROS = {"Hipotecario", "Crédito consumo", "Tarjeta"}
_pids_fin = [p for p in _all_pasivo_ids() if _pos(p).get("Tipo") in _TIPOS_FINANCIEROS]
_cuotas_fin_clp: list[float] = [
    calculator.normalizar_a_clp(
        _cuota_actual(p),
        _pos(p).get("Moneda", "CLP"),
        _valor_uf, _valor_usd,
    )
    for p in _pids_fin
]
_total_cuotas_fin = sum(_cuotas_fin_clp)

# Deuda total financiera (solo pasivos financieros, desde schedules)
_hoy_str_cob = date.today().strftime("%Y-%m")
_deuda_fin_total = 0.0
for _p_fin in _pids_fin:
    _tabla_fin = st.session_state.get("schedules", {}).get(_p_fin)
    if _tabla_fin is not None and not _tabla_fin.empty:
        _fut_fin = _tabla_fin[_tabla_fin["Periodo"] >= _hoy_str_cob]
        _saldo_fin = float(_fut_fin["Saldo_Inicial"].iloc[0]) if not _fut_fin.empty else 0.0
    else:
        _saldo_fin = float(_pos(_p_fin).get("Capital", 0))
    _deuda_fin_total += calculator.normalizar_a_clp(
        abs(_saldo_fin), _pos(_p_fin).get("Moneda", "CLP"), _valor_uf, _valor_usd
    )

with _metrics_ph.container():

    # ── Tipo de cambio (configuración manual) ─────────────────────────────────
    with st.expander("⚙️ Tipo de cambio", expanded=False):
        st.caption(
            "El sistema usa estos valores para convertir flujos en UF y USD a CLP "
            "en todas las métricas. Actualiza manualmente con el valor del día."
        )
        _col_uf, _col_usd = st.columns(2)
        with _col_uf:
            st.number_input(
                "Valor UF (CLP/UF)",
                min_value=1.0,
                value=float(st.session_state.get("valor_uf", 39_700.0)),
                step=100.0,
                format="%.0f",
                key="valor_uf",
                help="Fuente: Banco Central de Chile (www.bcentral.cl)",
            )
        with _col_usd:
            st.number_input(
                "Dólar USD (CLP/USD)",
                min_value=1.0,
                value=float(st.session_state.get("valor_usd", 950.0)),
                step=10.0,
                format="%.0f",
                key="valor_usd",
                help="Tipo de cambio observado del día.",
            )

    st.divider()

    # ── Métricas — cálculo de variables ───────────────────────────────────────
    # Carga financiera (solo pasivos financieros: Hipotecario, Crédito consumo, Tarjeta)
    if _ing > 0 and _cuotas_fin_clp:
        _cf = calculator.carga_financiera(_cuotas_fin_clp, _ing)
        _cf_pct = _cf * 100
        _cf_icon = "🟢" if _cf < 0.35 else ("🟡" if _cf < 0.50 else "🔴")
        _cf_label = "Saludable" if _cf < 0.35 else ("Precaución" if _cf < 0.50 else "Crítico")
        _cf_val = f"{_cf_icon} {_cf_pct:.1f}%"
        _cf_sub = f"{_cf_label} · Cuotas: $ {int(_total_cuotas_fin):,}/mes"
    elif _ing > 0:
        _cf_val = "🟢 0.0%"
        _cf_sub = "Sin compromisos financieros"
    else:
        _cf_val = "—"
        _cf_sub = "Define tu ingreso en Capa 1"

    # Posición de vida v2
    _denominador_pdv = _ese + sum(_cuotas_clp)
    if _denominador_pdv > 0:
        _pdv = calculator.posicion_vida_v2(_liq, _ese, _cuotas_clp)
        _pdv_icon = "🟢" if _pdv >= 3 else ("🟡" if _pdv >= 1 else "🔴")
        _pdv_val = f"{_pdv_icon} {_pdv:.1f} meses"
        _pdv_sub = "Liquidez / (Esenciales + Cuotas)"
    else:
        _pdv_val = "—"
        _pdv_sub = "Agrega pasivos y define esenciales"

    # Cobertura de deuda (activo = todas posiciones Activo_Liquido; deuda = solo financieros)
    if _deuda_fin_total > 0:
        _cob = calculator.cobertura_deuda(_activo_liq_total, _deuda_fin_total)
        _cob_pct = _cob * 100
        _cob_icon = "🟢" if _cob > 0.20 else ("🟡" if _cob > 0.10 else "🔴")
        _cob_val = f"{_cob_icon} {_cob_pct:.1f}%"
        _cob_sub = f"Liquidez: $ {int(_activo_liq_total):,} / Deuda fin."
    else:
        _cob_val = "—"
        _cob_sub = "Sin deuda financiera registrada"

    # Tasa de ahorro real
    _aporte_afp_m = float(
        (state.get_position("AFP_PRINCIPAL") or {}).get("Aporte_Mensual", 0)
    )
    _aporte_apv_m = sum(
        float((state.get_position(p) or {}).get("Aporte_Mensual", 0))
        for p in _all_apv_ids()
    )
    _aporte_inv_m = sum(
        float((state.get_position(p) or {}).get("Aporte_Mensual", 0))
        for p in state.list_positions(clase="Activo_Financiero")
        if p.startswith("ACT_INV_")
    )
    _ing_params_liv = _pos("ING_PRINCIPAL")
    if "Monto_Mensual" in _ing_params_liv:
        _ing_live = float(_ing_params_liv["Monto_Mensual"])
    elif "Ingreso_Mensual" in _ing_params_liv:
        _ing_live = float(_ing_params_liv["Ingreso_Mensual"])
    else:
        _ing_live = float(st.session_state.get("ingreso_mensual", st.session_state.get("live_ing", 0)))
    _ing_live = calculator.normalizar_a_clp(
        _ing_live,
        _ing_params_liv.get("Moneda", "CLP"),
        _valor_uf, _valor_usd,
    )
    if _ing_live > 0:
        _tasa = calculator.tasa_ahorro_real(
            _aporte_afp_m + _aporte_apv_m + _aporte_inv_m, _ing_live
        )
        _tasa_pct = _tasa * 100
        _tasa_icon = "🟢" if _tasa > 0.15 else ("🟡" if _tasa > 0.10 else "🔴")
        _ahr_val = f"{_tasa_icon} {_tasa_pct:.1f}%"
        _ahr_sub = "Inversión mensual / Ingreso"
    else:
        _ahr_val = "—"
        _ahr_sub = "Define tu ingreso en Capa 1"

    # Horizonte libre — formatear para display
    _MESES_CORTO = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    if _horizonte != "—":
        try:
            _y_h, _m_h = _horizonte.split("-")
            _horizonte_display = f"{_MESES_CORTO[int(_m_h) - 1]} {_y_h}"
        except Exception:
            _horizonte_display = _horizonte
    else:
        _horizonte_display = "Sin compromisos"
    _n_comp = len(_all_pasivo_ids())
    _h_sub = f"{_n_comp} compromiso{'s' if _n_comp != 1 else ''}" if _n_comp else "Agrega pasivos"

    # ── Métricas — tarjetas HTML/CSS ──────────────────────────────────────────
    st.markdown(f"""
<div style="display:flex;flex-direction:column;gap:12px;margin-bottom:8px;">
  <div style="display:flex;gap:12px;">
    <div style="flex:1;background:#1e2a3a;border-radius:8px;padding:20px;min-height:120px;">
      <div style="font-size:11px;color:#8fa0b0;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">CARGA FINANCIERA</div>
      <div style="font-size:36px;font-weight:700;color:#e8f0fe;">{_cf_val}</div>
      <div style="font-size:12px;color:#8fa0b0;margin-top:8px;">{_cf_sub}</div>
    </div>
    <div style="flex:1;background:#1e2a3a;border-radius:8px;padding:20px;min-height:120px;">
      <div style="font-size:11px;color:#8fa0b0;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">POSICIÓN DE VIDA</div>
      <div style="font-size:36px;font-weight:700;color:#e8f0fe;">{_pdv_val}</div>
      <div style="font-size:12px;color:#8fa0b0;margin-top:8px;">{_pdv_sub}</div>
    </div>
  </div>
  <div style="display:flex;gap:12px;">
    <div style="flex:1;background:#1a2230;border-radius:8px;padding:16px;">
      <div style="font-size:10px;color:#8fa0b0;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">COBERTURA DEUDA</div>
      <div style="font-size:24px;font-weight:700;color:#e8f0fe;">{_cob_val}</div>
      <div style="font-size:11px;color:#8fa0b0;margin-top:4px;">{_cob_sub}</div>
    </div>
    <div style="flex:1;background:#1a2230;border-radius:8px;padding:16px;">
      <div style="font-size:10px;color:#8fa0b0;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">TASA DE AHORRO</div>
      <div style="font-size:24px;font-weight:700;color:#e8f0fe;">{_ahr_val}</div>
      <div style="font-size:11px;color:#8fa0b0;margin-top:4px;">{_ahr_sub}</div>
    </div>
    <div style="flex:1;background:#1a2230;border-radius:8px;padding:16px;">
      <div style="font-size:10px;color:#8fa0b0;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">HORIZONTE LIBRE</div>
      <div style="font-size:20px;font-weight:700;color:#e8f0fe;">🏁 {_horizonte_display}</div>
      <div style="font-size:11px;color:#8fa0b0;margin-top:4px;">{_h_sub}</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

    st.divider()

    # ── Flujo mensual — gráfico de barras apiladas ────────────────────────────
    st.markdown("#### Flujo Mensual por Categoría")

    # Construir series de 24 meses
    _today_ch = date.today()
    _periodos_ch: list[str] = []
    _y_ch, _m_ch = _today_ch.year, _today_ch.month
    for _ in range(24):
        _periodos_ch.append(f"{_y_ch}-{_m_ch:02d}")
        _m_ch += 1
        if _m_ch > 12:
            _m_ch = 1
            _y_ch += 1

    _imp_ch = calculator.normalizar_a_clp(
        float(_pos("GAS_IMP_BUCKET").get("Monto_Mensual", 0)),
        _pos("GAS_IMP_BUCKET").get("Moneda", _MONEDA),
        _valor_uf, _valor_usd,
    )
    _asp_ch = calculator.normalizar_a_clp(
        float(_pos("GAS_ASP_BUCKET").get("Monto_Mensual", 0)),
        _pos("GAS_ASP_BUCKET").get("Moneda", _MONEDA),
        _valor_uf, _valor_usd,
    )

    # Deuda financiera por período (solo Hipotecario, Crédito consumo, Tarjeta)
    _deuda_ch: dict[str, float] = {}
    for _p_ch in _pids_fin:
        _tab_ch = st.session_state.get("schedules", {}).get(_p_ch)
        if _tab_ch is None or _tab_ch.empty:
            continue
        _mon_ch = _pos(_p_ch).get("Moneda", "CLP")
        for _per_ch in _periodos_ch:
            _row_ch = _tab_ch[_tab_ch["Periodo"] == _per_ch]
            if not _row_ch.empty:
                _cuota_ch = abs(float(_row_ch["Flujo_Periodo"].iloc[0]))
                _cuota_clp_ch = calculator.normalizar_a_clp(_cuota_ch, _mon_ch, _valor_uf, _valor_usd)
                _deuda_ch[_per_ch] = _deuda_ch.get(_per_ch, 0.0) + _cuota_clp_ch

    _ese_ser = [_ese] * 24
    _imp_ser = [_imp_ch] * 24
    _asp_ser = [_asp_ch] * 24
    _deuda_ser = [_deuda_ch.get(p, 0.0) for p in _periodos_ch]
    _exceso_ser = [
        _ing - (_ese_ser[i] + _imp_ser[i] + _asp_ser[i] + _deuda_ser[i])
        for i in range(24)
    ]

    if _ing > 0 or any(v > 0 for v in _deuda_ser):
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Esenciales", x=_periodos_ch, y=_ese_ser,
            marker_color="#1e4d8c",
            hovertemplate="%{x}<br>Esenciales: %{y:,.0f}<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Importantes", x=_periodos_ch, y=_imp_ser,
            marker_color="#2e6da4",
            hovertemplate="%{x}<br>Importantes: %{y:,.0f}<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Aspiraciones", x=_periodos_ch, y=_asp_ser,
            marker_color="#4a9fd4",
            hovertemplate="%{x}<br>Aspiraciones: %{y:,.0f}<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Deuda financiera", x=_periodos_ch, y=_deuda_ser,
            marker_color="#c0392b",
            hovertemplate="%{x}<br>Deuda fin.: %{y:,.0f}<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Exceso/Déficit", x=_periodos_ch, y=_exceso_ser,
            marker_color=["#27ae60" if v >= 0 else "#e74c3c" for v in _exceso_ser],
            hovertemplate="%{x}<br>Exceso/Déficit: %{y:,.0f}<extra></extra>",
        ))
        fig.update_layout(
            barmode="relative",
            height=260,
            margin=dict(l=0, r=0, t=8, b=0),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                        font=dict(size=9)),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)", zeroline=True,
                       zerolinecolor="#888"),
            xaxis=dict(tickangle=-45, tickfont=dict(size=9)),
        )
        if _ing > 0:
            fig.add_hline(
                y=_ing, line_dash="dot", line_color="white", line_width=1.5,
                annotation_text="Ingreso", annotation_font_color="white",
                annotation_font_size=9,
            )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        _meses_deficit = sum(1 for v in _exceso_ser if v < 0)
        if _meses_deficit:
            st.warning(f"⚠️ {_meses_deficit} meses en déficit en los próximos 24 meses.")
        else:
            st.success("✓ Sin meses en déficit en los próximos 24 meses.")
    else:
        st.caption("Agrega ingresos y compromisos para ver el flujo mensual.")



# ── Sugerencias pendientes ────────────────────────────────────────────────────

_sugerencias_c2 = st.session_state.get("sugerencias_pendientes", [])
if _sugerencias_c2:
    st.divider()
    st.markdown(f"#### 💡 Sugerencias de desagregación ({len(_sugerencias_c2)})")
    st.caption(
        "Vincula las cuotas de tus compromisos a los buckets de gasto "
        "para reflejar la realidad de tu presupuesto en Capa 1."
    )
    for _sug_c2 in list(_sugerencias_c2):
        _sug_id_c2 = _sug_c2["id"]
        _bucket_lbl_c2 = _BUCKET_LABELS.get(_sug_c2["bucket"], _sug_c2["bucket"])
        _monto_clp_c2 = f"$ {int(_sug_c2['monto']):,}"
        with st.container():
            _col_sug_c2, _col_btns_c2 = st.columns([4, 2])
            with _col_sug_c2:
                st.markdown(
                    f"**{_sug_c2['descripcion']}** · *{_sug_c2['tipo']}*  \n"
                    f"Vincular cuota **{_monto_clp_c2}/mes** → {_bucket_lbl_c2}"
                )
                if _sug_c2.get("excede_espacio", False):
                    st.warning(
                        f"⚠️ Tu {_sug_c2['tipo']} real es "
                        f"**$ {int(_sug_c2.get('exceso_clp', 0)):,}** "
                        f"mayor que lo disponible en {_bucket_lbl_c2}. "
                        "Aplicar ajustará el bucket automáticamente."
                    )
            with _col_btns_c2:
                if st.button(
                    "✓ Aplicar",
                    key=f"sug2_ap_{_sug_id_c2}",
                    type="primary",
                    use_container_width=True,
                ):
                    _aplicar_sugerencia(_sug_c2)
                    st.rerun()
                if st.button(
                    "✕ Descartar",
                    key=f"sug2_dc_{_sug_id_c2}",
                    use_container_width=True,
                ):
                    st.session_state["sugerencias_pendientes"] = [
                        s for s in st.session_state.get("sugerencias_pendientes", [])
                        if s["id"] != _sug_id_c2
                    ]
                    st.rerun()

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
