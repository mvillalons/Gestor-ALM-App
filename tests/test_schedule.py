"""Tests para core/schedule.py — todas las funciones de generación de tablas."""

from datetime import date

import pandas as pd
import pytest

from core.schedule import (
    COLS_TABLA_DESARROLLO,
    flujo_neto_mensual,
    gen_afp,
    gen_colegio,
    gen_credito_consumo,
    gen_hipotecario,
    gen_tarjeta,
)


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


# ===========================================================================
# gen_credito_consumo
# ===========================================================================


class TestGenCreditoConsumo:
    """Crédito de consumo — método francés."""

    MONTO = 5_000_000
    N_CUOTAS = 24
    TASA = 0.12  # 12 % anual
    FECHA = date(2026, 4, 1)

    def _df(self, **kwargs):
        return gen_credito_consumo(
            self.MONTO, self.N_CUOTAS, self.TASA, self.FECHA, **kwargs
        )

    # Estructura
    def test_columnas_estandar(self):
        assert list(self._df().columns) == COLS_TABLA_DESARROLLO

    def test_numero_filas(self):
        assert len(self._df()) == self.N_CUOTAS

    def test_tipo_flujo_calculado(self):
        assert (self._df()["Tipo_Flujo"] == "calculado").all()

    def test_id_posicion_propagado(self):
        df = gen_credito_consumo(
            self.MONTO, self.N_CUOTAS, self.TASA, self.FECHA, id_posicion="PAS_CON_001"
        )
        assert (df["ID_Posicion"] == "PAS_CON_001").all()

    # Invariantes financieros
    def test_saldo_final_cero(self):
        assert abs(self._df()["Saldo_Final"].iloc[-1]) < 1e-4

    def test_total_amortizado_igual_monto(self):
        assert abs(self._df()["Amortizacion"].sum() - self.MONTO) < 1e-2

    def test_flujo_negativo(self):
        assert (self._df()["Flujo_Periodo"] < 0).all()

    def test_saldo_decreciente(self):
        assert (self._df()["Saldo_Final"].diff().dropna() < 0).all()

    def test_continuidad_saldos(self):
        df = self._df()
        assert all(
            abs(a - b) < 1e-4
            for a, b in zip(df["Saldo_Final"].values[:-1], df["Saldo_Inicial"].values[1:])
        )

    def test_tasa_cero_cuota_igual_monto_dividido_n(self):
        df = gen_credito_consumo(self.MONTO, 12, 0.0, self.FECHA)
        cuotas = df["Flujo_Periodo"].abs()
        assert abs(cuotas.mean() - self.MONTO / 12) < 1e-4

    # Validaciones
    def test_monto_cero_falla(self):
        with pytest.raises(ValueError, match="monto"):
            gen_credito_consumo(0, self.N_CUOTAS, self.TASA, self.FECHA)

    def test_n_cuotas_cero_falla(self):
        with pytest.raises(ValueError, match="n_cuotas"):
            gen_credito_consumo(self.MONTO, 0, self.TASA, self.FECHA)

    def test_tasa_negativa_falla(self):
        with pytest.raises(ValueError, match="tasa_anual"):
            gen_credito_consumo(self.MONTO, self.N_CUOTAS, -0.01, self.FECHA)


# ===========================================================================
# gen_colegio
# ===========================================================================


