"""tests/test_planner.py — Tests del Agente Planificador (core/planner.py).

Cubre:
  - Paso 1: sin deudas → completo; con deudas → avalanche correcto
  - Paso 2: fondo cubierto → completo; meta editable cambia el umbral
  - Paso 3: brecha pensional calculada correctamente; parámetros editables
  - Paso 4: bloqueado hasta completar 1 y 2; activo cuando ambos completos
  - Integridad de la estructura de retorno (4 pasos, claves correctas)
"""

from __future__ import annotations

import pytest

from core import planner, state

# ---------------------------------------------------------------------------
# Helpers de setup
# ---------------------------------------------------------------------------


def _ss_base() -> dict:
    """Session state limpio con defaults inicializados."""
    ss: dict = {}
    state.init_session_state(_ss=ss)
    return ss


def _add_ing(ss: dict, monto: float = 2_000_000, moneda: str = "CLP") -> None:
    ss["positions"]["ING_PRINCIPAL"] = {
        "Clase": "Ingreso_Recurrente",
        "Monto_Mensual": monto,
        "Moneda": moneda,
    }


def _add_ese(ss: dict, monto: float = 500_000) -> None:
    ss["positions"]["GAS_ESE_BUCKET"] = {
        "Clase": "Gasto_Esencial",
        "Monto_Mensual": monto,
        "Moneda": "CLP",
    }


def _add_liq(ss: dict, saldo: float = 0.0) -> None:
    ss["positions"]["ACT_LIQUIDO_PRINCIPAL"] = {
        "Clase": "Activo_Liquido",
        "Saldo_Actual": saldo,
        "Moneda": "CLP",
    }


def _add_deuda(
    ss: dict,
    pid: str,
    descripcion: str,
    tasa_anual: float,
    monto: float = 1_000_000,
    moneda: str = "CLP",
) -> None:
    ss["positions"][pid] = {
        "Clase": "Pasivo_Corto_Plazo",
        "Tipo": "Crédito consumo",
        "Descripcion": descripcion,
        "Tasa_Anual": tasa_anual,
        "Monto": monto,
        "Moneda": moneda,
    }


def _add_afp(
    ss: dict,
    saldo: float = 50_000_000,
    edad_actual: float = 35.0,
    edad_jubilacion: float = 65.0,
) -> None:
    ss["positions"]["AFP_001"] = {
        "Clase": "Prevision_AFP",
        "Descripcion": "AFP Modelo",
        "Saldo_Actual": saldo,
        "Edad_Actual": edad_actual,
        "Edad_Jubilacion": edad_jubilacion,
        "Aporte_Mensual": 100_000,
        "Tasa_Anual": 0.05,
        "Moneda": "CLP",
    }


# ---------------------------------------------------------------------------
# 1. Estructura del retorno
# ---------------------------------------------------------------------------


class TestEstructuraPlan:
    def test_retorna_exactamente_4_pasos(self) -> None:
        plan = planner.generar_plan(_ss_base())
        assert len(plan) == 4

    def test_numeros_1_a_4_en_orden(self) -> None:
        plan = planner.generar_plan(_ss_base())
        assert [p["numero"] for p in plan] == [1, 2, 3, 4]

    def test_claves_obligatorias_presentes(self) -> None:
        claves = {"numero", "titulo", "estado", "diagnostico", "accion",
                  "monto_mensual", "plazo_meses", "params"}
        plan = planner.generar_plan(_ss_base())
        for paso in plan:
            assert claves.issubset(paso.keys()), (
                f"Paso {paso.get('numero')} falta claves: {claves - paso.keys()}"
            )

    def test_estados_son_valores_validos(self) -> None:
        validos = {planner.ESTADO_COMPLETO, planner.ESTADO_EN_CURSO, planner.ESTADO_PENDIENTE}
        plan = planner.generar_plan(_ss_base())
        for paso in plan:
            assert paso["estado"] in validos, (
                f"Paso {paso['numero']} tiene estado inválido: {paso['estado']}"
            )

    def test_monto_mensual_no_negativo(self) -> None:
        ss = _ss_base()
        _add_ing(ss)
        _add_ese(ss)
        _add_liq(ss, 1_000_000)
        plan = planner.generar_plan(ss)
        for paso in plan:
            assert paso["monto_mensual"] >= 0.0


