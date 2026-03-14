"""app.py — Entrada principal y enrutador del Gestor ALM.

Responsabilidades:
  1. Única llamada a ``st.set_page_config()`` para toda la app.
  2. Inicializar el session_state antes de cualquier renderizado.
  3. Construir el menú lateral dinámicamente según la capa desbloqueada.
  4. Mostrar el ``status_label()`` de sincronización en el sidebar siempre.
  5. Delegar la ejecución de la página activa a ``pg.run()``.

Menú progresivo:
  Sin onboarding completado → solo "👋 Bienvenido"
  Capa 1 (siempre)         → "🏠 Mi Resumen"
  Capa 2+                  → "📋 Mis Compromisos"
  Capa 3+                  → "📈 Mi Crecimiento"
  Capa 4+                  → "🏛️ Modo Pro"
"""

from __future__ import annotations

import streamlit as st

from core import state

# ── 1. Configuración global — debe ser el primer comando Streamlit ────────────
st.set_page_config(
    page_title="Gestor ALM",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 2. Estado de sesión ────────────────────────────────────────────────────────
state.init_session_state()

onboarding_done: bool = bool(st.session_state.get("onboarding_complete", False))
layer: int = state.get_layer()

# ── 3. Sidebar: branding + status (visible en todas las páginas) ───────────────
with st.sidebar:
    st.markdown("### 📊 Gestor ALM")

    lbl = state.status_label()
    if "Sincronizado" in lbl:
        st.success(lbl)
    elif "Cambios" in lbl:
        st.warning(lbl)
    else:
        st.caption(lbl)

    st.divider()

    if onboarding_done:
        nombre = st.session_state.get("nombre_usuario", "")
        if nombre:
            st.caption(f"👤 {nombre}")
        st.caption(f"Capa desbloqueada: **{layer} / 4**")

# ── 4. Lista de páginas según estado ──────────────────────────────────────────
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
    if layer >= 2:
        _pages.append(
            st.Page(
                "pages/03_capa2_control.py",
                title="Mis Compromisos",
                icon="📋",
            )
        )
    if layer >= 3:
        _pages.append(
            st.Page(
                "pages/04_capa3_crecimiento.py",
                title="Mi Crecimiento",
                icon="📈",
            )
        )
    if layer >= 4:
        _pages.append(
            st.Page(
                "pages/05_capa4_pro.py",
                title="Modo Pro",
                icon="🏛️",
            )
        )

# ── 5. Navegación ──────────────────────────────────────────────────────────────
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

# ── 6. Ejecutar la página activa ──────────────────────────────────────────────
pg.run()