class TestGenColegio:
    """Colegio — cuotas en meses específicos del año."""

    MONTO = 2_400_000     # anual
    CUOTAS = 10
    ANOS = 2
    MESES = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    FECHA = date(2026, 3, 1)

    def _df(self, **kwargs):
        return gen_colegio(
            self.MONTO, self.CUOTAS, self.ANOS, self.MESES, self.FECHA, **kwargs
        )

    # Estructura
    def test_columnas_estandar(self):
        assert list(self._df().columns) == COLS_TABLA_DESARROLLO

    def test_numero_filas_completo(self):
        """Cuando fecha_inicio coincide con el primer mes de pago."""
        assert len(self._df()) == self.CUOTAS * self.ANOS

    def test_tipo_flujo_calculado(self):
        assert (self._df()["Tipo_Flujo"] == "calculado").all()

    def test_id_posicion_propagado(self):
        df = gen_colegio(
            self.MONTO, self.CUOTAS, self.ANOS, self.MESES,
            self.FECHA, id_posicion="PAS_COL_001"
        )
        assert (df["ID_Posicion"] == "PAS_COL_001").all()

    def test_moneda_propagada(self):
        df = gen_colegio(self.MONTO, self.CUOTAS, self.ANOS, self.MESES, self.FECHA, moneda="UF")
        assert (df["Moneda"] == "UF").all()

    # Invariantes financieros
    def test_cuota_constante(self):
        """Cada cuota debe ser monto_anual / cuotas_por_ano."""
        cuota_esperada = self.MONTO / self.CUOTAS
        df = self._df()
        assert all(abs(v - cuota_esperada) < 1e-4 for v in df["Amortizacion"])

    def test_flujo_negativo(self):
        assert (self._df()["Flujo_Periodo"] < 0).all()

    def test_saldo_final_cero(self):
        assert abs(self._df()["Saldo_Final"].iloc[-1]) < 1e-4

    def test_rendimiento_cero(self):
        """Colegio no tiene interés."""
        assert (self._df()["Rendimiento_Costo"] == 0).all()

    def test_saldo_decreciente(self):
        assert (self._df()["Saldo_Final"].diff().dropna() < 0).all()

    def test_solo_meses_de_pago(self):
        """Las filas solo deben tener períodos en los meses seleccionados."""
        df = self._df()
        meses_en_tabla = {int(p.split("-")[1]) for p in df["Periodo"]}
        assert meses_en_tabla.issubset(set(self.MESES))

    def test_fecha_inicio_filtra_meses_pasados(self):
        """Si inicio es mayo, los meses de marzo y abril del año 1 se omiten."""
        fecha_mayo = date(2026, 5, 1)
        df = gen_colegio(self.MONTO, self.CUOTAS, 1, self.MESES, fecha_mayo)
        # Solo meses >= 5 en el primer año (5,6,7,8,9,10,11,12) → 8 filas
        assert len(df) == 8

    def test_cuotas_por_ano_limita_meses_usados(self):
        """Si cuotas_por_ano < len(meses), solo se usan los primeros N meses."""
        df = gen_colegio(1_000_000, 3, 1, [1, 3, 5, 7, 9, 11], date(2026, 1, 1))
        assert len(df) == 3
        meses_usados = sorted({int(p.split("-")[1]) for p in df["Periodo"]})
        assert meses_usados == [1, 3, 5]  # primeros 3 en orden

    # Validaciones
    def test_monto_cero_falla(self):
        with pytest.raises(ValueError, match="monto_anual"):
            gen_colegio(0, self.CUOTAS, self.ANOS, self.MESES, self.FECHA)

    def test_cuotas_mayor_que_meses_falla(self):
        with pytest.raises(ValueError):
            gen_colegio(self.MONTO, 11, self.ANOS, self.MESES, self.FECHA)

    def test_anos_cero_falla(self):
        with pytest.raises(ValueError, match="anos_restantes"):
            gen_colegio(self.MONTO, self.CUOTAS, 0, self.MESES, self.FECHA)

    def test_meses_vacio_falla(self):
        with pytest.raises(ValueError, match="vac"):
            gen_colegio(self.MONTO, self.CUOTAS, self.ANOS, [], self.FECHA)

    def test_sin_pagos_futuros_retorna_vacio(self):
        """Si todos los meses de pago caen antes de fecha_inicio en el único año → vacío."""
        # Meses 1 y 2 (enero y febrero), inicio en marzo del mismo año con 1 solo año.
        # Enero y febrero < marzo → se omiten; no quedan filas.
        df = gen_colegio(self.MONTO, 2, 1, [1, 2], date(2026, 3, 1))
        assert df.empty


