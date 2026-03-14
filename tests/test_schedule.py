"""Tests para core/schedule.py — gen_hipotecario."""

from datetime import date

import pandas as pd
import pytest

from core.schedule import COLS_TABLA_DESARROLLO, gen_hipotecario


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CAPITAL = 50_000_000  # CLP
TASA_ANUAL = 0.06      # 6 % anual
PLAZO = 240            # 20 años
FECHA = date(2026, 4, 1)
ID_POS = "PAS_HIP_001"


# ---------------------------------------------------------------------------
# Estructura y tipos del DataFrame
# ---------------------------------------------------------------------------


class TestEstructura:
    def test_columnas_estandar(self):
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, PLAZO, FECHA)
        assert list(df.columns) == COLS_TABLA_DESARROLLO

    def test_numero_filas(self):
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, PLAZO, FECHA)
        assert len(df) == PLAZO

    def test_id_posicion_propagado(self):
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, PLAZO, FECHA, id_posicion=ID_POS)
        assert (df["ID_Posicion"] == ID_POS).all()

    def test_tipo_flujo_calculado(self):
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, PLAZO, FECHA)
        assert (df["Tipo_Flujo"] == "calculado").all()

    def test_moneda_propagada(self):
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, PLAZO, FECHA, moneda="UF")
        assert (df["Moneda"] == "UF").all()

    def test_periodos_consecutivos(self):
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, 12, date(2026, 1, 1))
        periodos_esperados = [
            f"2026-{m:02d}" for m in range(1, 13)
        ]
        assert list(df["Periodo"]) == periodos_esperados

    def test_periodos_cruce_anio(self):
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, 3, date(2025, 11, 1))
        assert list(df["Periodo"]) == ["2025-11", "2025-12", "2026-01"]


# ---------------------------------------------------------------------------
# Invariantes financieros — Método francés
# ---------------------------------------------------------------------------


class TestMetodoFrances:
    def setup_method(self):
        self.df = gen_hipotecario(
            CAPITAL, TASA_ANUAL, PLAZO, FECHA, metodo="frances", id_posicion=ID_POS
        )

    def test_saldo_final_cero(self):
        """El saldo final de la última cuota debe ser 0."""
        assert abs(self.df["Saldo_Final"].iloc[-1]) < 1e-4

    def test_saldo_inicial_primera_fila(self):
        """El saldo inicial de la primera cuota = capital original."""
        assert abs(self.df["Saldo_Inicial"].iloc[0] - CAPITAL) < 1e-4

    def test_continuidad_saldos(self):
        """El saldo_final[i] debe igualar saldo_inicial[i+1]."""
        sf = self.df["Saldo_Final"].values[:-1]
        si = self.df["Saldo_Inicial"].values[1:]
        assert all(abs(a - b) < 1e-4 for a, b in zip(sf, si))

    def test_saldos_decrecientes(self):
        """En método francés, el saldo baja cada mes."""
        assert (self.df["Saldo_Final"].diff().dropna() < 0).all()

    def test_flujo_negativo(self):
        """Los flujos son negativos (egresos del usuario)."""
        assert (self.df["Flujo_Periodo"] < 0).all()

    def test_interes_negativo(self):
        """El costo financiero es negativo."""
        assert (self.df["Rendimiento_Costo"] <= 0).all()

    def test_amortizacion_positiva(self):
        """La amortización es positiva."""
        assert (self.df["Amortizacion"] > 0).all()

    def test_flujo_es_suma_amort_interes(self):
        """Flujo_Periodo == -(Amortizacion + |Rendimiento_Costo|)."""
        recalculado = -(self.df["Amortizacion"] + self.df["Rendimiento_Costo"].abs())
        assert (abs(self.df["Flujo_Periodo"] - recalculado) < 1e-4).all()

    def test_cuota_aproximadamente_constante(self):
        """En método francés las primeras n-1 cuotas son casi iguales."""
        cuotas = self.df["Flujo_Periodo"].iloc[:-1]
        assert cuotas.std() < 1.0  # variación < 1 CLP por redondeo

    def test_amortizacion_creciente(self):
        """La amortización de capital crece con el tiempo (francés)."""
        amort = self.df["Amortizacion"].values
        assert all(amort[i] <= amort[i + 1] for i in range(len(amort) - 2))

    def test_total_amortizado_igual_capital(self):
        """La suma de amortizaciones = capital original."""
        assert abs(self.df["Amortizacion"].sum() - CAPITAL) < 1e-2