# ---------------------------------------------------------------------------
# 2. Paso 1 — Liquidar deudas de consumo
# ---------------------------------------------------------------------------


class TestPaso1Deudas:
    def test_sin_deudas_estado_completo(self) -> None:
        plan = planner.generar_plan(_ss_base())
        assert plan[0]["estado"] == planner.ESTADO_COMPLETO

    def test_sin_deudas_accion_vacia(self) -> None:
        plan = planner.generar_plan(_ss_base())
        assert plan[0]["accion"] == ""

    def test_sin_deudas_lista_vacia_en_params(self) -> None:
        plan = planner.generar_plan(_ss_base())
        assert plan[0]["params"]["deudas_ordenadas"] == []

    def test_con_una_deuda_estado_en_curso(self) -> None:
        ss = _ss_base()
        _add_deuda(ss, "PAS_CON_001", "Crédito BCI", 0.12)
        plan = planner.generar_plan(ss)
        assert plan[0]["estado"] == planner.ESTADO_EN_CURSO

    def test_con_deuda_lista_no_vacia(self) -> None:
        ss = _ss_base()
        _add_deuda(ss, "PAS_CON_001", "Crédito BCI", 0.12)
        plan = planner.generar_plan(ss)
        assert len(plan[0]["params"]["deudas_ordenadas"]) == 1

    def test_dos_deudas_orden_avalanche_mayor_tasa_primero(self) -> None:
        ss = _ss_base()
        _add_deuda(ss, "PAS_CON_001", "Crédito bajo", 0.08, monto=1_000_000)
        _add_deuda(ss, "PAS_CON_002", "Crédito alto", 0.25, monto=500_000)
        plan = planner.generar_plan(ss)
        deudas = plan[0]["params"]["deudas_ordenadas"]
        assert len(deudas) == 2
        assert deudas[0]["tasa_anual"] > deudas[1]["tasa_anual"]
        assert deudas[0]["descripcion"] == "Crédito alto"
        assert deudas[1]["descripcion"] == "Crédito bajo"

    def test_tres_deudas_orden_avalanche_descendente(self) -> None:
        ss = _ss_base()
        _add_deuda(ss, "PAS_CON_001", "Baja", 0.08)
        _add_deuda(ss, "PAS_CON_002", "Alta", 0.35)
        _add_deuda(ss, "PAS_CON_003", "Media", 0.15)
        plan = planner.generar_plan(ss)
        tasas = [d["tasa_anual"] for d in plan[0]["params"]["deudas_ordenadas"]]
        assert tasas == sorted(tasas, reverse=True)

    def test_con_margen_positivo_plazo_calculado(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 300_000, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 200_000, "Moneda": "CLP"}
        _add_liq(ss, 4_000_000)  # > 3 meses esenciales → sin mínimo paralelo
        _add_deuda(ss, "PAS_CON_001", "Deuda única", 0.12, monto=1_000_000)
        plan = planner.generar_plan(ss)
        # margen = 2M - 1M = 1M; deuda = 1M → plazo = ceil(1M / 1M) = 1
        assert plan[0]["plazo_meses"] == 1
        assert plan[0]["monto_mensual"] == pytest.approx(1_000_000)

    def test_deudas_con_saldo_cero_excluidas(self) -> None:
        """Deudas con saldo 0 (pagadas) no deben aparecer en el plan."""
        ss = _ss_base()
        ss["positions"]["PAS_CON_PAGADA"] = {
            "Clase": "Pasivo_Corto_Plazo",
            "Tipo": "Crédito consumo",
            "Descripcion": "Deuda pagada",
            "Tasa_Anual": 0.20,
            "Monto": 0,  # saldo 0
            "Moneda": "CLP",
        }
        plan = planner.generar_plan(ss)
        assert plan[0]["estado"] == planner.ESTADO_COMPLETO

    def test_hipotecarios_no_entran_en_paso1(self) -> None:
        """Pasivos hipotecarios (Pasivo_Estructural) son aceptables → no se liquidan."""
        ss = _ss_base()
        ss["positions"]["PAS_HIP_001"] = {
            "Clase": "Pasivo_Estructural",
            "Tipo": "Hipotecario",
            "Descripcion": "Crédito hipotecario",
            "Tasa_Anual": 0.04,
            "Capital": 80_000_000,
            "Moneda": "UF",
        }
        plan = planner.generar_plan(ss)
        # Hipotecario no debe aparecer como deuda a liquidar
        assert plan[0]["estado"] == planner.ESTADO_COMPLETO


