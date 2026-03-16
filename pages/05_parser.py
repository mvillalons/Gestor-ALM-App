"""
Capa 3-B — Importación de cartolas bancarias.
Parser universal: Itaú cuenta corriente, TC nacional/internacional,
y cualquier PDF o Excel de cualquier banco.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

# ── Imports del proyecto ─────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import core.state as state
from core.calculator import normalizar_a_clp

try:
    from parser.normalizer import extraer_movimientos, movimientos_a_dataframe
    from parser.llm_classifier import clasificar_movimientos
    from parser.models import Movimiento, PropuestaClasificacion
    from parser import drive_inbox as _drive_inbox
    _PARSER_OK = True
except ImportError as _e:
    _PARSER_OK = False
    _PARSER_ERROR = str(_e)

# ── Constantes ────────────────────────────────────────────────────────────────

_TIPOS_UPLOAD = ["pdf", "xlsx", "xls", "csv"]

_CLASES_EMOJI = {
    "Ingreso_Recurrente": "💰",
    "Pasivo_Estructural": "🏠",
    "Activo_Financiero": "📈",
    "Activo_Liquido": "🏦",
    "Activo_Real": "🏡",
    "Prevision_AFP": "🔵",
    "Objetivo_Ahorro": "🎯",
    "Otro": "🗂️",
}

_TIPOS_PASIVO_COMPROMISO = {"Colegio", "Jardín", "Arriendo", "Otro"}


def emoji_clase(pos: dict) -> str:
    """Retorna emoji según la clase de la posición."""
    clase = pos.get("Clase", "")
    if clase == "Pasivo_Corto_Plazo":
        tipo = pos.get("Tipo", pos.get("Tipo_Pasivo", ""))
        if tipo in _TIPOS_PASIVO_COMPROMISO:
            return "📚"
        return "💳"
    return _CLASES_EMOJI.get(clase, "🗂️")


# ── API Key ───────────────────────────────────────────────────────────────────

def _get_api_key() -> str | None:
    """Lee la API key de Anthropic desde secrets o env."""
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


# ── Helpers de session_state ──────────────────────────────────────────────────

def _pendientes() -> list[dict]:
    return st.session_state.setdefault("parser_movimientos_pendientes", [])


def _otros() -> list[dict]:
    return st.session_state.setdefault("movimientos_otros", [])


def _pos(pid: str) -> dict:
    return (state.get_position(pid) or {})


def _todas_posiciones() -> dict[str, dict]:
    """Retorna todas las posiciones del portafolio."""
    positions = st.session_state.get("positions", {})
    return {k: v for k, v in positions.items() if isinstance(v, dict)}


# ── Tipos de cambio ───────────────────────────────────────────────────────────

def _valor_uf() -> float:
    return float(st.session_state.get("valor_uf", 39700.0))


def _valor_usd() -> float:
    return float(st.session_state.get("valor_usd", 950.0))


# ── Lógica de procesamiento ───────────────────────────────────────────────────

def _prop_a_dict(prop: "PropuestaClasificacion") -> dict:
    """Serializa una PropuestaClasificacion a dict para session_state."""
    m = prop.movimiento
    return {
        "fecha": m.fecha,
        "descripcion": m.descripcion,
        "monto": m.monto,
        "moneda": m.moneda,
        "monto_clp": m.monto_clp,
        "fuente": m.fuente,
        "referencia": m.referencia,
        "confianza_extraccion": m.confianza_extraccion,
        "raw": m.raw,
        "id_posicion_sugerido": prop.id_posicion_sugerido,
        "confianza": prop.confianza,
        "justificacion": prop.justificacion,
        "estado": prop.estado,
    }


def _dict_a_prop(d: dict) -> "PropuestaClasificacion":
    """Deserializa un dict a PropuestaClasificacion."""
    m = Movimiento(
        fecha=d["fecha"],
        descripcion=d["descripcion"],
        monto=float(d["monto"]),
        moneda=d.get("moneda", "CLP"),
        monto_clp=float(d.get("monto_clp", d["monto"])),
        fuente=d.get("fuente", ""),
        referencia=d.get("referencia", ""),
        confianza_extraccion=float(d.get("confianza_extraccion", 1.0)),
        raw=d.get("raw", ""),
    )
    return PropuestaClasificacion(
        movimiento=m,
        id_posicion_sugerido=d.get("id_posicion_sugerido", "SIN_CLASIFICAR"),
        confianza=float(d.get("confianza", 0.0)),
        justificacion=d.get("justificacion", ""),
        tipo_flujo="importado",
        estado=d.get("estado", "pendiente"),
    )


def procesar_archivo(
    filepath: str,
    posiciones: dict,
    valor_usd: float,
    valor_uf: float,
    anthropic_key: str | None,
) -> None:
    """
    Extrae y clasifica movimientos de un archivo de cartola.
    Guarda resultados en session_state["parser_movimientos_pendientes"].
    """
    with st.spinner("Extrayendo movimientos..."):
        movimientos, formato = extraer_movimientos(
            filepath,
            valor_usd_clp=valor_usd,
            valor_uf_clp=valor_uf,
        )

    if not movimientos:
        st.warning("No se encontraron movimientos en el archivo.")
        return

    st.success(f"Formato detectado: **{formato}** — {len(movimientos)} movimientos extraídos")

    propuestas: list[PropuestaClasificacion]
    if anthropic_key:
        with st.spinner(f"Clasificando {len(movimientos)} movimientos con IA..."):
            propuestas = clasificar_movimientos(
                movimientos, posiciones, anthropic_key
            )
    else:
        # Sin API key: crear propuestas SIN_CLASIFICAR
        propuestas = [
            PropuestaClasificacion(
                movimiento=m,
                id_posicion_sugerido="SIN_CLASIFICAR",
                confianza=0.0,
                justificacion="Sin clasificación automática (API key no configurada)",
                tipo_flujo="importado",
                estado="pendiente",
            )
            for m in movimientos
        ]

    # Guardar en session_state
    st.session_state["parser_movimientos_pendientes"] = [
        _prop_a_dict(p) for p in propuestas
    ]
    st.session_state["parser_ultimo_archivo"] = Path(filepath).name
    state.mark_dirty()


def aprobar_movimiento(idx: int) -> None:
    """
    Aprueba un movimiento de la lista de pendientes.
    - Si tiene posición válida: agrega como flujo_puntual en schedules
    - Si es SIN_CLASIFICAR u OTR_NO_CLASIFICADO: va a movimientos_otros
    """
    pendientes = _pendientes()
    if idx >= len(pendientes):
        return

    d = pendientes[idx]
    id_pos = d.get("id_posicion_sugerido", "SIN_CLASIFICAR")

    from datetime import datetime
    fecha_imp = datetime.today().strftime("%Y-%m-%d")

    if id_pos not in ("SIN_CLASIFICAR", "OTR_NO_CLASIFICADO", ""):
        # Agregar como flujo_puntual en la tabla de desarrollo
        schedules = st.session_state.setdefault("schedules", {})
        if id_pos not in schedules or schedules[id_pos] is None:
            import pandas as pd
            schedules[id_pos] = pd.DataFrame(columns=[
                "ID_Posicion", "Periodo", "Saldo_Inicial", "Flujo_Periodo",
                "Rendimiento_Costo", "Amortizacion", "Saldo_Final",
                "Moneda", "Tipo_Flujo", "Notas",
            ])

        import pandas as pd
        nueva_fila = pd.DataFrame([{
            "ID_Posicion": id_pos,
            "Periodo": d["fecha"][:7],  # YYYY-MM
            "Saldo_Inicial": 0.0,
            "Flujo_Periodo": float(d["monto_clp"]),
            "Rendimiento_Costo": 0.0,
            "Amortizacion": 0.0,
            "Saldo_Final": 0.0,
            "Moneda": "CLP",
            "Tipo_Flujo": "importado",
            "Notas": d["descripcion"][:100],
        }])
        schedules[id_pos] = pd.concat(
            [schedules[id_pos], nueva_fila], ignore_index=True
        )
    else:
        # Enviar a movimientos_otros
        _otros().append({
            "fecha": d["fecha"],
            "descripcion": d["descripcion"],
            "monto": d["monto"],
            "moneda": d.get("moneda", "CLP"),
            "monto_clp": d.get("monto_clp", d["monto"]),
            "fuente": d.get("fuente", ""),
            "motivo_descarte": "sin_clasificar",
            "fecha_importacion": fecha_imp,
        })

    pendientes.pop(idx)
    state.mark_dirty()


def descartar_movimiento(idx: int) -> None:
    """Descarta un movimiento — lo mueve a movimientos_otros."""
    pendientes = _pendientes()
    if idx >= len(pendientes):
        return

    d = pendientes[idx]
    from datetime import datetime
    _otros().append({
        "fecha": d["fecha"],
        "descripcion": d["descripcion"],
        "monto": d["monto"],
        "moneda": d.get("moneda", "CLP"),
        "monto_clp": d.get("monto_clp", d["monto"]),
        "fuente": d.get("fuente", ""),
        "motivo_descarte": "descartado",
        "fecha_importacion": datetime.today().strftime("%Y-%m-%d"),
    })

    pendientes.pop(idx)
    state.mark_dirty()


def aprobar_todos_alta_confianza() -> int:
    """
    Aprueba todos los movimientos con confianza >= 0.8 y posición definida.
    Retorna el número de movimientos aprobados.
    """
    pendientes = _pendientes()
    indices_aprobar = [
        i for i, d in enumerate(pendientes)
        if float(d.get("confianza", 0)) >= 0.8
        and d.get("id_posicion_sugerido", "SIN_CLASIFICAR") not in (
            "SIN_CLASIFICAR", "OTR_NO_CLASIFICADO", ""
        )
    ]
    # Aprobar en orden inverso para no desincronizar índices
    for i in reversed(indices_aprobar):
        aprobar_movimiento(i)
    return len(indices_aprobar)


# ── Formateo ─────────────────────────────────────────────────────────────────

def _fmt_monto(monto: float, moneda: str, monto_clp: float) -> str:
    """Formatea monto con color."""
    color = "#4CAF50" if monto >= 0 else "#EF5350"
    signo = "+" if monto >= 0 else ""
    if moneda != "CLP":
        return (
            f'<span style="color:{color}">{signo}{monto:,.0f} {moneda}</span>'
            f'<br><small style="color:#888">≈ $ {monto_clp:,.0f}</small>'
        )
    return f'<span style="color:{color}">{signo}$ {monto_clp:,.0f}</span>'


def _badge_confianza(conf: float) -> str:
    if conf >= 0.8:
        return f'<span style="background:#1b5e20;color:#a5d6a7;padding:2px 6px;border-radius:4px;font-size:11px">🟢 {conf:.0%}</span>'
    if conf >= 0.5:
        return f'<span style="background:#4a3900;color:#ffe082;padding:2px 6px;border-radius:4px;font-size:11px">🟡 {conf:.0%}</span>'
    return f'<span style="background:#4e0000;color:#ef9a9a;padding:2px 6px;border-radius:4px;font-size:11px">🔴 {conf:.0%}</span>'


def _opciones_posicion(posiciones: dict) -> list[tuple[str, str]]:
    """
    Retorna lista de (id, label) ordenada por clase para el selectbox.
    """
    orden_clase = {
        "Ingreso_Recurrente": 0,
        "Pasivo_Estructural": 1,
        "Pasivo_Corto_Plazo": 2,
        "Activo_Liquido": 3,
        "Activo_Financiero": 4,
        "Activo_Real": 5,
        "Prevision_AFP": 6,
        "Objetivo_Ahorro": 7,
        "Otro": 8,
    }
    items = []
    for pid, params in posiciones.items():
        if pid.startswith("GAS_") or pid == "OTR_NO_CLASIFICADO":
            continue
        clase = params.get("Clase", "Otro")
        desc = params.get("Descripcion", pid)
        em = emoji_clase(params)
        label = f"{em} {desc} [{pid}]"
        items.append((pid, label, orden_clase.get(clase, 9)))

    items.sort(key=lambda x: (x[2], x[1]))
    result = [(pid, lbl) for pid, lbl, _ in items]
    result.append(("SIN_CLASIFICAR", "❓ Sin clasificar"))
    result.append(("OTR_NO_CLASIFICADO", "🗂️ Agregar a Otros"))
    return result


# ── UI — Sección de carga ─────────────────────────────────────────────────────

def _render_subir_archivo(posiciones: dict, api_key: str | None) -> None:
    """Tab de subida de archivo local."""
    uploaded = st.file_uploader(
        "Sube tu cartola bancaria",
        type=_TIPOS_UPLOAD,
        help="Formatos soportados: PDF (Itaú y otros bancos), Excel (.xlsx/.xls), CSV",
    )
    if uploaded is None:
        st.caption(
            "Soportado: Cartola Itaú cuenta corriente, "
            "TC Nacional, TC Internacional, y cualquier PDF/Excel/CSV de banco chileno."
        )
        return

    suffix = Path(uploaded.name).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(uploaded.read())
        tmp_path = tmp.name

    try:
        procesar_archivo(tmp_path, posiciones, _valor_usd(), _valor_uf(), api_key)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    st.rerun()


def _render_inbox_drive(posiciones: dict, api_key: str | None) -> None:
    """Tab del inbox en Drive."""
    drive_client = st.session_state.get("drive_client")
    if drive_client is None:
        st.info("Conecta Google Drive primero para acceder al Inbox.")
        return

    archivos = _drive_inbox.listar_inbox(drive_client)
    if not archivos:
        st.info(
            "El inbox está vacío.\n\n"
            "Para importar cartolas desde Drive:\n"
            "1. Abre Google Drive\n"
            "2. Navega a `/ALM_Data/Inbox/`\n"
            "3. Sube tus archivos PDF o Excel\n"
            "4. Vuelve aquí y haz clic en **Procesar**"
        )
        return

    st.caption(f"{len(archivos)} archivo(s) disponibles en el Inbox")
    for arch in archivos:
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(f"📄 **{arch.get('name', 'archivo')}**")
        with c2:
            if st.button("Procesar", key=f"inbox_{arch.get('id', '')}"):
                file_id = arch.get("id", "")
                file_name = arch.get("name", "archivo.pdf")
                suffix = Path(file_name).suffix or ".pdf"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    drive_client.download_file(file_id, tmp_path)
                    procesar_archivo(tmp_path, posiciones, _valor_usd(), _valor_uf(), api_key)
                    _drive_inbox.mover_a_procesados(drive_client, file_id, file_name)
                    st.success(f"✓ {file_name} procesado y movido a Procesados")
                except Exception as exc:
                    st.error(f"Error: {exc}")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                st.rerun()


# ── UI — Tabla de movimientos pendientes ─────────────────────────────────────

def _render_pendientes(posiciones: dict) -> None:
    """Muestra la tabla de movimientos pendientes de revisión."""
    pendientes = _pendientes()
    if not pendientes:
        return

    # Estadísticas
    n_clasif = sum(
        1 for d in pendientes
        if d.get("id_posicion_sugerido", "SIN_CLASIFICAR") not in (
            "SIN_CLASIFICAR", ""
        ) and float(d.get("confianza", 0)) >= 0.8
    )
    n_revision = sum(
        1 for d in pendientes
        if d.get("id_posicion_sugerido", "SIN_CLASIFICAR") not in (
            "SIN_CLASIFICAR", ""
        ) and 0.5 <= float(d.get("confianza", 0)) < 0.8
    )
    n_sin = sum(
        1 for d in pendientes
        if d.get("id_posicion_sugerido", "SIN_CLASIFICAR") in ("SIN_CLASIFICAR", "")
        or float(d.get("confianza", 0)) < 0.5
    )

    st.markdown(f"""
