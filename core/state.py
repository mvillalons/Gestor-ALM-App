"""core/state.py — Gestión del estado de sesión y lógica de "cambios sin guardar".

En producción todas las funciones operan sobre ``st.session_state`` directamente.
Para tests se puede pasar un dict ordinario mediante el parámetro ``_ss``,
evitando así la dependencia del contexto de Streamlit.

Patrón de uso (producción):
    from core import state

    state.init_session_state()        # al inicio de cada página
    state.set_position("ING_001", params)
    state.mark_dirty()                # llamado internamente por set_position
    label = state.status_label()      # "● Cambios sin guardar"

Patrón de uso (tests):
    ss = {}
    state.init_session_state(_ss=ss)
    state.mark_dirty(_ss=ss)
    assert state.is_dirty(_ss=ss)

Streamlit se importa de forma lazy (dentro de ``_get_ss``) para que el módulo
sea importable sin un contexto de Streamlit activo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.calculator import capa_desbloqueada

# ---------------------------------------------------------------------------
# Valores por defecto
# ---------------------------------------------------------------------------


def _make_defaults() -> dict[str, Any]:
    """Crea un dict de valores default con objetos mutables frescos.

    No usar un dict de módulo compartido para los defaults: si se asigna
    ``ss["positions"] = _DEFAULTS["positions"]``, todas las sesiones
    compartirían el mismo dict interno y se contaminarían entre sí.
    """
    return {
        "dirty": False,
        "positions": {},
        "schedules": {},            # {id_posicion: pd.DataFrame} — tablas de desarrollo en memoria
        # Capa 2 unlock flags (set during onboarding, restored from Drive on load)
        "meta_fondo_definida": False,
        "buckets_confirmados": False,
        # Capa 3 unlock data
        "pasivos_con_tabla": [],    # lista de IDs de pasivos con tabla generada
        "afp_saldo": None,          # float | None — saldo AFP actual
        # Capa 4 unlock data
        "activos_con_tabla": [],    # lista de IDs de activos financieros con tabla
        "objetivos_activos": [],    # lista de IDs de objetivos de ahorro activos
        # Tipos de cambio para normalización a CLP (actualización manual hasta Capa 4)
        "valor_uf": 39_700.0,       # CLP por UF  — fuente: Banco Central (manual)
        "valor_usd": 950.0,         # CLP por USD — fuente: BCCh / SII (manual)
        "layer_unlocked": 1,
        "onboarding_complete": False,
        "last_saved": None,
        # Capa 2-C — desagregación de buckets
        "sugerencias_pendientes": [],   # list[dict] — sugerencias de vinculación bucket
    }

# Labels del indicador de estado
_LABEL_CLEAN = "✓ Sincronizado"
_LABEL_DIRTY = "● Cambios sin guardar"
_LABEL_NEVER = "Sin guardar aún"


# ---------------------------------------------------------------------------
# Helper interno — resolución del session_state
# ---------------------------------------------------------------------------


def _get_ss(_ss: dict | None) -> dict:
    """Resuelve el objeto de session_state a usar.

    Args:
        _ss: Dict inyectado (para tests). Si es ``None``, importa y retorna
            ``st.session_state`` de Streamlit (lazy import).

    Returns:
        El dict de estado a usar.
    """
    if _ss is not None:
        return _ss
    import streamlit as st  # noqa: PLC0415 — lazy import intencional

    return st.session_state


# ---------------------------------------------------------------------------
# 1. Inicialización
# ---------------------------------------------------------------------------


def init_session_state(_ss: dict | None = None) -> None:
    """Inicializa ``st.session_state`` con valores default si las claves no existen.

    Es idempotente: no sobreescribe claves que ya estén presentes.
    Debe llamarse al inicio de cada página Streamlit.

    Claves inicializadas:
        - ``"dirty"`` → ``False``
        - ``"positions"`` → ``{}``
        - ``"meta_fondo_definida"`` → ``False``  (Capa 2 unlock)
        - ``"buckets_confirmados"`` → ``False``  (Capa 2 unlock)
        - ``"pasivos_con_tabla"`` → ``[]``       (Capa 3 unlock)
        - ``"afp_saldo"`` → ``None``             (Capa 3 unlock)
        - ``"activos_con_tabla"`` → ``[]``       (Capa 4 unlock)
        - ``"objetivos_activos"`` → ``[]``       (Capa 4 unlock)
        - ``"layer_unlocked"`` → ``1``
        - ``"onboarding_complete"`` → ``False``
        - ``"last_saved"`` → ``None``

    Args:
        _ss: Dict de estado (inyección para tests). ``None`` usa
            ``st.session_state``.
    """
    ss = _get_ss(_ss)
    for key, default in _make_defaults().items():
        if key not in ss:
            ss[key] = default


# ---------------------------------------------------------------------------
# 2. Gestión de cambios (dirty flag)
# ---------------------------------------------------------------------------


def mark_dirty(_ss: dict | None = None) -> None:
    """Marca el estado como "con cambios sin guardar".

    Args:
        _ss: Dict de estado (inyección para tests).
    """
    _get_ss(_ss)["dirty"] = True


def mark_clean(timestamp: datetime, _ss: dict | None = None) -> None:
    """Marca el estado como sincronizado y registra el timestamp de guardado.

    Args:
        timestamp: Momento en que se completó el guardado en Drive.
        _ss: Dict de estado (inyección para tests).
    """
    ss = _get_ss(_ss)
    ss["dirty"] = False
    ss["last_saved"] = timestamp


def is_dirty(_ss: dict | None = None) -> bool:
    """Retorna ``True`` si hay cambios sin guardar.

    Args:
        _ss: Dict de estado (inyección para tests).

    Returns:
        Valor actual de la clave ``"dirty"``. ``False`` si la clave no existe.
    """
    return bool(_get_ss(_ss).get("dirty", False))


def status_label(_ss: dict | None = None) -> str:
    """Retorna el texto del indicador de estado de sincronización.

    Returns:
        - ``"● Cambios sin guardar"`` cuando hay cambios pendientes.
        - ``"✓ Sincronizado"`` cuando está limpio y ya se guardó al menos una vez.
        - ``"Sin guardar aún"`` cuando está limpio pero nunca se ha guardado.

    Args:
        _ss: Dict de estado (inyección para tests).
    """
    ss = _get_ss(_ss)
    if ss.get("dirty", False):
        return _LABEL_DIRTY
    if ss.get("last_saved") is not None:
        return _LABEL_CLEAN
    return _LABEL_NEVER


# ---------------------------------------------------------------------------
# 3. Gestión de posiciones
# ---------------------------------------------------------------------------


def set_position(
    id_posicion: str,
    params: dict,
    _ss: dict | None = None,
) -> None:
    """Agrega o actualiza una posición en el estado y marca dirty.

    Args:
        id_posicion: ID único de la posición (p. ej. ``"PAS_HIP_001"``).
        params: Diccionario de parámetros de la posición.
        _ss: Dict de estado (inyección para tests).
    """
    ss = _get_ss(_ss)
    ss.setdefault("positions", {})[id_posicion] = params
    mark_dirty(_ss=ss)


def get_position(
    id_posicion: str,
    _ss: dict | None = None,
) -> dict | None:
    """Retorna los parámetros de una posición por su ID.

    Args:
        id_posicion: ID de la posición a consultar.
        _ss: Dict de estado (inyección para tests).

    Returns:
        Dict de parámetros si la posición existe, ``None`` en caso contrario.
    """
    ss = _get_ss(_ss)
    return ss.get("positions", {}).get(id_posicion)


def delete_position(
    id_posicion: str,
    _ss: dict | None = None,
) -> None:
    """Elimina una posición del estado y marca dirty.

    Si el ID no existe, la operación es silenciosa (no lanza error y no
    marca dirty, ya que el estado no cambió).

    Args:
        id_posicion: ID de la posición a eliminar.
        _ss: Dict de estado (inyección para tests).
    """
    ss = _get_ss(_ss)
    positions: dict = ss.get("positions", {})
    if id_posicion in positions:
        del positions[id_posicion]
        mark_dirty(_ss=ss)


def list_positions(
    clase: str | None = None,
    _ss: dict | None = None,
) -> list[str]:
    """Lista los IDs de posiciones registradas, opcionalmente filtrando por clase.

    Args:
        clase: Si se provee, retorna solo los IDs cuya clave ``"Clase"``
            coincida con este valor (p. ej. ``"Pasivo_Estructural"``).
            ``None`` retorna todos los IDs.
        _ss: Dict de estado (inyección para tests).

    Returns:
        Lista de IDs ordenada por orden de inserción.
    """
    ss = _get_ss(_ss)
    positions: dict = ss.get("positions", {})

    if clase is None:
        return list(positions.keys())

    return [
        pid
        for pid, params in positions.items()
        if params.get("Clase") == clase
    ]


# ---------------------------------------------------------------------------
# 4. Gestión de capas
# ---------------------------------------------------------------------------


def update_layer(_ss: dict | None = None) -> None:
    """Recalcula la capa desbloqueada y actualiza ``layer_unlocked``.

    Delega en :func:`core.calculator.capa_desbloqueada` usando el propio
    session_state como parámetro, ya que contiene todas las claves que
    esa función necesita.

    Args:
        _ss: Dict de estado (inyección para tests).
    """
    ss = _get_ss(_ss)
    ss["layer_unlocked"] = capa_desbloqueada(ss)


def get_layer(_ss: dict | None = None) -> int:
    """Retorna la capa máxima desbloqueada.

    Args:
        _ss: Dict de estado (inyección para tests).

    Returns:
        Entero entre 1 y 4. Retorna ``1`` si la clave no existe.
    """
    return int(_get_ss(_ss).get("layer_unlocked", 1))