# ---------------------------------------------------------------------------
# 3. Paso 2 — Fondo de reserva
# ---------------------------------------------------------------------------


class TestPaso2FondoReserva:
    def test_fondo_cubierto_6_meses_completo(self) -> None:
        ss = _ss_base()
        _add_ese(ss, 1_000_000)
        _add_liq(ss, 7_000_000)  # 7 meses > 6
        ss["plan_params"] = planner.make_plan_params_defaults()
        plan = planner.generar_plan(ss)
        assert plan[1]["estado"] == planner.ESTADO_COMPLETO

    def test_fondo_exactamente_6_meses_completo(self) -> None:
        ss = _ss_base()
        _add_ese(ss, 1_000_000)
        _add_liq(ss, 6_000_000)  # exactamente 6 meses
        ss["plan_params"] = planner.make_plan_params_defaults()
        plan = planner.generar_plan(ss)
        assert plan[1]["estado"] == planner.ESTADO_COMPLETO

    def test_fondo_insuficiente_en_curso(self) -> None:
        ss = _ss_base()
        _add_ese(ss, 1_000_000)
        _add_liq(ss, 2_000_000)  # 2 meses < 6
        ss["plan_params"] = planner.make_plan_params_defaults()
        plan = planner.generar_plan(ss)
        assert plan[1]["estado"] == planner.ESTADO_EN_CURSO

    def test_meta_editable_3_meses_cambia_umbral(self) -> None:
        ss = _ss_base()
        _add_ese(ss, 1_000_000)
        _add_liq(ss, 3_500_000)  # 3.5 meses

        # Con meta 6 meses → incompleto
        params_6 = planner.make_plan_params_defaults()
        params_6["meses_reserva_meta"] = 6
        ss["plan_params"] = params_6
        plan_6 = planner.generar_plan(ss)
        assert plan_6[1]["estado"] == planner.ESTADO_EN_CURSO

        # Con meta 3 meses → completo
        params_3 = planner.make_plan_params_defaults()
        params_3["meses_reserva_meta"] = 3
        ss["plan_params"] = params_3
        plan_3 = planner.generar_plan(ss)
        assert plan_3[1]["estado"] == planner.ESTADO_COMPLETO

    def test_meta_en_titulo_refleja_meses_configurados(self) -> None:
        ss = _ss_base()
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 9
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        assert "9" in plan[1]["titulo"]

    def test_con_deudas_paso2_usa_20_pct_margen(self) -> None:
        """Con Paso 1 en_curso y fondo < 3 meses, Paso 2 usa solo 20% del margen."""
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 1_000_000)
        _add_liq(ss, 500_000)   # < 3 meses esenciales → corre paralelo
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        _add_deuda(ss, "PAS_CON_001", "Deuda", 0.10, monto=2_000_000)
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 6
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        # margen = 2M - 1M = 1M; 20% = 200K
        assert plan[1]["monto_mensual"] == pytest.approx(200_000)


# ---------------------------------------------------------------------------
# 4. Paso 3 — Pensión asegurada
# ---------------------------------------------------------------------------


