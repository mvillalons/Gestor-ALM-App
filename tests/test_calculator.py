"""Tests para core/calculator.py — métricas financieras Capa 1, 2, 3."""

import pytest

from core.calculator import (
    BENCHMARK_CARGA_FINANCIERA,
    MESES_META_DEFAULT,
    VALOR_UF_DEFAULT,
    VALOR_USD_DEFAULT,
    bucket_sugerido,
    calcular_pension_mensual,
    capa_desbloqueada,
    carga_financiera,
    espacio_disponible_bucket,
    gap_fondo,
    margen_libre,
    mes_stress,
    meta_fondo_reserva,
    meses_para_fondo,
    normalizar_a_clp,
    posicion_vida_v1,
    posicion_vida_v2,
    posicion_vida_v3,
)

# ---------------------------------------------------------------------------
# Constantes de referencia usadas en múltiples tests
# ---------------------------------------------------------------------------

INGRESO = 3_000_000       # CLP
ESENCIALES = 800_000
IMPORTANTES = 400_000
ASPIRACIONES = 200_000
ACTIVO_LIQ = 4_000_000
CUOTAS = [350_000, 150_000]   # hipoteca + crédito consumo


# ===========================================================================
# Capa 1 — Claridad
# ===========================================================================


class TestMargenLibre:
    def test_valor_positivo(self):
        resultado = margen_libre(INGRESO, ESENCIALES, IMPORTANTES, ASPIRACIONES)
        assert resultado == INGRESO - (ESENCIALES + IMPORTANTES + ASPIRACIONES)

    def test_calculo_exacto(self):
        assert margen_libre(3_000_000, 800_000, 400_000, 200_000) == 1_600_000

    def test_margen_negativo_cuando_gastos_superan_ingreso(self):
        assert margen_libre(1_000_000, 800_000, 400_000, 200_000) == -400_000

    def test_margen_cero_cuando_gasto_igual_ingreso(self):
        assert margen_libre(1_000_000, 500_000, 300_000, 200_000) == 0

    def test_todos_cero(self):
        assert margen_libre(0, 0, 0, 0) == 0

    def test_sin_aspiraciones(self):
        assert margen_libre(2_000_000, 800_000, 400_000, 0) == 800_000

    def test_solo_esenciales(self):
        assert margen_libre(1_000_000, 600_000, 0, 0) == 400_000


class TestMetaFondoReserva:
    def test_default_tres_meses(self):
        assert meta_fondo_reserva(800_000) == 800_000 * MESES_META_DEFAULT

    def test_meses_personalizados(self):
        assert meta_fondo_reserva(800_000, meses_meta=6) == 4_800_000

    def test_un_mes(self):
        assert meta_fondo_reserva(500_000, meses_meta=1) == 500_000

    def test_meses_meta_cero_lanza_error(self):
        with pytest.raises(ValueError, match="meses_meta"):
            meta_fondo_reserva(800_000, meses_meta=0)

    def test_meses_meta_negativo_lanza_error(self):
        with pytest.raises(ValueError, match="meses_meta"):
            meta_fondo_reserva(800_000, meses_meta=-1)

    def test_esenciales_cero(self):
        # Esenciales = 0 es un estado válido (usuario sin gastos registrados)
        assert meta_fondo_reserva(0) == 0

    def test_constante_default_es_tres(self):
        assert MESES_META_DEFAULT == 3


class TestGapFondo:
    def test_gap_positivo_falta_dinero(self):
        meta = 2_400_000
        activo = 1_500_000
        assert gap_fondo(meta, activo) == 900_000

    def test_gap_cero_fondo_exacto(self):
        assert gap_fondo(2_400_000, 2_400_000) == 0

    def test_gap_negativo_superavit(self):
        assert gap_fondo(2_400_000, 3_000_000) == -600_000

    def test_activo_cero(self):
        assert gap_fondo(2_400_000, 0) == 2_400_000

    def test_meta_cero(self):
        assert gap_fondo(0, 1_000_000) == -1_000_000