# ===========================================================================
# gen_tarjeta
# ===========================================================================


class TestGenTarjeta:
    """Tarjeta de crédito — saldo decreciente con pago mensual fijo."""

    DEUDA = 1_000_000
    PAGO = 150_000
    TASA_M = 0.02  # 2 % mensual
    FECHA = date(2026, 4, 1)

    def _df(self, **kwargs):
        return gen_tarjeta(self.DEUDA, self.PAGO, self.TASA_M, self.FECHA, **kwargs)

    # Estructura
    def test_columnas_estandar(self):
        assert list(self._df().columns) == COLS_TABLA_DESARROLLO

    def test_tipo_flujo_calculado(self):
        assert (self._df()["Tipo_Flujo"] == "calculado").all()

    def test_id_posicion_propagado(self):
        df = gen_tarjeta(
            self.DEUDA, self.PAGO, self.TASA_M, self.FECHA, id_posicion="PAS_TAR_001"
        )
        assert (df["ID_Posicion"] == "PAS_TAR_001").all()

    # Invariantes financieros
    def test_saldo_final_cero(self):
        """El saldo termina en cero."""
        assert abs(self._df()["Saldo_Final"].iloc[-1]) < 0.02

    def test_saldo_decreciente(self):
        """El saldo baja cada mes."""
        df = self._df()
        assert (df["Saldo_Final"].diff().dropna() < 0).all()

    def test_flujo_negativo(self):
        assert (self._df()["Flujo_Periodo"] < 0).all()

    def test_interes_negativo(self):
        assert (self._df()["Rendimiento_Costo"] <= 0).all()

    def test_amortizacion_positiva(self):
        assert (self._df()["Amortizacion"] > 0).all()

    def test_continuidad_saldos(self):
        df = self._df()
        assert all(
            abs(a - b) < 1e-4
            for a, b in zip(df["Saldo_Final"].values[:-1], df["Saldo_Inicial"].values[1:])
        )

    def test_total_amortizado_igual_deuda(self):
        assert abs(self._df()["Amortizacion"].sum() - self.DEUDA) < 0.02

    def test_ultimo_pago_ajustado(self):
        """La última cuota puede ser menor que el pago habitual."""
        df = self._df()
        ultimo_flujo = abs(float(df["Flujo_Periodo"].iloc[-1]))
        assert ultimo_flujo <= self.PAGO + 1e-4

    def test_tasa_cero_pago_divide_deuda(self):
        """Con 0% de interés, cada pago amortiza exactamente hasta llegar a 0."""
        df = gen_tarjeta(1_000_000, 200_000, 0.0, self.FECHA)
        assert len(df) == 5  # 1_000_000 / 200_000
        assert (df["Rendimiento_Costo"] == 0).all()
        assert abs(df["Saldo_Final"].iloc[-1]) < 1e-4

    def test_max_meses_limita_iteraciones(self):
        """max_meses evita ciclos infinitos si el pago es muy bajo."""
        # Pago apenas por encima del interés → converge lento; max_meses lo corta
        deuda = 1_000_000
        tasa_m = 0.02
        # pago = interes_inicial + 1 CLP
        pago_minimo = deuda * tasa_m + 1
        df = gen_tarjeta(deuda, pago_minimo, tasa_m, self.FECHA, max_meses=10)
        assert len(df) <= 10

    # Validaciones
    def test_deuda_cero_falla(self):
        with pytest.raises(ValueError, match="deuda_total"):
            gen_tarjeta(0, self.PAGO, self.TASA_M, self.FECHA)

    def test_pago_cero_falla(self):
        with pytest.raises(ValueError, match="pago_mensual"):
            gen_tarjeta(self.DEUDA, 0, self.TASA_M, self.FECHA)

    def test_tasa_negativa_falla(self):
        with pytest.raises(ValueError, match="tasa_mensual"):
            gen_tarjeta(self.DEUDA, self.PAGO, -0.01, self.FECHA)

    def test_pago_insuficiente_falla(self):
        """Pago menor o igual al interés inicial → no amortiza → error."""
        interes = self.DEUDA * self.TASA_M
        with pytest.raises(ValueError, match="pago_mensual"):
            gen_tarjeta(self.DEUDA, interes, self.TASA_M, self.FECHA)