class TestPaso3Pension:
    def test_retorna_paso_numero_3(self) -> None:
        plan = planner.generar_plan(_ss_base())
        assert plan[2]["numero"] == 3

    def test_titulo_correcto(self) -> None:
        plan = planner.generar_plan(_ss_base())
        assert "Pensión" in plan[2]["titulo"] or "pension" in plan[2]["titulo"].lower()

    def test_brecha_correcta_sin_afp(self) -> None:
        """Sin AFP, aporte_ideal supera el margen → monto se limita al margen disponible.

        Con divisor=120 (fallback sin AFP), aporte_ideal = ing×1.4 > ing ≥ margen,
        por lo que el monto efectivo queda capped al margen disponible tras P1 y P2.
        """
        ss = _ss_base()
        _add_ing(ss, 1_000_000)
        params = planner.make_plan_params_defaults()
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        # meta = 1M × 0.70 × 12 × 20 = 168M; divisor = 120 → aporte_ideal = 1.4M
        # ese=0 → P2 completo; margen = 1M; 1.4M > 1M → monto capped al margen (1M)
        assert plan[2]["monto_mensual"] == pytest.approx(1_000_000, rel=1e-3)

    def test_tasa_reemplazo_50_pct(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 1_000_000)
        params = planner.make_plan_params_defaults()
        params["tasa_reemplazo"] = 0.50
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        # meta = 1M × 0.50 × 12 × 20 = 120M → aporte = 120M / 120 = 1M
        expected = (1_000_000 * 0.50 * 12 * 20) / 120
        assert plan[2]["monto_mensual"] == pytest.approx(expected, rel=1e-6)

    def test_anos_retiro_editable(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 1_000_000)
        params = planner.make_plan_params_defaults()
        params["anos_retiro"] = 10
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        # meta = 1M × 0.70 × 12 × 10 = 84M → aporte = 84M / 120 = 700K
        expected = (1_000_000 * 0.70 * 12 * 10) / 120
        assert plan[2]["monto_mensual"] == pytest.approx(expected, rel=1e-6)

    def test_con_afp_usa_plazo_correcto(self) -> None:
        """Con AFP a 30 años de jubilación, el divisor es 30×12=360."""
        ss = _ss_base()
        _add_ing(ss, 1_000_000)
        _add_afp(ss, saldo=0, edad_actual=35.0, edad_jubilacion=65.0)
        params = planner.make_plan_params_defaults()
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        # meta = 1M × 0.70 × 12 × 20 = 168M
        # proyeccion_afp ≈ 0 (saldo=0, no hay schedule → usa Saldo_Actual=0)
        # divisor = (65 - 35) × 12 = 360
        expected = (1_000_000 * 0.70 * 12 * 20) / 360
        assert plan[2]["monto_mensual"] == pytest.approx(expected, rel=1e-2)

    def test_con_proyeccion_que_cubre_meta_estado_completo(self) -> None:
        """Si proyección >= meta → estado completo."""
        ss = _ss_base()
        _add_ing(ss, 1_000_000)
        # meta = 168M → ponemos saldo AFP de 200M (sin schedule, usa Saldo_Actual)
        _add_afp(ss, saldo=200_000_000, edad_actual=35.0, edad_jubilacion=65.0)
        params = planner.make_plan_params_defaults()
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        assert plan[2]["estado"] == planner.ESTADO_COMPLETO

    def test_edad_jubilacion_override_en_plan_params(self) -> None:
        """plan_params["edad_jubilacion"] sobreescribe el valor del AFP."""
        ss = _ss_base()
        _add_ing(ss, 1_000_000)
        _add_afp(ss, saldo=0, edad_actual=35.0, edad_jubilacion=65.0)
        params = planner.make_plan_params_defaults()
        params["edad_jubilacion"] = 60.0  # override: jubilación a los 60
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        # divisor = (60 - 35) × 12 = 300
        expected = (1_000_000 * 0.70 * 12 * 20) / 300
        assert plan[2]["monto_mensual"] == pytest.approx(expected, rel=1e-2)


# ---------------------------------------------------------------------------
# 5. Paso 4 — Acumulación y estilo de vida
# ---------------------------------------------------------------------------