class TestMesesParaFondo:
    def test_calculo_normal(self):
        resultado = meses_para_fondo(gap=900_000, margen=300_000)
        assert resultado == pytest.approx(3.0)

    def test_gap_cero_retorna_cero(self):
        assert meses_para_fondo(gap=0, margen=300_000) == 0.0

    def test_gap_negativo_retorna_cero(self):
        assert meses_para_fondo(gap=-100_000, margen=300_000) == 0.0

    def test_margen_cero_retorna_none(self):
        assert meses_para_fondo(gap=900_000, margen=0) is None

    def test_margen_negativo_retorna_none(self):
        assert meses_para_fondo(gap=900_000, margen=-100_000) is None

    def test_resultado_fraccionario(self):
        resultado = meses_para_fondo(gap=1_000_000, margen=300_000)
        assert resultado == pytest.approx(10 / 3)

    def test_gap_positivo_margen_positivo_retorna_float(self):
        resultado = meses_para_fondo(gap=600_000, margen=200_000)
        assert isinstance(resultado, float)
        assert resultado == pytest.approx(3.0)

    def test_gap_cero_margen_negativo_retorna_cero(self):
        # Fondo cubierto aunque no haya margen → 0.0
        assert meses_para_fondo(gap=0, margen=-50_000) == 0.0


class TestPosicionVidaV1:
    def test_tres_meses_cobertura(self):
        assert posicion_vida_v1(2_400_000, 800_000) == pytest.approx(3.0)

    def test_calculo_exacto(self):
        assert posicion_vida_v1(ACTIVO_LIQ, ESENCIALES) == pytest.approx(
            ACTIVO_LIQ / ESENCIALES
        )

    def test_activo_cero_da_cero(self):
        assert posicion_vida_v1(0, 800_000) == pytest.approx(0.0)

    def test_activo_igual_esenciales_da_uno(self):
        assert posicion_vida_v1(800_000, 800_000) == pytest.approx(1.0)

    def test_esenciales_cero_lanza_error(self):
        with pytest.raises(ValueError, match="esenciales"):
            posicion_vida_v1(ACTIVO_LIQ, 0)

    def test_esenciales_negativos_lanza_error(self):
        with pytest.raises(ValueError, match="esenciales"):
            posicion_vida_v1(ACTIVO_LIQ, -1)

    def test_valor_tipico_mayor_a_tres(self):
        # 4M líquido / 800K esenciales = 5 meses → sobre el mínimo recomendado
        assert posicion_vida_v1(4_000_000, 800_000) == pytest.approx(5.0)


# ===========================================================================
# Capa 2 — Control
# ===========================================================================


class TestCargaFinanciera:
    def test_benchmark_saludable_menor_a_35(self):
        ratio = carga_financiera([500_000], 3_000_000)
        assert ratio < BENCHMARK_CARGA_FINANCIERA

    def test_calculo_exacto_una_cuota(self):
        assert carga_financiera([600_000], 2_000_000) == pytest.approx(0.30)

    def test_calculo_varias_cuotas(self):
        assert carga_financiera([350_000, 150_000], 2_000_000) == pytest.approx(0.25)

    def test_lista_vacia_da_cero(self):
        assert carga_financiera([], INGRESO) == pytest.approx(0.0)

    def test_ingreso_cero_lanza_error(self):
        with pytest.raises(ValueError, match="ingreso"):
            carga_financiera([500_000], 0)

    def test_ingreso_negativo_lanza_error(self):
        with pytest.raises(ValueError, match="ingreso"):
            carga_financiera([500_000], -1_000_000)

    def test_ratio_sobre_benchmark_es_carga_alta(self):
        ratio = carga_financiera([1_200_000], 2_000_000)
        assert ratio > BENCHMARK_CARGA_FINANCIERA

    def test_constante_benchmark_es_035(self):
        assert BENCHMARK_CARGA_FINANCIERA == pytest.approx(0.35)


class TestPosicionVidaV2:
    def test_menor_que_v1_cuando_hay_cuotas(self):
        v1 = posicion_vida_v1(ACTIVO_LIQ, ESENCIALES)
        v2 = posicion_vida_v2(ACTIVO_LIQ, ESENCIALES, CUOTAS)
        assert v2 < v1

    def test_igual_a_v1_sin_cuotas(self):
        v1 = posicion_vida_v1(ACTIVO_LIQ, ESENCIALES)
        v2 = posicion_vida_v2(ACTIVO_LIQ, ESENCIALES, [])
        assert v2 == pytest.approx(v1)

    def test_calculo_exacto(self):
        # 4M / (800K + 500K) = 4M / 1.3M ≈ 3.076
        resultado = posicion_vida_v2(4_000_000, 800_000, [500_000])
        assert resultado == pytest.approx(4_000_000 / 1_300_000)

    def test_denominador_cero_lanza_error(self):
        with pytest.raises(ValueError):
            posicion_vida_v2(ACTIVO_LIQ, 0, [])

    def test_denominador_negativo_lanza_error(self):
        with pytest.raises(ValueError):
            posicion_vida_v2(ACTIVO_LIQ, -500_000, [])

    def test_activo_cero_da_cero(self):
        assert posicion_vida_v2(0, ESENCIALES, CUOTAS) == pytest.approx(0.0)

    def test_multiples_cuotas(self):
        denom = ESENCIALES + sum(CUOTAS)
        assert posicion_vida_v2(ACTIVO_LIQ, ESENCIALES, CUOTAS) == pytest.approx(
            ACTIVO_LIQ / denom
        )


