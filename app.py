"""app.py — Entrada principal y enrutador del Gestor ALM.

Responsabilidades:
  1. Única llamada a ``st.set_page_config()`` para toda la app.
  2. Inicializar el session_state antes de cualquier renderizado.
  3. Cargar datos desde Drive UNA SOLA VEZ por sesión (flag ``drive_loaded``).
  4. Construir el menú lateral dinámicamente según la capa desbloqueada.
  5. Mostrar el ``status_label()`` de sincronización en el sidebar siempre.
  6. Delegar la ejecución de la página activa a ``pg.run()``.

Carga inicial desde Drive:
  - Solo se ejecuta si ``token.json`` existe (usuario ya autenticó).
  - Protegida por el flag ``drive_loaded`` en session_state para no
    repetirse en cada rerun de Streamlit.
  - Si Drive falla (sin conexión, token expirado), muestra un warning en el
    sidebar y continúa con los datos en memoria. No bloquea la navegación.

Menú progresivo:
  Sin onboarding completado → solo "👋 Bienvenido"
  Capa 1 (siempre)         → "🏠 Mi Resumen"
  Capa 2+                  → "📋 Mis Compromisos"
  Capa 3+                  → "📈 Mi Crecimiento"
  Capa 4+                  → "🏛️ Modo Pro"
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from core import drive, state

# ── 1. Configuración global — debe ser el primer comando Streamlit ────────────
st.set_page_config(
    page_title="Gestor ALM",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 2. Estado de sesión — inicializar con defaults ────────────────────────────
state.init_session_state()

# ── 3. Carga inicial desde Drive (solo la primera vez en la sesión) ───────────
if not st.session_state.get("drive_loaded", False):
    if os.path.exists(drive.TOKEN_PATH):
        try:
            _svc = drive.authenticate_drive()
            _folders = drive.ensure_folder_structure(_svc)
            _positions = drive.load_positions(_svc, _folders)

            if _positions:
                st.session_state["positions"] = _positions
                st.session_state["onboarding_complete"] = True

                # Cargar tablas de desarrollo para pasivos y AFP
                _schedules: dict = {}
                _pasivos_con_tabla: list[str] = []

                for _pid, _pparams in _positions.items():
                    if _pid.startswith("PAS_"):
                        _tabla = drive.load_schedule(_svc, _folders, _pid)
                        if _tabla is not None and not _tabla.empty:
                            _schedules[_pid] = _tabla
                            _pasivos_con_tabla.append(_pid)
                    elif _pid.startswith("AFP_"):
                        _tabla = drive.load_schedule(_svc, _folders, _pid)
                        if _tabla is not None and not _tabla.empty:
                            _schedules[_pid] = _tabla
                        # Restaurar afp_saldo para el cálculo de capas
                        _saldo_afp = _pparams.get("Saldo_Actual")
                        if _saldo_afp is not None:
                            st.session_state["afp_saldo"] = float(_saldo_afp)

                if _schedules:
                    st.session_state["schedules"] = _schedules
                if _pasivos_con_tabla:
                    st.session_state["pasivos_con_tabla"] = _pasivos_con_tabla

                # Recalcular capa desbloqueada con el estado restaurado
                state.update_layer()

                # Marcar como sincronizado — los datos coinciden con Drive
                state.mark_clean(datetime.now(tz=timezone.utc))

        except Exception as _exc:  # noqa: BLE001
            # Drive no disponible o token expirado → continuar con memoria
            st.session_state["_drive_load_error"] = str(_exc)

    # Flag: no volver a intentar la carga en reruns posteriores
    st.session_state["drive_loaded"] = True

# ── 4. Leer estado — después de la posible carga desde Drive ──────────────────
onboarding_done: bool = bool(st.session_state.get("onboarding_complete", False))
layer: int = state.get_layer()

# ── 5. Sidebar: branding + status + alerta de Drive ───────────────────────────
with st.sidebar:
    st.markdown("### 📊 Gestor ALM")

    lbl = state.status_label()
    if "Sincronizado" in lbl:
        st.success(lbl)
    elif "Cambios" in lbl:
        st.warning(lbl)
    else:
        st.caption(lbl)

    # Mostrar error de carga UNA SOLA VEZ y limpiar (pop evita que persista)
    _load_err: str | None = st.session_state.pop("_drive_load_error", None)
    if _load_err:
        st.warning(
            f"⚠️ Drive no disponible: {_load_err[:100]}\n\n"
            "Continuando con datos en memoria."
        )

    st.divider()

    if onboarding_done:
        nombre = st.session_state.get("nombre_usuario", "")
        if nombre:
            st.caption(f"👤 {nombre}")
        st.caption(f"Capa desbloqueada: **{layer} / 4**")

# ── 6. Lista de páginas según estado ──────────────────────────────────────────
if not onboarding_done:
    _pages = [
        st.Page(
            "pages/01_onboarding.py",
            title="Bienvenido",
            icon="👋",
            default=True,
        ),
    ]
else:
    _pages = [
        st.Page(
            "pages/02_capa1_claridad.py",
            title="Mi Resumen",
            icon="🏠",
            default=True,
        ),
    ]
    if layer >= 2 and os.path.exists("pages/03_capa2_control.py"):
        _pages.append(
            st.Page(
                "pages/03_capa2_control.py",
                title="Mis Compromisos",
                icon="📋",
            )
        )
    if layer >= 3 and os.path.exists("pages/04_capa3_crecimiento.py"):
        _pages.append(
            st.Page(
                "pages/04_capa3_crecimiento.py",
                title="Mi Crecimiento",
                icon="📈",
            )
        )
    if layer >= 4 and os.path.exists("pages/05_capa4_pro.py"):
        _pages.append(
            st.Page(
                "pages/05_capa4_pro.py",
                title="Modo Pro",
                icon="🏛️",
            )
        )

# ── 7. Navegación ──────────────────────────────────────────────────────────────
pg = st.navigation(_pages, position="sidebar")

# CSS: simular layout="centered" durante el onboarding (app usa "wide")
if not onboarding_done:
    st.markdown(
        """
        <style>
        section.main > div.block-container {
            max-width: 780px;
            padding-left: 2rem;
            padding-right: 2rem;
            margin-left: auto;
            margin-right: auto;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

# ── 8. Ejecutar la página activa ──────────────────────────────────────────────
pg.run()