class TestPaso4Acumulacion:
    def test_bloqueado_cuando_paso1_en_curso(self) -> None:
        ss = _ss_base()
        _add_deuda(ss, "PAS_CON_001", "Deuda", 0.12)
        plan = planner.generar_plan(ss)
        assert plan[3]["estado"] == planner.ESTADO_PENDIENTE

    def test_bloqueado_cuando_paso2_en_curso(self) -> None:
        """Sin deudas pero fondo insuficiente → Paso 4 pendiente."""
        ss = _ss_base()
        _add_ese(ss, 1_000_000)
        _add_liq(ss, 1_000_000)  # solo 1 mes < 6
        params = planner.make_plan_params_defaults()
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        assert plan[3]["estado"] == planner.ESTADO_PENDIENTE

    def test_activo_cuando_paso1_y_paso2_completos(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 4_000_000)  # 8 meses > 6 → Paso 2 completo
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 6
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        assert plan[0]["estado"] == planner.ESTADO_COMPLETO
        assert plan[1]["estado"] == planner.ESTADO_COMPLETO
        assert plan[3]["estado"] == planner.ESTADO_EN_CURSO

    def test_distribucion_default_50_30_20(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 4_000_000)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 6
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        dist = plan[3]["params"]["distribucion_paso4"]
        assert dist["inversion"] == pytest.approx(0.50, rel=1e-3)
        assert dist["estilo_vida"] == pytest.approx(0.30, rel=1e-3)
        assert dist["libre"] == pytest.approx(0.20, rel=1e-3)

    def test_distribucion_editable_persiste_en_plan(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 4_000_000)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 6
        params["distribucion_paso4"] = {"inversion": 0.60, "estilo_vida": 0.25, "libre": 0.15}
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        dist = plan[3]["params"]["distribucion_paso4"]
        # Los porcentajes deben ser normalizados al 100% (suman 1.0 → sin cambio)
        total = dist["inversion"] + dist["estilo_vida"] + dist["libre"]
        assert total == pytest.approx(1.0, rel=1e-6)
        assert dist["inversion"] == pytest.approx(0.60, rel=1e-3)

    def test_diagnostico_incluye_distribucion_numerica(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 4_000_000)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 6
        ss["plan_params"] = params
        plan = planner.generar_plan(ss)
        # El diagnóstico debe mencionar el margen disponible
        assert "$" in plan[3]["diagnostico"]

    def test_monto_mensual_igual_a_margen_libre(self) -> None:
        """Cuando P1+P2+P3 están completos, P4 recibe el margen libre íntegro."""
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 4_000_000)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 200_000, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 100_000, "Moneda": "CLP"}
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 6
        ss["plan_params"] = params
        # AFP con proyección enorme → brecha ≤ 0 → P3 completo → no consume margen
        ss["positions"]["AFP_COBERTURA"] = {
            "Clase": "Prevision_AFP",
            "Saldo_Actual": 1_000_000_000,
            "Edad_Actual": 35.0,
            "Edad_Jubilacion": 65.0,
            "Aporte_Mensual": 0,
            "Tasa_Anual": 0.0,
            "Moneda": "CLP",
        }
        plan = planner.generar_plan(ss)
        # margen = 2M - 500K - 200K - 100K = 1.2M
        # P1 completo, P2 completo, P3 completo → P4 recibe 1.2M íntegros
        assert plan[3]["monto_mensual"] == pytest.approx(1_200_000)


# ---------------------------------------------------------------------------
# 6. make_plan_params_defaults
# ---------------------------------------------------------------------------


class TestMakePlanParamsDefaults:
    def test_retorna_dict_con_claves_requeridas(self) -> None:
        d = planner.make_plan_params_defaults()
        assert "tasa_reemplazo" in d
        assert "anos_retiro" in d
        assert "meses_reserva_meta" in d
        assert "distribucion_paso4" in d

    def test_defaults_correctos(self) -> None:
        d = planner.make_plan_params_defaults()
        assert d["tasa_reemplazo"] == pytest.approx(0.70)
        assert d["anos_retiro"] == 20
        assert d["meses_reserva_meta"] == 6

    def test_distribucion_por_defecto_suma_1(self) -> None:
        d = planner.make_plan_params_defaults()
        dist = d["distribucion_paso4"]
        total = dist["inversion"] + dist["estilo_vida"] + dist["libre"]
        assert total == pytest.approx(1.0)

    def test_cada_llamada_retorna_objeto_fresco(self) -> None:
        """Modificar un resultado no debe afectar al siguiente."""
        d1 = planner.make_plan_params_defaults()
        d1["distribucion_paso4"]["inversion"] = 0.99
        d2 = planner.make_plan_params_defaults()
        assert d2["distribucion_paso4"]["inversion"] == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# 7. Integración — parámetros editables cambian el plan completo
# ---------------------------------------------------------------------------