# ===========================================================================
# gen_afp
# ===========================================================================


class TestGenAfp:
    """Proyección AFP — acumulación hasta jubilación."""

    SALDO = 10_000_000
    APORTE = 150_000
    TASA = 0.05   # 5 % anual
    EDAD_ACT = 35.0
    EDAD_JUB = 65.0
    FECHA = date(2026, 4, 1)

    def _df(self, **kwargs):
        return gen_afp(
            self.SALDO, self.APORTE, self.TASA,
            self.EDAD_ACT, self.EDAD_JUB, self.FECHA, **kwargs
        )

    # Estructura
    def test_columnas_estandar(self):
        assert list(self._df().columns) == COLS_TABLA_DESARROLLO

    def test_numero_filas(self):
        """30 años * 12 meses = 360 filas."""
        assert len(self._df()) == round((self.EDAD_JUB - self.EDAD_ACT) * 12)

    def test_tipo_flujo_calculado(self):
        assert (self._df()["Tipo_Flujo"] == "calculado").all()

    def test_id_posicion_propagado(self):
        df = gen_afp(
            self.SALDO, self.APORTE, self.TASA,
            self.EDAD_ACT, self.EDAD_JUB, self.FECHA, id_posicion="AFP_PRINCIPAL"
        )
        assert (df["ID_Posicion"] == "AFP_PRINCIPAL").all()

    def test_moneda_propagada(self):
        df = gen_afp(
            self.SALDO, self.APORTE, self.TASA,
            self.EDAD_ACT, self.EDAD_JUB, self.FECHA, moneda="CLP"
        )
        assert (df["Moneda"] == "CLP").all()

    # Invariantes financieros
    def test_saldo_creciente(self):
        """El saldo AFP debe crecer cada mes."""
        df = self._df()
        assert (df["Saldo_Final"].diff().dropna() > 0).all()

    def test_saldo_inicial_primera_fila(self):
        assert abs(self._df()["Saldo_Inicial"].iloc[0] - self.SALDO) < 1.0

    def test_flujo_periodo_negativo(self):
        """El aporte es un egreso del usuario → Flujo_Periodo negativo."""
        df = self._df()
        assert (df["Flujo_Periodo"] <= 0).all()

    def test_rendimiento_positivo(self):
        """El rendimiento es positivo (crecimiento del fondo)."""
        df = self._df()
        assert (df["Rendimiento_Costo"] >= 0).all()

    def test_continuidad_saldos(self):
        df = self._df()
        assert all(
            abs(a - b) < 1.0
            for a, b in zip(df["Saldo_Final"].values[:-1], df["Saldo_Inicial"].values[1:])
        )

    def test_tasa_cero_sin_rendimiento(self):
        """Con 0% de rentabilidad, saldo crece solo por aportes."""
        df = gen_afp(
            self.SALDO, self.APORTE, 0.0, self.EDAD_ACT, self.EDAD_JUB, self.FECHA
        )
        plazo = round((self.EDAD_JUB - self.EDAD_ACT) * 12)
        saldo_esperado = self.SALDO + self.APORTE * plazo
        assert abs(df["Saldo_Final"].iloc[-1] - saldo_esperado) < 1.0
        assert (df["Rendimiento_Costo"] == 0).all()

    def test_saldo_inicial_cero_funciona(self):
        """Permite saldo actual = 0."""
        df = gen_afp(0, self.APORTE, self.TASA, self.EDAD_ACT, self.EDAD_JUB, self.FECHA)
        assert df["Saldo_Final"].iloc[-1] > 0

    def test_aporte_cero_solo_rentabilidad(self):
        """Con aporte = 0, el saldo crece solo por rendimiento."""
        df = gen_afp(
            self.SALDO, 0, self.TASA, self.EDAD_ACT, self.EDAD_JUB, self.FECHA
        )
        assert (df["Flujo_Periodo"] == 0).all()
        assert (df["Rendimiento_Costo"] > 0).all()

    # Validaciones
    def test_saldo_negativo_falla(self):
        with pytest.raises(ValueError, match="saldo_actual"):
            gen_afp(-1, self.APORTE, self.TASA, self.EDAD_ACT, self.EDAD_JUB, self.FECHA)

    def test_aporte_negativo_falla(self):
        with pytest.raises(ValueError, match="aporte_mensual"):
            gen_afp(self.SALDO, -1, self.TASA, self.EDAD_ACT, self.EDAD_JUB, self.FECHA)

    def test_tasa_negativa_falla(self):
        with pytest.raises(ValueError, match="tasa_anual"):
            gen_afp(self.SALDO, self.APORTE, -0.01, self.EDAD_ACT, self.EDAD_JUB, self.FECHA)

    def test_edad_jubilacion_menor_que_actual_falla(self):
        with pytest.raises(ValueError, match="edad_jubilacion"):
            gen_afp(self.SALDO, self.APORTE, self.TASA, 65.0, 64.0, self.FECHA)

    def test_misma_edad_falla(self):
        with pytest.raises(ValueError, match="edad_jubilacion"):
            gen_afp(self.SALDO, self.APORTE, self.TASA, 65.0, 65.0, self.FECHA)