# ---------------------------------------------------------------------------
# Invariantes financieros — Método alemán
# ---------------------------------------------------------------------------


class TestMetodoAleman:
    def setup_method(self):
        self.df = gen_hipotecario(
            CAPITAL, TASA_ANUAL, PLAZO, FECHA, metodo="aleman"
        )

    def test_saldo_final_cero(self):
        assert abs(self.df["Saldo_Final"].iloc[-1]) < 1e-4

    def test_continuidad_saldos(self):
        sf = self.df["Saldo_Final"].values[:-1]
        si = self.df["Saldo_Inicial"].values[1:]
        assert all(abs(a - b) < 1e-4 for a, b in zip(sf, si))

    def test_amortizacion_constante(self):
        """Las primeras n-1 amortizaciones son iguales (constante)."""
        amort = self.df["Amortizacion"].iloc[:-1]
        assert amort.std() < 1e-4

    def test_cuota_decreciente(self):
        """En método alemán la cuota total decrece."""
        cuotas = self.df["Flujo_Periodo"].values  # negativas, más negativo = mayor cuota
        assert all(cuotas[i] <= cuotas[i + 1] for i in range(len(cuotas) - 2))

    def test_total_amortizado_igual_capital(self):
        assert abs(self.df["Amortizacion"].sum() - CAPITAL) < 1e-2


# ---------------------------------------------------------------------------
# Casos especiales
# ---------------------------------------------------------------------------


class TestCasosEspeciales:
    def test_tasa_cero(self):
        """Préstamo sin interés: cuota = capital / plazo, sin costo financiero."""
        df = gen_hipotecario(CAPITAL, 0.0, 12, FECHA)
        assert (df["Rendimiento_Costo"] == 0).all()
        cuotas = df["Flujo_Periodo"].abs()
        assert abs(cuotas.mean() - CAPITAL / 12) < 1e-4

    def test_plazo_un_mes(self):
        """Un solo pago = capital total."""
        df = gen_hipotecario(100_000, 0.06, 1, FECHA)
        assert len(df) == 1
        assert abs(df["Amortizacion"].iloc[0] - 100_000) < 1e-4
        assert abs(df["Saldo_Final"].iloc[0]) < 1e-4

    def test_fecha_string_yyyy_mm(self):
        """Acepta fecha como string 'YYYY-MM'."""
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, 12, "2026-04")
        assert df["Periodo"].iloc[0] == "2026-04"

    def test_fecha_string_yyyy_mm_dd(self):
        """Acepta fecha como string 'YYYY-MM-DD'."""
        df = gen_hipotecario(CAPITAL, TASA_ANUAL, 12, "2026-04-15")
        assert df["Periodo"].iloc[0] == "2026-04"

    def test_moneda_uf(self):
        """Capital en UF: la tabla queda en UF sin convertir."""
        df = gen_hipotecario(2000, 0.03, 180, FECHA, moneda="UF")
        assert (df["Moneda"] == "UF").all()
        # El saldo inicial debe ser el capital en UF
        assert abs(df["Saldo_Inicial"].iloc[0] - 2000) < 1e-4


# ---------------------------------------------------------------------------
# Validaciones de parámetros inválidos
# ---------------------------------------------------------------------------


class TestValidaciones:
    def test_capital_cero_falla(self):
        with pytest.raises(ValueError, match="capital"):
            gen_hipotecario(0, TASA_ANUAL, PLAZO, FECHA)

    def test_capital_negativo_falla(self):
        with pytest.raises(ValueError, match="capital"):
            gen_hipotecario(-1000, TASA_ANUAL, PLAZO, FECHA)

    def test_tasa_negativa_falla(self):
        with pytest.raises(ValueError, match="tasa_anual"):
            gen_hipotecario(CAPITAL, -0.01, PLAZO, FECHA)

    def test_plazo_cero_falla(self):
        with pytest.raises(ValueError, match="plazo_meses"):
            gen_hipotecario(CAPITAL, TASA_ANUAL, 0, FECHA)

    def test_metodo_invalido_falla(self):
        with pytest.raises(ValueError, match="[Mm]étodo"):
            gen_hipotecario(CAPITAL, TASA_ANUAL, PLAZO, FECHA, metodo="bullet")

    def test_fecha_formato_invalido(self):
        with pytest.raises((ValueError, IndexError)):
            gen_hipotecario(CAPITAL, TASA_ANUAL, PLAZO, "26/04/2026")