class TestParametrosEditablesCambianPlan:
    """Verifica que editar plan_params recalcula el plan sin efectos secundarios."""

    def _ss_con_deuda_y_fondo(self) -> dict:
        ss = _ss_base()
        _add_ing(ss, 3_000_000)
        _add_ese(ss, 800_000)
        _add_liq(ss, 1_000_000)  # ~1.25 meses < 6
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 200_000, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        _add_deuda(ss, "PAS_CON_001", "Crédito", 0.18, monto=5_000_000)
        return ss

    def test_cambiar_meses_reserva_afecta_paso2(self) -> None:
        ss = self._ss_con_deuda_y_fondo()
        params_6 = planner.make_plan_params_defaults()
        params_6["meses_reserva_meta"] = 6
        ss["plan_params"] = params_6
        plan_6 = planner.generar_plan(ss)
        assert "6" in plan_6[1]["titulo"]

        params_9 = planner.make_plan_params_defaults()
        params_9["meses_reserva_meta"] = 9
        ss["plan_params"] = params_9
        plan_9 = planner.generar_plan(ss)
        assert "9" in plan_9[1]["titulo"]
        # Con meta mayor, el plazo debe ser mayor o igual
        assert plan_9[1]["plazo_meses"] >= plan_6[1]["plazo_meses"]

    def test_cambiar_tasa_reemplazo_afecta_paso3(self) -> None:
        """Menor tasa de reemplazo → menor aporte requerido en Paso 3.

        Usa setup con P1+P2 completos para que el margen disponible llegue al
        Paso 3 sin ser consumido por deudas o fondo.
        Con AFP (divisor=360): aporte_70≈933K, aporte_50≈667K — ambos < margen(1.5M).
        """
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 4_000_000)  # 8 meses > 6 → P2 completo
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        _add_afp(ss, saldo=0, edad_actual=35.0, edad_jubilacion=65.0)

        params_70 = planner.make_plan_params_defaults()
        params_70["tasa_reemplazo"] = 0.70
        ss["plan_params"] = params_70
        plan_70 = planner.generar_plan(ss)
        monto_70 = plan_70[2]["monto_mensual"]

        params_50 = planner.make_plan_params_defaults()
        params_50["tasa_reemplazo"] = 0.50
        ss["plan_params"] = params_50
        plan_50 = planner.generar_plan(ss)
        monto_50 = plan_50[2]["monto_mensual"]

        # Menor tasa de reemplazo → menor brecha → menor aporte requerido
        assert monto_50 < monto_70

    def test_paso4_se_activa_tras_completar_deuda_y_fondo(self) -> None:
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}

        # Sin fondo ni deudas saldadas → Paso 4 pendiente
        _add_liq(ss, 0)
        params = planner.make_plan_params_defaults()
        params["meses_reserva_meta"] = 6
        ss["plan_params"] = params
        plan_sin = planner.generar_plan(ss)
        assert plan_sin[3]["estado"] == planner.ESTADO_PENDIENTE

        # Con fondo completo y sin deudas → Paso 4 activo
        _add_liq(ss, 3_100_000)  # 6.2 meses esenciales
        plan_con = planner.generar_plan(ss)
        assert plan_con[0]["estado"] == planner.ESTADO_COMPLETO
        assert plan_con[1]["estado"] == planner.ESTADO_COMPLETO
        assert plan_con[3]["estado"] == planner.ESTADO_EN_CURSO


# ---------------------------------------------------------------------------
# 8. Coordinación de margen — cascada P1→P2→P3→P4
# ---------------------------------------------------------------------------


class TestMargenCascada:
    """Verifica que el margen se reparte correctamente entre los pasos."""

    def _ss_p1p2_completos(
        self,
        ingreso: float = 5_000_000,
        esenciales: float = 4_000_000,
    ) -> dict:
        """Session state sin deudas y con fondo completo (P1 y P2 completos)."""
        ss = _ss_base()
        _add_ing(ss, ingreso)
        _add_ese(ss, esenciales)
        # Fondo = 8 meses de esenciales > 6 → Paso 2 completo
        _add_liq(ss, esenciales * 8)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        return ss

    def test_paso3_monto_capped_por_margen_disponible(self) -> None:
        """Si aporte_ideal > margen disponible, Paso 3 devuelve min(ideal, margen)."""
        # margen = 5M - 4M = 1M
        # Sin AFP → divisor = 120 (fallback)
        # meta = 5M × 0.70 × 12 × 20 = 840M → aporte_ideal = 840M/120 = 7M
        # margen_restante_p3 = 1M → cap → monto_paso3 = 1M
        ss = self._ss_p1p2_completos(ingreso=5_000_000, esenciales=4_000_000)
        plan = planner.generar_plan(ss)

        assert plan[0]["estado"] == planner.ESTADO_COMPLETO, "Paso 1 debe ser completo"
        assert plan[1]["estado"] == planner.ESTADO_COMPLETO, "Paso 2 debe ser completo"
        # El monto del Paso 3 no puede superar el margen libre (1M)
        assert plan[2]["monto_mensual"] == pytest.approx(1_000_000, rel=1e-3)

    def test_paso4_monto_cero_cuando_margen_agotado_por_p3(self) -> None:
        """Cuando P3 consume todo el margen restante, P4 recibe 0 aunque esté activo."""
        # misma configuración → P3 toma todo el margen (1M)
        # margen_restante_p4 = 1M - 0 - 0 - 1M = 0
        ss = self._ss_p1p2_completos(ingreso=5_000_000, esenciales=4_000_000)
        plan = planner.generar_plan(ss)

        # P4 está EN_CURSO (P1+P2 completos) pero sin margen
        assert plan[3]["estado"] == planner.ESTADO_EN_CURSO
        assert plan[3]["monto_mensual"] == pytest.approx(0.0, abs=1.0)

    def test_paso4_recibe_margen_restante_tras_p3(self) -> None:
        """Con brecha pensional pequeña, P4 obtiene el margen sobrante."""
        # ingreso=2M, ese=500K, margen=1.5M, P2 completo (liq=4M > 3M=500K*6)
        # AFP con saldo grande → brecha pequeña → aporte_ideal < margen
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 4_000_000)
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        # AFP con proyección muy alta → brecha = 0 → P3 completo → P4 recibe margen completo
        ss["positions"]["AFP_GRANDE"] = {
            "Clase": "Prevision_AFP",
            "Saldo_Actual": 1_000_000_000,  # 1 billón → proyección > meta
            "Edad_Actual": 35.0,
            "Edad_Jubilacion": 65.0,
            "Aporte_Mensual": 0,
            "Tasa_Anual": 0.0,
            "Moneda": "CLP",
        }
        plan = planner.generar_plan(ss)
        # P3 completo → monto_p3 = 0 → P4 recibe margen completo = 1.5M
        assert plan[2]["estado"] == planner.ESTADO_COMPLETO
        assert plan[3]["monto_mensual"] == pytest.approx(1_500_000, rel=1e-3)