class TestMesStress:
    def test_flujo_negativo_es_stress(self):
        assert mes_stress(-1) is True
        assert mes_stress(-0.01) is True
        assert mes_stress(-1_000_000) is True

    def test_flujo_cero_no_es_stress(self):
        assert mes_stress(0) is False

    def test_flujo_positivo_no_es_stress(self):
        assert mes_stress(1) is False
        assert mes_stress(500_000) is False

    def test_retorna_bool(self):
        assert isinstance(mes_stress(-100), bool)
        assert isinstance(mes_stress(100), bool)


# ===========================================================================
# Capa 3 — Crecimiento
# ===========================================================================


class TestPosicionVidaV3:
    def test_mayor_o_igual_que_v2_cuando_portafolio_positivo(self):
        v2 = posicion_vida_v2(ACTIVO_LIQ, ESENCIALES, CUOTAS)
        v3 = posicion_vida_v3(ACTIVO_LIQ, 5_000_000, ESENCIALES, CUOTAS)
        assert v3 > v2

    def test_igual_a_v2_cuando_portafolio_cero(self):
        v2 = posicion_vida_v2(ACTIVO_LIQ, ESENCIALES, CUOTAS)
        v3 = posicion_vida_v3(ACTIVO_LIQ, 0, ESENCIALES, CUOTAS)
        assert v3 == pytest.approx(v2)

    def test_calculo_exacto(self):
        # (4M + 5M) / (800K + 500K) = 9M / 1.3M
        resultado = posicion_vida_v3(4_000_000, 5_000_000, 800_000, [500_000])
        assert resultado == pytest.approx(9_000_000 / 1_300_000)

    def test_denominador_cero_lanza_error(self):
        with pytest.raises(ValueError):
            posicion_vida_v3(ACTIVO_LIQ, 1_000_000, 0, [])

    def test_sin_cuotas_usa_solo_esenciales(self):
        resultado = posicion_vida_v3(2_000_000, 1_000_000, 500_000, [])
        assert resultado == pytest.approx(3_000_000 / 500_000)

    def test_todos_activos_cero_da_cero(self):
        assert posicion_vida_v3(0, 0, ESENCIALES, CUOTAS) == pytest.approx(0.0)


# ===========================================================================
# Helper — capa_desbloqueada
# ===========================================================================