# ===========================================================================
# flujo_neto_mensual
# ===========================================================================


class TestFlujoNetoMensual:
    """Flujo neto mensual consolidado."""

    FECHA = date(2026, 4, 1)
    INGRESO = 2_000_000

    def _tabla_simple(self, n=12, flujo=-100_000, moneda="CLP") -> pd.DataFrame:
        """Genera una tabla sintética con Flujo_Periodo constante."""
        from core.schedule import _next_month, _parse_fecha  # noqa: PLC0415
        fecha = _parse_fecha(self.FECHA)
        rows = []
        for _ in range(n):
            rows.append({
                "Periodo": fecha.strftime("%Y-%m"),
                "Flujo_Periodo": flujo,
                "Moneda": moneda,
            })
            fecha = _next_month(fecha)
        return pd.DataFrame(rows)

    # Estructura
    def test_columnas_salida(self):
        df = flujo_neto_mensual([self._tabla_simple()], self.INGRESO)
        assert list(df.columns) == ["Periodo", "Flujo_Pasivos", "Ingreso", "Flujo_Neto"]

    def test_retorna_vacio_sin_tablas(self):
        df = flujo_neto_mensual([], self.INGRESO)
        assert df.empty
        assert "Flujo_Neto" in df.columns

    # Valores
    def test_flujo_neto_positivo_cuando_ingreso_mayor(self):
        df = flujo_neto_mensual([self._tabla_simple(flujo=-100_000)], self.INGRESO)
        assert (df["Flujo_Neto"] > 0).all()

    def test_flujo_neto_negativo_cuando_cuota_mayor(self):
        df = flujo_neto_mensual([self._tabla_simple(flujo=-3_000_000)], self.INGRESO)
        assert (df["Flujo_Neto"] < 0).all()

    def test_ingreso_constante_en_todas_las_filas(self):
        df = flujo_neto_mensual([self._tabla_simple()], self.INGRESO)
        assert (df["Ingreso"] == self.INGRESO).all()

    def test_flujo_neto_igual_ingreso_mas_pasivos(self):
        df = flujo_neto_mensual([self._tabla_simple(flujo=-500_000)], self.INGRESO)
        expected = self.INGRESO + (-500_000)
        assert (abs(df["Flujo_Neto"] - expected) < 1e-4).all()

    def test_dos_tablas_suman_flujos_por_periodo(self):
        """Dos pasivos en los mismos períodos → Flujo_Pasivos = suma de ambos."""
        t1 = self._tabla_simple(flujo=-200_000)
        t2 = self._tabla_simple(flujo=-300_000)
        df = flujo_neto_mensual([t1, t2], self.INGRESO)
        assert (abs(df["Flujo_Pasivos"] - (-500_000)) < 1e-4).all()

    def test_periodos_ordenados(self):
        df = flujo_neto_mensual([self._tabla_simple(n=6)], self.INGRESO)
        periodos = list(df["Periodo"])
        assert periodos == sorted(periodos)

    def test_numero_filas_igual_a_periodos_unicos(self):
        t1 = self._tabla_simple(n=6)
        t2 = self._tabla_simple(n=6)
        df = flujo_neto_mensual([t1, t2], self.INGRESO)
        assert len(df) == 6  # mismos períodos → 6 filas, no 12

    def test_tablas_con_periodos_distintos(self):
        """Tablas con períodos distintos se agregan correctamente."""
        from core.schedule import gen_credito_consumo  # noqa: PLC0415
        t1 = gen_credito_consumo(1_000_000, 6, 0.0, date(2026, 4, 1))
        t2 = gen_credito_consumo(1_000_000, 6, 0.0, date(2026, 7, 1))
        df = flujo_neto_mensual([t1, t2], self.INGRESO)
        # Períodos: 2026-04 a 2026-09 (t1) ∪ 2026-07 a 2026-12 (t2) = 9 únicos
        assert len(df) == 9

    # Validaciones
    def test_ingreso_negativo_falla(self):
        with pytest.raises(ValueError, match="ingreso_mensual"):
            flujo_neto_mensual([self._tabla_simple()], -1)