<div style="display:flex; gap:12px; margin-bottom:16px; flex-wrap:wrap;">
  <div style="background:#1b5e20;color:#a5d6a7;padding:8px 16px;border-radius:8px;font-weight:bold">
    ✅ {n_clasif} clasificados
  </div>
  <div style="background:#4a3900;color:#ffe082;padding:8px 16px;border-radius:8px;font-weight:bold">
    ⚠️ {n_revision} revisar
  </div>
  <div style="background:#37474f;color:#b0bec5;padding:8px 16px;border-radius:8px;font-weight:bold">
    ❓ {n_sin} sin clasificar
  </div>
</div>
""", unsafe_allow_html=True)

    if n_clasif > 0:
        if st.button(
            f"✅ Aprobar todos los clasificados con conf ≥ 80% ({n_clasif})",
            use_container_width=True,
            type="primary",
        ):
            n = aprobar_todos_alta_confianza()
            st.success(f"✓ {n} movimientos aprobados")
            st.rerun()

    st.divider()

    opciones = _opciones_posicion(posiciones)
    opts_ids = [o[0] for o in opciones]
    opts_labels = [o[1] for o in opciones]

    # Renderizar cada movimiento
    indices_a_eliminar = []
    for i, d in enumerate(pendientes):
        id_pos = d.get("id_posicion_sugerido", "SIN_CLASIFICAR")
        confianza = float(d.get("confianza", 0.0))
        moneda = d.get("moneda", "CLP")
        monto = float(d.get("monto", 0))
        monto_clp = float(d.get("monto_clp", monto))

        with st.container():
            c_fecha, c_desc, c_monto, c_pos, c_acc = st.columns([1, 3, 2, 3, 1])

            with c_fecha:
                try:
                    from datetime import datetime
                    dt = datetime.strptime(d["fecha"], "%Y-%m-%d")
                    st.caption(dt.strftime("%d/%m/%Y"))
                except Exception:
                    st.caption(d.get("fecha", ""))

            with c_desc:
                desc = d.get("descripcion", "")
                st.caption(desc[:40] + ("…" if len(desc) > 40 else ""))

            with c_monto:
                st.markdown(
                    _fmt_monto(monto, moneda, monto_clp),
                    unsafe_allow_html=True,
                )

            with c_pos:
                if id_pos not in ("SIN_CLASIFICAR", "OTR_NO_CLASIFICADO", ""):
                    pos_data = posiciones.get(id_pos, {})
                    pos_desc = pos_data.get("Descripcion", id_pos)
                    em = emoji_clase(pos_data)
                    st.markdown(
                        f"{em} {pos_desc[:25]}<br>"
                        + _badge_confianza(confianza),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("⚠️ Sin clasificar", unsafe_allow_html=True)

                # Selectbox para cambiar asignación
                idx_default = opts_ids.index(id_pos) if id_pos in opts_ids else len(opts_ids) - 1
                nueva_sel = st.selectbox(
                    "Asignar a",
                    options=opts_labels,
                    index=idx_default,
                    key=f"sel_pos_{i}",
                    label_visibility="collapsed",
                )
                nuevo_id = opts_ids[opts_labels.index(nueva_sel)]
                if nuevo_id != id_pos:
                    pendientes[i]["id_posicion_sugerido"] = nuevo_id
                    pendientes[i]["confianza"] = 1.0 if nuevo_id not in ("SIN_CLASIFICAR", "OTR_NO_CLASIFICADO") else 0.0

            with c_acc:
                if st.button("✅", key=f"apro_{i}", help="Aprobar"):
                    aprobar_movimiento(i)
                    st.rerun()
                if st.button("✕", key=f"desc_{i}", help="Descartar"):
                    descartar_movimiento(i)
                    st.rerun()

        st.divider()


# ── UI — Sección "Otros" ─────────────────────────────────────────────────────

def _render_otros(posiciones: dict) -> None:
    """Expander con movimientos en la lista 'Otros'."""
    otros = _otros()
    n = len(otros)

    with st.expander(f"🗂️ Movimientos sin clasificar ({n} ítems)", expanded=False):
        if not otros:
            st.caption("No hay movimientos descartados o sin clasificar.")
            return

        opciones = _opciones_posicion(posiciones)
        opts_ids = [o[0] for o in opciones]
        opts_labels = [o[1] for o in opciones]

        for i, d in enumerate(otros):
            c1, c2, c3, c4, c5 = st.columns([1, 3, 2, 1, 1])
            with c1:
                try:
                    from datetime import datetime
                    dt = datetime.strptime(d["fecha"], "%Y-%m-%d")
                    st.caption(dt.strftime("%d/%m/%Y"))
                except Exception:
                    st.caption(d.get("fecha", ""))
            with c2:
                desc = d.get("descripcion", "")
                st.caption(desc[:40] + ("…" if len(desc) > 40 else ""))
            with c3:
                monto = float(d.get("monto_clp", d.get("monto", 0)))
                color = "#4CAF50" if monto >= 0 else "#EF5350"
                st.markdown(
                    f'<span style="color:{color}">$ {monto:,.0f}</span>',
                    unsafe_allow_html=True,
                )
            with c4:
                motivo = d.get("motivo_descarte", "")
                st.caption("sin clasificar" if motivo == "sin_clasificar" else "descartado")
            with c5:
                col_r, col_e = st.columns(2)
                with col_r:
                    if st.button("↩️", key=f"reclas_{i}", help="Reclasificar"):
                        st.session_state[f"reclas_show_{i}"] = True
                with col_e:
                    if st.button("🗑️", key=f"del_otro_{i}", help="Eliminar"):
                        otros.pop(i)
                        state.mark_dirty()
                        st.rerun()

            # Reclasificación inline
            if st.session_state.get(f"reclas_show_{i}"):
                sel = st.selectbox(
                    "Nueva posición",
                    options=opts_labels,
                    key=f"reclas_sel_{i}",
                )
                c_ok, c_can = st.columns(2)
                with c_ok:
                    if st.button("✓ Confirmar", key=f"reclas_ok_{i}", type="primary"):
                        nuevo_id = opts_ids[opts_labels.index(sel)]
                        if nuevo_id not in ("SIN_CLASIFICAR", "OTR_NO_CLASIFICADO", ""):
                            # Crear propuesta y aprobar
                            mov = Movimiento(
                                fecha=d["fecha"],
                                descripcion=d["descripcion"],
                                monto=float(d["monto"]),
                                moneda=d.get("moneda", "CLP"),
                                monto_clp=float(d.get("monto_clp", d["monto"])),
                                fuente=d.get("fuente", "reclasificado"),
                                referencia="",
                                confianza_extraccion=1.0,
                                raw="",
                            )
                            prop_d = _prop_a_dict(PropuestaClasificacion(
                                movimiento=mov,
                                id_posicion_sugerido=nuevo_id,
                                confianza=1.0,
                                justificacion="Reclasificado manualmente",
                            ))
                            _pendientes().append(prop_d)
                            otros.pop(i)
                        st.session_state.pop(f"reclas_show_{i}", None)
                        state.mark_dirty()
                        st.rerun()
                with c_can:
                    if st.button("✕", key=f"reclas_can_{i}"):
                        st.session_state.pop(f"reclas_show_{i}", None)
                        st.rerun()

            st.divider()


# ── UI — Footer ───────────────────────────────────────────────────────────────

def _render_footer() -> None:
    """Botón de guardado."""
    if not st.session_state.get("dirty", False):
        return

    st.divider()
    if st.button("💾 Guardar en Drive", type="primary", use_container_width=True):
        drive_client = st.session_state.get("drive_client")
        if drive_client is None:
            st.warning("Conecta Google Drive para guardar.")
            return
        try:
            # Guardar flujos aprobados (ya en schedules)
            # Guardar movimientos_otros
            import pandas as pd
            otros = _otros()
            if otros:
                df_otros = pd.DataFrame(otros)
                drive_client.upload_dataframe(
                    df_otros, "Otros/movimientos_sin_clasificar.csv"
                )
            st.success("✓ Guardado en Drive")
            state.mark_clean()
            st.rerun()
        except Exception as exc:
            st.error(f"Error al guardar: {exc}")


# ── PÁGINA PRINCIPAL ──────────────────────────────────────────────────────────

st.title("📥 Importar cartola")
st.caption(
    "Importa tus cartolas bancarias (PDF, Excel o CSV) y asigna cada "
    "movimiento a una posición de tu portafolio."
)

if not _PARSER_OK:
    st.error(
        f"Error al cargar el módulo parser: {_PARSER_ERROR}\n\n"
        "Instala las dependencias: `pip install pdfplumber openpyxl`"
    )
    st.stop()

# API key
api_key = _get_api_key()
if not api_key:
    st.warning(
        "⚠️ **Clasificación automática deshabilitada** — no se encontró ANTHROPIC_API_KEY.\n\n"
        "Puedes importar y clasificar manualmente. Para habilitar la IA:\n"
        "- En local: `export ANTHROPIC_API_KEY=sk-ant-...`\n"
        "- En Streamlit Cloud: agrega `ANTHROPIC_API_KEY` en Secrets"
    )

# Posiciones del portafolio
posiciones = _todas_posiciones()

# ── SECCIÓN 1 — Subir archivo ─────────────────────────────────────────────────
st.markdown("### 1 — Subir archivo")
tab_upload, tab_inbox = st.tabs(["📁 Subir archivo", "☁️ Inbox en Drive"])

with tab_upload:
    _render_subir_archivo(posiciones, api_key)

with tab_inbox:
    _render_inbox_drive(posiciones, api_key)

# ── SECCIÓN 2 — Resultados ────────────────────────────────────────────────────
pendientes = _pendientes()
if pendientes:
    st.markdown("---")
    archivo_nombre = st.session_state.get("parser_ultimo_archivo", "archivo")
    st.markdown(f"### 2 — Revisar {len(pendientes)} movimientos de **{archivo_nombre}**")
    _render_pendientes(posiciones)

# ── SECCIÓN 3 — Otros ─────────────────────────────────────────────────────────
otros = _otros()
if otros:
    st.markdown("---")
    _render_otros(posiciones)

# ── SECCIÓN 4 — Footer ────────────────────────────────────────────────────────
_render_footer()