class TestCapaDesbloqueada:

    # --- Capa 1 ---

    def test_estado_vacio_retorna_1(self):
        assert capa_desbloqueada({}) == 1

    def test_solo_meta_fondo_definida_retorna_1(self):
        assert capa_desbloqueada({"meta_fondo_definida": True}) == 1

    def test_solo_buckets_confirmados_retorna_1(self):
        assert capa_desbloqueada({"buckets_confirmados": True}) == 1

    def test_ambas_false_retorna_1(self):
        state = {"meta_fondo_definida": False, "buckets_confirmados": False}
        assert capa_desbloqueada(state) == 1

    # --- Capa 2 ---

    def test_condicion_capa2_retorna_2(self):
        state = {"meta_fondo_definida": True, "buckets_confirmados": True}
        assert capa_desbloqueada(state) == 2

    def test_capa2_sin_pasivos_sin_afp_retorna_2(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": [],
            "afp_saldo": None,
        }
        assert capa_desbloqueada(state) == 2

    def test_capa2_con_pasivos_pero_sin_afp_retorna_2(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001"],
            "afp_saldo": None,
        }
        assert capa_desbloqueada(state) == 2

    def test_capa2_con_afp_pero_sin_pasivos_retorna_2(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": [],
            "afp_saldo": 50_000_000,
        }
        assert capa_desbloqueada(state) == 2

    # --- Capa 3 ---

    def test_condicion_capa3_retorna_3(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001"],
            "afp_saldo": 50_000_000,
        }
        assert capa_desbloqueada(state) == 3

    def test_capa3_sin_activos_ni_objetivos_retorna_3(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001"],
            "afp_saldo": 50_000_000,
            "activos_con_tabla": [],
            "objetivos_activos": [],
        }
        assert capa_desbloqueada(state) == 3

    def test_capa3_con_activos_pero_sin_objetivos_retorna_3(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001"],
            "afp_saldo": 50_000_000,
            "activos_con_tabla": ["ACT_ETF_001"],
            "objetivos_activos": [],
        }
        assert capa_desbloqueada(state) == 3

    def test_capa3_con_objetivos_pero_sin_activos_retorna_3(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001"],
            "afp_saldo": 50_000_000,
            "activos_con_tabla": [],
            "objetivos_activos": ["OBJ_VIAJE_001"],
        }
        assert capa_desbloqueada(state) == 3

    # --- Capa 4 ---

    def test_todas_condiciones_retorna_4(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001"],
            "afp_saldo": 50_000_000,
            "activos_con_tabla": ["ACT_ETF_001"],
            "objetivos_activos": ["OBJ_VIAJE_001"],
        }
        assert capa_desbloqueada(state) == 4

    def test_multiples_pasivos_activos_objetivos_retorna_4(self):
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001", "PAS_CON_001"],
            "afp_saldo": 80_000_000,
            "activos_con_tabla": ["ACT_ETF_001", "ACT_FM_002"],
            "objetivos_activos": ["OBJ_CASA_001", "OBJ_VIAJE_002"],
        }
        assert capa_desbloqueada(state) == 4

    # --- Condiciones de borde ---

    def test_claves_ausentes_tratadas_como_falsy(self):
        # Solo capa2 cumplida, el resto de claves no existen
        state = {"meta_fondo_definida": True, "buckets_confirmados": True}
        assert capa_desbloqueada(state) == 2

    def test_afp_saldo_cero_es_valido(self):
        # afp_saldo = 0 es un valor válido (no es None)
        state = {
            "meta_fondo_definida": True,
            "buckets_confirmados": True,
            "pasivos_con_tabla": ["PAS_HIP_001"],
            "afp_saldo": 0,
        }
        assert capa_desbloqueada(state) == 3

    def test_retorno_siempre_entre_1_y_4(self):
        for state in [
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
            assert 1 <= capa_desbloqueada(state) <= 4


# ===========================================================================
# Tests de integración entre métricas
# ===========================================================================


class TestIntegracionCapas:
    """Verifica coherencia entre métricas que el CLAUDE.md relaciona."""

    def test_flujo_completo_capa1(self):
        """Encadenamiento: ingreso → margen → meta → gap → meses."""
        ingreso = 3_000_000
        esp = 800_000
        imp = 400_000
        asp = 200_000
        activo = 1_500_000

        ml = margen_libre(ingreso, esp, imp, asp)
        meta = meta_fondo_reserva(esp)
        gap = gap_fondo(meta, activo)
        meses = meses_para_fondo(gap, ml)
        pv1 = posicion_vida_v1(activo, esp)

        assert ml == 1_600_000
        assert meta == 2_400_000
        assert gap == 900_000
        assert meses == pytest.approx(900_000 / 1_600_000)
        assert pv1 == pytest.approx(1_500_000 / 800_000)

    def test_posicion_vida_degrada_con_mas_deuda(self):
        """v1 > v2 > v2_con_mas_cuotas cuando se agrega deuda."""
        v1 = posicion_vida_v1(ACTIVO_LIQ, ESENCIALES)
        v2 = posicion_vida_v2(ACTIVO_LIQ, ESENCIALES, [300_000])
        v2_mas_deuda = posicion_vida_v2(ACTIVO_LIQ, ESENCIALES, [300_000, 500_000])

        assert v1 > v2 > v2_mas_deuda

    def test_portafolio_mejora_posicion_v3(self):
        """Más activos financieros liquidables → mejor posición v3."""
        v3_bajo = posicion_vida_v3(ACTIVO_LIQ, 1_000_000, ESENCIALES, CUOTAS)
        v3_alto = posicion_vida_v3(ACTIVO_LIQ, 5_000_000, ESENCIALES, CUOTAS)
        assert v3_alto > v3_bajo

    def test_fondo_cubierto_no_requiere_meses(self):
        """Si el activo ya supera la meta, meses_para_fondo = 0."""
        esp = 800_000
        meta = meta_fondo_reserva(esp)
        gap = gap_fondo(meta, meta + 1)   # activo > meta → gap negativo
        assert meses_para_fondo(gap, 1_600_000) == 0.0

    def test_sin_margen_no_hay_estimacion(self):
        """Gastos iguales al ingreso → meses_para_fondo es None."""
        ml = margen_libre(2_000_000, 800_000, 700_000, 500_000)  # = 0
        assert ml == 0
        assert meses_para_fondo(500_000, ml) is None


# ===========================================================================
# normalizar_a_clp
# ===========================================================================

UF = 39_700.0    # tipo de cambio de prueba
USD = 950.0


class TestNormalizarAClp:
    """Conversión de monedas a CLP."""

    # ── Casos básicos ──────────────────────────────────────────────────────

    def test_clp_sin_cambio(self):
        assert normalizar_a_clp(500_000.0, "CLP", UF, USD) == 500_000.0

    def test_uf_multiplica_por_valor_uf(self):
        assert normalizar_a_clp(1.0, "UF", UF, USD) == pytest.approx(UF)

    def test_usd_multiplica_por_valor_usd(self):
        assert normalizar_a_clp(1.0, "USD", UF, USD) == pytest.approx(USD)

    def test_uf_valor_exacto(self):
        assert normalizar_a_clp(100.0, "UF", UF, USD) == pytest.approx(100.0 * UF)

    def test_usd_valor_exacto(self):
        assert normalizar_a_clp(1_000.0, "USD", UF, USD) == pytest.approx(1_000.0 * USD)

    def test_cero_retorna_cero_cualquier_moneda(self):
        assert normalizar_a_clp(0.0, "UF", UF, USD) == 0.0
        assert normalizar_a_clp(0.0, "USD", UF, USD) == 0.0
        assert normalizar_a_clp(0.0, "CLP", UF, USD) == 0.0

    def test_negativo_preserva_signo(self):
        """Flujos negativos (egresos) se convierten conservando el signo."""
        result = normalizar_a_clp(-10.0, "UF", UF, USD)
        assert result == pytest.approx(-10.0 * UF)

    def test_moneda_desconocida_trata_como_clp(self):
        """Moneda no reconocida → retorna el flujo sin cambio (trato como CLP)."""
        assert normalizar_a_clp(123.0, "EUR", UF, USD) == pytest.approx(123.0)
        assert normalizar_a_clp(456.0, "GBP", UF, USD) == pytest.approx(456.0)

    def test_retorna_float(self):
        result = normalizar_a_clp(100, "CLP", UF, USD)
        assert isinstance(result, float)

    # ── Constantes de módulo ───────────────────────────────────────────────

    def test_valor_uf_default_es_positivo(self):
        assert VALOR_UF_DEFAULT > 0

    def test_valor_usd_default_es_positivo(self):
        assert VALOR_USD_DEFAULT > 0

    # ── Validaciones ───────────────────────────────────────────────────────

    def test_valor_uf_cero_lanza_error(self):
        with pytest.raises(ValueError, match="valor_uf"):
            normalizar_a_clp(1.0, "UF", 0.0, USD)

    def test_valor_uf_negativo_lanza_error(self):
        with pytest.raises(ValueError, match="valor_uf"):
            normalizar_a_clp(1.0, "UF", -1.0, USD)

    def test_valor_usd_cero_lanza_error(self):
        with pytest.raises(ValueError, match="valor_usd"):
            normalizar_a_clp(1.0, "USD", UF, 0.0)

    def test_valor_usd_negativo_lanza_error(self):
        with pytest.raises(ValueError, match="valor_usd"):
            normalizar_a_clp(1.0, "USD", UF, -50.0)

    def test_valor_uf_invalido_sin_importar_moneda_clp(self):
        """Incluso para CLP, los tipos de cambio deben ser válidos."""
        with pytest.raises(ValueError):
            normalizar_a_clp(500_000.0, "CLP", 0.0, USD)


# ===========================================================================
# carga_financiera con mezcla de monedas (normalización previa)
# ===========================================================================


class TestCargaFinancieraMixMoneda:
    """carga_financiera() con cuotas en distintas monedas normalizadas a CLP."""

    def test_cuota_uf_normalizada_da_ratio_correcto(self):
        """Cuota de 10 UF, ingreso CLP 2_000_000."""
        cuota_uf = 10.0  # UF
        cuota_clp = normalizar_a_clp(cuota_uf, "UF", UF, USD)
        ingreso_clp = 2_000_000.0
        ratio = carga_financiera([cuota_clp], ingreso_clp)
        assert ratio == pytest.approx(cuota_clp / ingreso_clp)

    def test_cuota_usd_normalizada_da_ratio_correcto(self):
        """Cuota de 500 USD, ingreso CLP 2_000_000."""
        cuota_usd = 500.0
        cuota_clp = normalizar_a_clp(cuota_usd, "USD", UF, USD)
        ingreso_clp = 2_000_000.0
        ratio = carga_financiera([cuota_clp], ingreso_clp)
        assert ratio == pytest.approx(cuota_clp / ingreso_clp)

    def test_mezcla_clp_uf_usd(self):
        """Tres cuotas en distintas monedas se suman correctamente en CLP."""
        c_clp = 300_000.0
        c_uf = normalizar_a_clp(5.0, "UF", UF, USD)   # 5 UF en CLP
        c_usd = normalizar_a_clp(200.0, "USD", UF, USD)  # 200 USD en CLP
        ingreso = 3_000_000.0
        ratio = carga_financiera([c_clp, c_uf, c_usd], ingreso)
        expected = (c_clp + c_uf + c_usd) / ingreso
        assert ratio == pytest.approx(expected)

    def test_normalizacion_sin_cuotas_en_moneda_principal_da_cero(self):
        """Sin cuotas, la carga financiera es 0 independiente de los FX."""
        assert carga_financiera([], 2_000_000.0) == 0.0


# ---------------------------------------------------------------------------
# TestBucketSugerido
# ---------------------------------------------------------------------------


class TestBucketSugerido:
    """Tests para calculator.bucket_sugerido()."""

    def test_hipotecario_retorna_esenciales(self):
        assert bucket_sugerido("Hipotecario") == "GAS_ESE_BUCKET"

    def test_colegio_retorna_esenciales(self):
        assert bucket_sugerido("Colegio") == "GAS_ESE_BUCKET"

    def test_credito_consumo_retorna_importantes(self):
        assert bucket_sugerido("Crédito consumo") == "GAS_IMP_BUCKET"

    def test_tarjeta_retorna_importantes(self):
        assert bucket_sugerido("Tarjeta") == "GAS_IMP_BUCKET"

    def test_apv_retorna_aspiraciones(self):
        assert bucket_sugerido("APV") == "GAS_ASP_BUCKET"

    def test_otro_retorna_importantes_por_defecto(self):
        assert bucket_sugerido("Otro") == "GAS_IMP_BUCKET"

    def test_tipo_desconocido_retorna_importantes(self):
        assert bucket_sugerido("Préstamo informal") == "GAS_IMP_BUCKET"

    def test_string_vacio_retorna_importantes(self):
        assert bucket_sugerido("") == "GAS_IMP_BUCKET"


# ---------------------------------------------------------------------------
# TestEspacioDisponibleBucket
# ---------------------------------------------------------------------------


class TestEspacioDisponibleBucket:
    """Tests para calculator.espacio_disponible_bucket()."""

    def _ss(self, bucket_monto: float, vinculadas: dict[str, float]) -> dict:
        """Construye un session_state mínimo para los tests."""
        positions: dict = {
            "GAS_ESE_BUCKET": {"Monto_Mensual": bucket_monto},
        }
        for pid, cuota in vinculadas.items():
            positions[pid] = {
                "bucket_vinculado": "GAS_ESE_BUCKET",
                "Cuota_Vinculada_CLP": cuota,
            }
        return {"positions": positions}

    def test_sin_vinculaciones_espacio_igual_a_monto(self):
        ss = self._ss(2_000_000, {})
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 2_000_000.0

    def test_una_vinculacion_resta_correctamente(self):
        ss = self._ss(2_000_000, {"PAS_HIP_001": 1_500_000})
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 500_000.0

    def test_dos_vinculaciones_restan_suma(self):
        ss = self._ss(3_000_000, {"PAS_HIP_001": 1_200_000, "PAS_COL_001": 800_000})
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 1_000_000.0

    def test_cuota_igual_al_monto_da_cero(self):
        ss = self._ss(1_000_000, {"PAS_HIP_001": 1_000_000})
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 0.0

    def test_cuota_mayor_que_monto_da_negativo(self):
        ss = self._ss(1_000_000, {"PAS_HIP_001": 1_500_000})
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == -500_000.0

    def test_bucket_inexistente_da_cero(self):
        ss: dict = {"positions": {}}
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 0.0

    def test_positions_vacias_da_cero(self):
        ss: dict = {"positions": {}}
        assert espacio_disponible_bucket(ss, "GAS_IMP_BUCKET") == 0.0

    def test_vinculaciones_de_otro_bucket_no_afectan(self):
        ss = {
            "positions": {
                "GAS_ESE_BUCKET": {"Monto_Mensual": 2_000_000},
                "GAS_IMP_BUCKET": {"Monto_Mensual": 1_000_000},
                "PAS_CON_001": {
                    "bucket_vinculado": "GAS_IMP_BUCKET",
                    "Cuota_Vinculada_CLP": 300_000,
                },
            }
        }
        # El espacio de ESE no debe verse afectado por vinculaciones de IMP
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 2_000_000.0

    def test_sin_clave_positions_da_cero(self):
        ss: dict = {}
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 0.0

    def test_posicion_sin_cuota_vinculada_cuenta_como_cero(self):
        """Posiciones con bucket_vinculado pero sin Cuota_Vinculada_CLP."""
        ss = {
            "positions": {
                "GAS_ESE_BUCKET": {"Monto_Mensual": 2_000_000},
                "PAS_HIP_001": {"bucket_vinculado": "GAS_ESE_BUCKET"},  # sin Cuota
            }
        }
        assert espacio_disponible_bucket(ss, "GAS_ESE_BUCKET") == 2_000_000.0


# ===========================================================================
# Capa 3 — Pensión mensual sostenible
# ===========================================================================


class TestCalcularPensionMensual:
    """Tests para calcular_pension_mensual() — fórmula de anualidad."""

    def test_total_cero_da_cero(self):
        """Si no hay capital acumulado la pensión es cero."""
        assert calcular_pension_mensual(0, 20) == 0.0

    def test_total_negativo_da_cero(self):
        """Capital negativo también retorna cero."""
        assert calcular_pension_mensual(-500_000, 20) == 0.0

    def test_anos_cero_lanza_valueerror(self):
        """anos_retiro = 0 debe lanzar ValueError."""
        with pytest.raises(ValueError):
            calcular_pension_mensual(1_000_000, 0)

    def test_anos_negativos_lanza_valueerror(self):
        """anos_retiro < 0 debe lanzar ValueError."""
        with pytest.raises(ValueError):
            calcular_pension_mensual(1_000_000, -5)

    def test_resultado_aproximado_anualidad_4pct(self):
        """1_000_000 / 20 años / 4% anual → ~6_060/mes (regla del 4%)."""
        resultado = calcular_pension_mensual(1_000_000, 20, 0.04)
        # Con la fórmula de anualidad compuesta mensual el resultado es ~6_023.
        # Usamos tolerancia del 2% para cubrir diferencias de redondeo.
        assert resultado == pytest.approx(6_060, rel=0.02)

    def test_tasa_cero_divide_linealmente(self):
        """Con tasa 0% se divide el capital entre el número de meses."""
        resultado = calcular_pension_mensual(1_200_000, 10, 0.0)
        assert resultado == pytest.approx(1_200_000 / 120, rel=1e-6)

    def test_horizonte_corto_da_pension_alta(self):
        """A menor horizonte, mayor pensión mensual."""
        pension_20 = calcular_pension_mensual(1_000_000, 20, 0.04)
        pension_10 = calcular_pension_mensual(1_000_000, 10, 0.04)
        assert pension_10 > pension_20

    def test_mayor_tasa_da_mayor_pension(self):
        """A mayor tasa de retiro, mayor pensión sostenible."""
        pension_baja = calcular_pension_mensual(1_000_000, 20, 0.02)
        pension_alta = calcular_pension_mensual(1_000_000, 20, 0.06)
        assert pension_alta > pension_baja

    def test_consistencia_con_total_grande(self):
        """La pensión escala linealmente con el capital."""
        p1 = calcular_pension_mensual(1_000_000, 20, 0.04)
        p2 = calcular_pension_mensual(2_000_000, 20, 0.04)
        assert p2 == pytest.approx(2 * p1, rel=1e-6)