# ---------------------------------------------------------------------------
# 9. Formato de texto — sin markdown en acciones, espacios correctos
# ---------------------------------------------------------------------------


class TestFormatoTexto:
    """Verifica que las cadenas de texto tienen formato correcto."""

    def test_accion_paso1_sin_asteriscos_markdown(self) -> None:
        """La acción del Paso 1 no debe contener marcado bold (**)."""
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 0)
        _add_deuda(ss, "PAS_CON_001", "Crédito BCI", 0.12, monto=1_000_000)
        plan = planner.generar_plan(ss)
        accion_p1 = plan[0]["accion"]
        assert "**" not in accion_p1, (
            f"Paso 1 accion tiene marcado markdown: {accion_p1!r}"
        )

    def test_accion_paso3_guion_con_espacios(self) -> None:
        """El em-dash (—) en la acción del Paso 3 debe tener espacio antes y después."""
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 200_000)
        _add_liq(ss, 2_000_000)  # 10 meses → P2 completo
        ss["positions"]["GAS_IMP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        ss["positions"]["GAS_ASP_BUCKET"] = {"Monto_Mensual": 0, "Moneda": "CLP"}
        _add_afp(ss, saldo=0, edad_actual=35, edad_jubilacion=65)
        plan = planner.generar_plan(ss)
        accion_p3 = plan[2]["accion"]
        # Solo verificar si la acción contiene un em-dash
        if "—" in accion_p3:
            idx = accion_p3.index("—")
            assert accion_p3[idx - 1] == " ", (
                f"Sin espacio antes del guión en Paso 3: {accion_p3!r}"
            )
            assert accion_p3[idx + 1] == " ", (
                f"Sin espacio después del guión en Paso 3: {accion_p3!r}"
            )

    def test_diagnostico_sin_numeros_concatenados(self) -> None:
        """Los diagnósticos no deben tener números pegados a palabras ('$500,000sin')."""
        ss = _ss_base()
        _add_ing(ss, 2_000_000)
        _add_ese(ss, 500_000)
        _add_liq(ss, 0)
        _add_deuda(ss, "PAS_CON_001", "Crédito", 0.15)
        plan = planner.generar_plan(ss)
        for paso in plan:
            # Verificar que '$ X' siempre tiene espacio después del signo $
            diag = paso["diagnostico"]
            if "$" in diag:
                idx = diag.index("$")
                # El carácter después de $ debe ser espacio o dígito (nunca letra)
                post = diag[idx + 1] if idx + 1 < len(diag) else " "
                assert post in (" ", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"), (
                    f"Paso {paso['numero']}: $ seguido de {post!r} — posible concatenación"
                )