# ===========================================================================
# flujo_neto_mensual — normalización de monedas
# ===========================================================================

_UF_TEST = 39_700.0
_USD_TEST = 950.0


class TestFlujoNetoMensualMultiMoneda:
    """flujo_neto_mensual normaliza flujos UF/USD a CLP antes de sumar."""

    FECHA = date(2026, 4, 1)
    INGRESO = 2_000_000.0

    def _tabla_moneda(self, n=12, flujo=-100.0, moneda="UF") -> pd.DataFrame:
        from core.schedule import _next_month, _parse_fecha  # noqa: PLC0415
        fecha = _parse_fecha(self.FECHA)
        rows = []
        for _ in range(n):
            rows.append({
                "Periodo": fecha.strftime("%Y-%m"),
                "Flujo_Periodo": flujo,
                "Moneda": moneda,
            })
            fecha = _next_month(fecha)
        return pd.DataFrame(rows)

    def test_flujo_uf_se_convierte_a_clp(self):
        """Cuota de -10 UF debe convertirse a -10 * valor_uf CLP."""
        t = self._tabla_moneda(flujo=-10.0, moneda="UF")
        df = flujo_neto_mensual([t], self.INGRESO, valor_uf=_UF_TEST, valor_usd=_USD_TEST)
        expected_flujo_pasivos = -10.0 * _UF_TEST
        assert (abs(df["Flujo_Pasivos"] - expected_flujo_pasivos) < 1.0).all()

    def test_flujo_usd_se_convierte_a_clp(self):
        """Cuota de -500 USD debe convertirse a -500 * valor_usd CLP."""
        t = self._tabla_moneda(flujo=-500.0, moneda="USD")
        df = flujo_neto_mensual([t], self.INGRESO, valor_uf=_UF_TEST, valor_usd=_USD_TEST)
        expected_flujo_pasivos = -500.0 * _USD_TEST
        assert (abs(df["Flujo_Pasivos"] - expected_flujo_pasivos) < 1.0).all()

    def test_flujo_clp_sin_conversion(self):
        """Cuota en CLP no cambia al normalizar."""
        t = self._tabla_moneda(flujo=-300_000.0, moneda="CLP")
        df = flujo_neto_mensual([t], self.INGRESO, valor_uf=_UF_TEST, valor_usd=_USD_TEST)
        assert (abs(df["Flujo_Pasivos"] - (-300_000.0)) < 1.0).all()

    def test_mezcla_uf_y_clp_suma_correctamente(self):
        """Tabla UF + tabla CLP → suma normalizada a CLP."""
        t_uf = self._tabla_moneda(flujo=-5.0, moneda="UF")
        t_clp = self._tabla_moneda(flujo=-200_000.0, moneda="CLP")
        df = flujo_neto_mensual(
            [t_uf, t_clp], self.INGRESO, valor_uf=_UF_TEST, valor_usd=_USD_TEST
        )
        expected = -5.0 * _UF_TEST + (-200_000.0)
        assert (abs(df["Flujo_Pasivos"] - expected) < 1.0).all()

    def test_mezcla_usd_clp_uf(self):
        """Tres tablas en distintas monedas suman correctamente en CLP."""
        t_uf = self._tabla_moneda(flujo=-3.0, moneda="UF")
        t_usd = self._tabla_moneda(flujo=-200.0, moneda="USD")
        t_clp = self._tabla_moneda(flujo=-100_000.0, moneda="CLP")
        df = flujo_neto_mensual(
            [t_uf, t_usd, t_clp], self.INGRESO, valor_uf=_UF_TEST, valor_usd=_USD_TEST
        )
        expected = -3.0 * _UF_TEST + (-200.0 * _USD_TEST) + (-100_000.0)
        assert (abs(df["Flujo_Pasivos"] - expected) < 1.0).all()

    def test_flujo_neto_correcto_con_normalizacion(self):
        """Flujo_Neto = Ingreso + Flujo_Pasivos (normalizado)."""
        t = self._tabla_moneda(flujo=-10.0, moneda="UF")
        df = flujo_neto_mensual([t], self.INGRESO, valor_uf=_UF_TEST, valor_usd=_USD_TEST)
        expected_neto = self.INGRESO + (-10.0 * _UF_TEST)
        assert (abs(df["Flujo_Neto"] - expected_neto) < 1.0).all()

    def test_tabla_sin_columna_moneda_trata_como_clp(self):
        """Si la tabla no tiene columna Moneda, trata los flujos como CLP."""
        from core.schedule import _next_month, _parse_fecha  # noqa: PLC0415
        fecha = _parse_fecha(self.FECHA)
        rows = [{"Periodo": fecha.strftime("%Y-%m"), "Flujo_Periodo": -100_000.0}]
        t = pd.DataFrame(rows)  # sin columna Moneda
        df = flujo_neto_mensual([t], self.INGRESO, valor_uf=_UF_TEST, valor_usd=_USD_TEST)
        assert abs(df["Flujo_Pasivos"].iloc[0] - (-100_000.0)) < 1.0

    def test_valor_uf_afecta_magnitud(self):
        """Cambiar valor_uf cambia el Flujo_Pasivos proporcionalmente."""
        t = self._tabla_moneda(flujo=-1.0, moneda="UF")
        df_a = flujo_neto_mensual([t], self.INGRESO, valor_uf=30_000.0, valor_usd=_USD_TEST)
        df_b = flujo_neto_mensual([t], self.INGRESO, valor_uf=40_000.0, valor_usd=_USD_TEST)
        # 40k UF → flujo más negativo
        assert df_b["Flujo_Pasivos"].iloc[0] < df_a["Flujo_Pasivos"].iloc[0]
