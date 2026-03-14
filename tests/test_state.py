"""Tests para core/state.py — sin importar Streamlit real.

Estrategia: todas las funciones aceptan ``_ss`` (dict ordinario).
Los tests construyen su propio dict y lo pasan directamente —
no hay mock de ``st.session_state`` ni dependencia del runtime de Streamlit.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.state as state
from core.state import (
    _LABEL_CLEAN,
    _LABEL_DIRTY,
    _LABEL_NEVER,
    delete_position,
    get_layer,
    get_position,
    init_session_state,
    is_dirty,
    list_positions,
    mark_clean,
    mark_dirty,
    set_position,
    status_label,
    update_layer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ss() -> dict:
    """Session state vacío listo para inicializar."""
    return {}


@pytest.fixture()
def ss_init(ss) -> dict:
    """Session state ya inicializado con defaults."""
    init_session_state(_ss=ss)
    return ss


@pytest.fixture()
def ss_with_positions(ss_init) -> dict:
    """Session state con 3 posiciones de distintas clases."""
    set_position("ING_001", {"Clase": "Ingreso_Recurrente", "Moneda": "CLP"}, _ss=ss_init)
    set_position("GAS_ESE_001", {"Clase": "Gasto_Esencial", "Moneda": "CLP"}, _ss=ss_init)
    set_position("PAS_HIP_001", {"Clase": "Pasivo_Estructural", "Moneda": "UF"}, _ss=ss_init)
    # Resetear dirty para que tests puedan verificar desde estado limpio
    ss_init["dirty"] = False
    return ss_init


TS = datetime(2026, 3, 13, 10, 30, 0, tzinfo=timezone.utc)


# ===========================================================================
# 1. init_session_state
# ===========================================================================


class TestInitSessionState:
    def test_inicializa_todas_las_claves(self, ss):
        init_session_state(_ss=ss)
        assert "dirty" in ss
        assert "positions" in ss
        assert "layer_unlocked" in ss
        assert "onboarding_complete" in ss
        assert "last_saved" in ss

    def test_valores_default(self, ss):
        init_session_state(_ss=ss)
        assert ss["dirty"] is False
        assert ss["positions"] == {}
        assert ss["layer_unlocked"] == 1
        assert ss["onboarding_complete"] is False
        assert ss["last_saved"] is None

    def test_idempotente_no_sobreescribe_existentes(self, ss):
        ss["dirty"] = True
        ss["layer_unlocked"] = 3
        ss["positions"] = {"ING_001": {"Clase": "Ingreso_Recurrente"}}
        init_session_state(_ss=ss)
        # Los valores previos se conservan
        assert ss["dirty"] is True
        assert ss["layer_unlocked"] == 3
        assert "ING_001" in ss["positions"]

    def test_partial_init_rellena_lo_que_falta(self, ss):
        ss["dirty"] = True  # ya existe
        init_session_state(_ss=ss)
        assert ss["dirty"] is True          # conservado
        assert ss["positions"] == {}        # creado con default
        assert ss["layer_unlocked"] == 1    # creado con default

    def test_idempotente_en_doble_llamada(self, ss):
        init_session_state(_ss=ss)
        init_session_state(_ss=ss)
        assert ss["dirty"] is False
        assert ss["positions"] == {}


# ===========================================================================
# 2. Dirty flag
# ===========================================================================


class TestMarkDirty:
    def test_setea_dirty_true(self, ss_init):
        assert ss_init["dirty"] is False
        mark_dirty(_ss=ss_init)
        assert ss_init["dirty"] is True

    def test_idempotente(self, ss_init):
        mark_dirty(_ss=ss_init)
        mark_dirty(_ss=ss_init)
        assert ss_init["dirty"] is True

    def test_funciona_sin_init(self, ss):
        mark_dirty(_ss=ss)
        assert ss["dirty"] is True


class TestMarkClean:
    def test_setea_dirty_false(self, ss_init):
        ss_init["dirty"] = True
        mark_clean(TS, _ss=ss_init)
        assert ss_init["dirty"] is False

    def test_guarda_timestamp(self, ss_init):
        mark_clean(TS, _ss=ss_init)
        assert ss_init["last_saved"] == TS

    def test_sobreescribe_timestamp_anterior(self, ss_init):
        ts1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 13, tzinfo=timezone.utc)
        mark_clean(ts1, _ss=ss_init)
        mark_clean(ts2, _ss=ss_init)
        assert ss_init["last_saved"] == ts2

    def test_dirty_false_y_last_saved_atomico(self, ss_init):
        ss_init["dirty"] = True
        mark_clean(TS, _ss=ss_init)
        assert ss_init["dirty"] is False
        assert ss_init["last_saved"] is TS


class TestIsDirty:
    def test_false_en_estado_limpio(self, ss_init):
        assert is_dirty(_ss=ss_init) is False

    def test_true_tras_mark_dirty(self, ss_init):
        mark_dirty(_ss=ss_init)
        assert is_dirty(_ss=ss_init) is True

    def test_false_tras_mark_clean(self, ss_init):
        mark_dirty(_ss=ss_init)
        mark_clean(TS, _ss=ss_init)
        assert is_dirty(_ss=ss_init) is False

    def test_false_por_default_en_ss_vacio(self, ss):
        assert is_dirty(_ss=ss) is False

    def test_retorna_bool(self, ss_init):
        assert isinstance(is_dirty(_ss=ss_init), bool)


class TestStatusLabel:
    def test_nunca_guardado(self, ss_init):
        assert status_label(_ss=ss_init) == _LABEL_NEVER

    def test_dirty(self, ss_init):
        mark_dirty(_ss=ss_init)
        assert status_label(_ss=ss_init) == _LABEL_DIRTY

    def test_sincronizado(self, ss_init):
        mark_clean(TS, _ss=ss_init)
        assert status_label(_ss=ss_init) == _LABEL_CLEAN

    def test_dirty_prevalece_sobre_last_saved(self, ss_init):
        # Guardado previo + nuevos cambios → debe mostrar dirty
        mark_clean(TS, _ss=ss_init)
        mark_dirty(_ss=ss_init)
        assert status_label(_ss=ss_init) == _LABEL_DIRTY

    def test_labels_son_los_correctos(self):
        assert _LABEL_DIRTY == "● Cambios sin guardar"
        assert _LABEL_CLEAN == "✓ Sincronizado"
        assert _LABEL_NEVER == "Sin guardar aún"

    def test_estado_vacio_retorna_nunca_guardado(self, ss):
        assert status_label(_ss=ss) == _LABEL_NEVER


# ===========================================================================
# 3. Gestión de posiciones
# ===========================================================================


class TestSetPosition:
    def test_crea_nueva_posicion(self, ss_init):
        params = {"Clase": "Ingreso_Recurrente", "Moneda": "CLP"}
        set_position("ING_001", params, _ss=ss_init)
        assert ss_init["positions"]["ING_001"] == params

    def test_actualiza_posicion_existente(self, ss_init):
        set_position("ING_001", {"Clase": "Ingreso_Recurrente"}, _ss=ss_init)
        set_position("ING_001", {"Clase": "Ingreso_Recurrente", "Monto": 3_000_000}, _ss=ss_init)
        assert ss_init["positions"]["ING_001"]["Monto"] == 3_000_000

    def test_marca_dirty(self, ss_init):
        assert ss_init["dirty"] is False
        set_position("ING_001", {}, _ss=ss_init)
        assert ss_init["dirty"] is True

    def test_funciona_sin_positions_previo(self, ss):
        set_position("ING_001", {"Clase": "x"}, _ss=ss)
        assert ss["positions"]["ING_001"]["Clase"] == "x"

    def test_multiples_posiciones_independientes(self, ss_init):
        set_position("ING_001", {"Monto": 1}, _ss=ss_init)
        set_position("ING_002", {"Monto": 2}, _ss=ss_init)
        assert len(ss_init["positions"]) == 2
        assert ss_init["positions"]["ING_001"]["Monto"] == 1
        assert ss_init["positions"]["ING_002"]["Monto"] == 2


class TestGetPosition:
    def test_retorna_params_existentes(self, ss_with_positions):
        result = get_position("ING_001", _ss=ss_with_positions)
        assert result == {"Clase": "Ingreso_Recurrente", "Moneda": "CLP"}

    def test_retorna_none_si_no_existe(self, ss_with_positions):
        assert get_position("NOEXISTE", _ss=ss_with_positions) is None

    def test_retorna_none_en_ss_vacio(self, ss):
        assert get_position("ING_001", _ss=ss) is None

    def test_retorna_referencia_al_dict_original(self, ss_init):
        params = {"Clase": "Ingreso_Recurrente"}
        set_position("ING_001", params, _ss=ss_init)
        result = get_position("ING_001", _ss=ss_init)
        assert result is ss_init["positions"]["ING_001"]


class TestDeletePosition:
    def test_elimina_posicion_existente(self, ss_with_positions):
        delete_position("ING_001", _ss=ss_with_positions)
        assert "ING_001" not in ss_with_positions["positions"]

    def test_marca_dirty_al_eliminar(self, ss_with_positions):
        assert ss_with_positions["dirty"] is False
        delete_position("ING_001", _ss=ss_with_positions)
        assert ss_with_positions["dirty"] is True

    def test_no_marca_dirty_si_no_existe(self, ss_init):
        assert ss_init["dirty"] is False
        delete_position("NOEXISTE", _ss=ss_init)
        assert ss_init["dirty"] is False

    def test_no_lanza_error_si_no_existe(self, ss_init):
        delete_position("NOEXISTE", _ss=ss_init)  # debe ser silencioso

    def test_otras_posiciones_no_afectadas(self, ss_with_positions):
        delete_position("ING_001", _ss=ss_with_positions)
        assert "GAS_ESE_001" in ss_with_positions["positions"]
        assert "PAS_HIP_001" in ss_with_positions["positions"]


class TestListPositions:
    def test_lista_todas_las_posiciones(self, ss_with_positions):
        ids = list_positions(_ss=ss_with_positions)
        assert set(ids) == {"ING_001", "GAS_ESE_001", "PAS_HIP_001"}

    def test_filtra_por_clase(self, ss_with_positions):
        ids = list_positions(clase="Pasivo_Estructural", _ss=ss_with_positions)
        assert ids == ["PAS_HIP_001"]

    def test_clase_sin_coincidencias_retorna_vacia(self, ss_with_positions):
        ids = list_positions(clase="Activo_Financiero", _ss=ss_with_positions)
        assert ids == []

    def test_sin_clase_retorna_todos(self, ss_with_positions):
        assert len(list_positions(_ss=ss_with_positions)) == 3

    def test_ss_vacio_retorna_lista_vacia(self, ss):
        assert list_positions(_ss=ss) == []

    def test_retorna_lista(self, ss_with_positions):
        assert isinstance(list_positions(_ss=ss_with_positions), list)

    def test_posiciones_sin_clase_no_se_incluyen_en_filtro(self, ss_init):
        set_position("ING_001", {"Moneda": "CLP"}, _ss=ss_init)  # sin Clase
        set_position("ING_002", {"Clase": "Ingreso_Recurrente"}, _ss=ss_init)
        ids = list_positions(clase="Ingreso_Recurrente", _ss=ss_init)
        assert ids == ["ING_002"]
        assert "ING_001" not in ids


# ===========================================================================
# 4. Gestión de capas
# ===========================================================================


class TestUpdateLayer:
    def test_capa1_cuando_estado_minimo(self, ss_init):
        update_layer(_ss=ss_init)
        assert ss_init["layer_unlocked"] == 1

    def test_capa2_con_condiciones_cumplidas(self, ss_init):
        ss_init["meta_fondo_definida"] = True
        ss_init["buckets_confirmados"] = True
        update_layer(_ss=ss_init)
        assert ss_init["layer_unlocked"] == 2

    def test_capa3_con_condiciones_cumplidas(self, ss_init):
        ss_init["meta_fondo_definida"] = True
        ss_init["buckets_confirmados"] = True
        ss_init["pasivos_con_tabla"] = ["PAS_HIP_001"]
        ss_init["afp_saldo"] = 50_000_000
        update_layer(_ss=ss_init)
        assert ss_init["layer_unlocked"] == 3

    def test_capa4_con_todas_condiciones_cumplidas(self, ss_init):
        ss_init["meta_fondo_definida"] = True
        ss_init["buckets_confirmados"] = True
        ss_init["pasivos_con_tabla"] = ["PAS_HIP_001"]
        ss_init["afp_saldo"] = 50_000_000
        ss_init["activos_con_tabla"] = ["ACT_ETF_001"]
        ss_init["objetivos_activos"] = ["OBJ_VIAJE_001"]
        update_layer(_ss=ss_init)
        assert ss_init["layer_unlocked"] == 4

    def test_regresion_a_capa_inferior(self, ss_init):
        # Simula que el usuario borra un pasivo: retrocede de capa 3 a capa 2
        ss_init["meta_fondo_definida"] = True
        ss_init["buckets_confirmados"] = True
        ss_init["pasivos_con_tabla"] = []  # borrado
        ss_init["afp_saldo"] = 50_000_000
        ss_init["layer_unlocked"] = 3
        update_layer(_ss=ss_init)
        assert ss_init["layer_unlocked"] == 2

    def test_resultado_entre_1_y_4(self, ss_init):
        for combo in [
            {},
            {"meta_fondo_definida": True, "buckets_confirmados": True},
            {
                "meta_fondo_definida": True,
                "buckets_confirmados": True,
                "pasivos_con_tabla": ["X"],
                "afp_saldo": 1,
            },
            {
                "meta_fondo_definida": True,
                "buckets_confirmados": True,
                "pasivos_con_tabla": ["X"],
                "afp_saldo": 1,
                "activos_con_tabla": ["Y"],
                "objetivos_activos": ["Z"],
            },
        ]:
            ss_init.update(combo)
            update_layer(_ss=ss_init)
            assert 1 <= ss_init["layer_unlocked"] <= 4


class TestGetLayer:
    def test_retorna_1_por_default(self, ss):
        assert get_layer(_ss=ss) == 1

    def test_retorna_valor_inicializado(self, ss_init):
        assert get_layer(_ss=ss_init) == 1

    def test_retorna_capa_actualizada(self, ss_init):
        ss_init["layer_unlocked"] = 3
        assert get_layer(_ss=ss_init) == 3

    def test_retorna_int(self, ss_init):
        assert isinstance(get_layer(_ss=ss_init), int)

    def test_refleja_update_layer(self, ss_init):
        ss_init["meta_fondo_definida"] = True
        ss_init["buckets_confirmados"] = True
        update_layer(_ss=ss_init)
        assert get_layer(_ss=ss_init) == 2


# ===========================================================================
# Tests de integración — flujos completos
# ===========================================================================


class TestIntegracion:
    def test_flujo_completo_edicion_guardado(self, ss_init):
        """Editar → dirty → guardar → clean → sincronizado."""
        assert status_label(_ss=ss_init) == _LABEL_NEVER

        set_position("ING_001", {"Clase": "Ingreso_Recurrente"}, _ss=ss_init)
        assert is_dirty(_ss=ss_init) is True
        assert status_label(_ss=ss_init) == _LABEL_DIRTY

        mark_clean(TS, _ss=ss_init)
        assert is_dirty(_ss=ss_init) is False
        assert status_label(_ss=ss_init) == _LABEL_CLEAN

    def test_segunda_edicion_tras_guardado(self, ss_init):
        """Guardar → segunda edición → dirty de nuevo."""
        set_position("ING_001", {}, _ss=ss_init)
        mark_clean(TS, _ss=ss_init)
        assert is_dirty(_ss=ss_init) is False

        set_position("ING_001", {"Monto": 999}, _ss=ss_init)
        assert is_dirty(_ss=ss_init) is True

    def test_delete_actualiza_lista(self, ss_with_positions):
        assert "ING_001" in list_positions(_ss=ss_with_positions)
        delete_position("ING_001", _ss=ss_with_positions)
        assert "ING_001" not in list_positions(_ss=ss_with_positions)

    def test_update_layer_despues_de_set_position(self, ss_init):
        """Agregar pasivo + setear afp_saldo → update_layer → capa 3."""
        ss_init["meta_fondo_definida"] = True
        ss_init["buckets_confirmados"] = True
        ss_init["afp_saldo"] = 20_000_000

        # Simula agregar un pasivo con tabla
        set_position("PAS_HIP_001", {"Clase": "Pasivo_Estructural"}, _ss=ss_init)
        ss_init["pasivos_con_tabla"] = ["PAS_HIP_001"]
        update_layer(_ss=ss_init)

        assert get_layer(_ss=ss_init) == 3

    def test_init_idempotente_en_flujo_real(self, ss):
        """init → editar → init de nuevo no borra datos."""
        init_session_state(_ss=ss)
        set_position("ING_001", {"Monto": 1_000}, _ss=ss)
        init_session_state(_ss=ss)  # segunda llamada
        assert get_position("ING_001", _ss=ss) is not None
