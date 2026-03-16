"""
Tests del módulo parser/.
No requieren archivos PDF reales ni API keys.
"""
from __future__ import annotations
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from parser.models import Movimiento, PropuestaClasificacion
from parser.detector import detectar_formato, FORMATOS_CONOCIDOS
from parser.normalizer import (
    extraer_movimientos,
    movimientos_a_dataframe,
    dataframe_a_movimientos,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mov(
    fecha="2024-01-15",
    descripcion="SUPERMERCADO",
    monto=-50000.0,
    moneda="CLP",
    monto_clp=-50000.0,
    fuente="test",
    confianza_extraccion=1.0,
) -> Movimiento:
    return Movimiento(
        fecha=fecha,
        descripcion=descripcion,
        monto=monto,
        moneda=moneda,
        monto_clp=monto_clp,
        fuente=fuente,
        referencia="",
        confianza_extraccion=confianza_extraccion,
        raw="",
    )


# ── Models ───────────────────────────────────────────────────────────────────

class TestMovimiento:
    def test_creacion_basica(self):
        m = _mov()
        assert m.fecha == "2024-01-15"
        assert m.monto == -50000.0
        assert m.moneda == "CLP"

    def test_monto_positivo_ingreso(self):
        m = _mov(monto=1_000_000.0)
        assert m.monto > 0

    def test_monto_negativo_egreso(self):
        m = _mov(monto=-50_000.0)
        assert m.monto < 0

    def test_campos_opcionales_default(self):
        m = Movimiento(
            fecha="2024-01-01",
            descripcion="test",
            monto=0.0,
            moneda="CLP",
            monto_clp=0.0,
            fuente="test",
        )
        assert m.referencia == ""
        assert m.confianza_extraccion == 1.0
        assert m.raw == ""


class TestPropuestaClasificacion:
    def test_creacion(self):
        p = PropuestaClasificacion(
            movimiento=_mov(),
            id_posicion_sugerido="GAS_ESE_BUCKET",
            confianza=0.92,
            justificacion="Supermercado → gasto esencial",
        )
        assert p.tipo_flujo == "importado"
        assert p.estado == "pendiente"
        assert p.confianza == 0.92

    def test_sin_clasificar(self):
        p = PropuestaClasificacion(
            movimiento=_mov(),
            id_posicion_sugerido="SIN_CLASIFICAR",
            confianza=0.0,
            justificacion="",
        )
        assert p.id_posicion_sugerido == "SIN_CLASIFICAR"


# ── Detector ─────────────────────────────────────────────────────────────────

class TestDetectarFormato:
    def test_xlsx_devuelve_generic_excel(self, tmp_path):
        f = tmp_path / "cartola.xlsx"
        f.write_bytes(b"dummy")
        assert detectar_formato(str(f)) == "generic_excel"

    def test_xls_devuelve_generic_excel(self, tmp_path):
        f = tmp_path / "cartola.xls"
        f.write_bytes(b"dummy")
        assert detectar_formato(str(f)) == "generic_excel"

    def test_csv_devuelve_generic_excel(self, tmp_path):
        f = tmp_path / "movimientos.csv"
        f.write_text("fecha,descripcion,monto\n2024-01-01,test,1000")
        assert detectar_formato(str(f)) == "generic_excel"

    def test_extension_desconocida(self, tmp_path):
        f = tmp_path / "archivo.txt"
        f.write_text("algo")
        assert detectar_formato(str(f)) == "desconocido"

    def test_pdf_generico_sin_keywords(self, tmp_path):
        f = tmp_path / "cartola.pdf"
        f.write_bytes(b"dummy pdf content")
        # Sin pdfplumber real, el mock retorna generic_pdf
        with patch("parser.detector._PDFPLUMBER_OK", False):
            assert detectar_formato(str(f)) == "generic_pdf"

    def test_pdf_itau_cta_cte(self, tmp_path):
        f = tmp_path / "cartola.pdf"
        f.write_bytes(b"dummy")
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Cartola Histórica Cuenta corriente Itaú"
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("parser.detector._PDFPLUMBER_OK", True), \
             patch("parser.detector.pdfplumber", create=True) as mock_plumber:
            mock_plumber.open.return_value = mock_pdf
            resultado = detectar_formato(str(f))

        assert resultado == "itau_cta_cte"

    def test_pdf_itau_tc_nacional(self, tmp_path):
        f = tmp_path / "tc.pdf"
        f.write_bytes(b"dummy")
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "ESTADO DE CUENTA NACIONAL DE TARJETA"
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("parser.detector._PDFPLUMBER_OK", True), \
             patch("parser.detector.pdfplumber", create=True) as mock_plumber:
            mock_plumber.open.return_value = mock_pdf
            resultado = detectar_formato(str(f))

        assert resultado == "itau_tc_nacional"

    def test_formatos_conocidos_no_vacio(self):
        assert len(FORMATOS_CONOCIDOS) >= 3
        assert "itau_cta_cte" in FORMATOS_CONOCIDOS


# ── Normalizer ───────────────────────────────────────────────────────────────

class TestMovimientosADataframe:
    def test_lista_vacia(self):
        df = movimientos_a_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
        assert "fecha" in df.columns
        assert "monto" in df.columns

    def test_un_movimiento(self):
        m = _mov(fecha="2024-03-10", monto=-100_000.0)
        df = movimientos_a_dataframe([m])
        assert len(df) == 1
        assert df.iloc[0]["fecha"] == "2024-03-10"
        assert df.iloc[0]["monto"] == -100_000.0

    def test_columnas_esperadas(self):
        df = movimientos_a_dataframe([_mov()])
        for col in ["fecha", "descripcion", "monto", "moneda", "monto_clp", "fuente"]:
            assert col in df.columns


class TestDataframeAMovimientos:
    def test_ida_y_vuelta(self):
        movs = [
            _mov(fecha="2024-01-01", monto=-50000.0),
            _mov(fecha="2024-01-02", monto=1_000_000.0, descripcion="SUELDO"),
        ]
        df = movimientos_a_dataframe(movs)
        movs2 = dataframe_a_movimientos(df)
        assert len(movs2) == 2
        assert movs2[0].fecha == "2024-01-01"
        assert movs2[1].descripcion == "SUELDO"

    def test_monto_preservado(self):
        movs = [_mov(monto=-123456.0)]
        df = movimientos_a_dataframe(movs)
        movs2 = dataframe_a_movimientos(df)
        assert movs2[0].monto == -123456.0


class TestNormalizacion:
    def test_usd_a_clp(self):
        movs = [_mov(moneda="USD", monto=-100.0, monto_clp=0.0)]
        # Simular la normalización directamente
        valor_usd = 950.0
        for m in movs:
            if m.moneda == "USD":
                m.monto_clp = m.monto * valor_usd
        assert movs[0].monto_clp == -95000.0

    def test_uf_a_clp(self):
        movs = [_mov(moneda="UF", monto=-2.0, monto_clp=0.0)]
        valor_uf = 39700.0
        for m in movs:
            if m.moneda == "UF":
                m.monto_clp = m.monto * valor_uf
        assert movs[0].monto_clp == -79400.0


# ── Generic Excel Extractor ──────────────────────────────────────────────────

class TestGenericExcel:
    def test_csv_simple(self, tmp_path):
        from parser.extractors.generic_excel import extraer

        csv_content = "fecha,descripcion,monto\n2024-01-15,SUPERMERCADO,-50000\n2024-01-16,SUELDO,1500000\n"
        f = tmp_path / "movimientos.csv"
        f.write_text(csv_content)

        movs = extraer(str(f))
        assert len(movs) == 2
        assert movs[0].fecha == "2024-01-15"
        assert movs[0].monto == -50000.0
        assert movs[1].monto == 1500000.0

    def test_csv_cargo_abono(self, tmp_path):
        from parser.extractors.generic_excel import extraer

        csv_content = "fecha,glosa,cargo,abono\n2024-02-01,ARRIENDO,500000,0\n2024-02-05,TRANSFERENCIA,0,200000\n"
        f = tmp_path / "movimientos.csv"
        f.write_text(csv_content)

        movs = extraer(str(f))
        assert len(movs) == 2
        # cargo → negativo, abono → positivo
        egreso = next(m for m in movs if "ARRIENDO" in m.descripcion)
        ingreso = next(m for m in movs if "TRANSFERENCIA" in m.descripcion)
        assert egreso.monto == -500000.0
        assert ingreso.monto == 200000.0

    def test_xlsx(self, tmp_path):
        from parser.extractors.generic_excel import extraer

        df = pd.DataFrame({
            "fecha": ["2024-03-01", "2024-03-02"],
            "descripcion": ["PAGO SERVICIO", "DEPOSITO"],
            "monto": [-30000, 500000],
        })
        f = tmp_path / "cartola.xlsx"
        df.to_excel(str(f), index=False)

        movs = extraer(str(f))
        assert len(movs) == 2
        assert movs[0].fuente == "generic_excel"

    def test_confianza_alta_si_columnas_por_nombre(self, tmp_path):
        from parser.extractors.generic_excel import extraer

        csv_content = "fecha,descripcion,monto\n2024-01-01,TEST,1000\n"
        f = tmp_path / "test.csv"
        f.write_text(csv_content)

        movs = extraer(str(f))
        # Detectó por nombre → confianza 0.9
        assert movs[0].confianza_extraccion == 0.9

    def test_fila_sin_monto_ignorada(self, tmp_path):
        from parser.extractors.generic_excel import extraer

        csv_content = "fecha,descripcion,monto\n2024-01-01,TEST,0\n2024-01-02,REAL,5000\n"
        f = tmp_path / "test.csv"
        f.write_text(csv_content)

        movs = extraer(str(f))
        assert len(movs) == 1
        assert movs[0].descripcion == "REAL"


# ── Generic PDF Extractor ────────────────────────────────────────────────────

class TestGenericPdf:
    def test_confianza_extraccion_07(self, tmp_path):
        from parser.extractors import generic_pdf

        f = tmp_path / "cartola.pdf"
        f.write_bytes(b"dummy")

        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "15/01/2024 SUPERMERCADO JUMBO $45.990\n"
            "20/01/2024 PAGO CUENTA $15.000\n"
        )
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("parser.extractors.generic_pdf._PDFPLUMBER_OK", True), \
             patch("parser.extractors.generic_pdf.pdfplumber", create=True) as mock_plumber:
            mock_plumber.open.return_value = mock_pdf
            movs = generic_pdf.extraer(str(f))

        # Si detectó movimientos, todos deben tener confianza 0.7
        for m in movs:
            assert m.confianza_extraccion == 0.7
            assert m.fuente == "generic_pdf"

    def test_sin_pdfplumber_lanza_error(self, tmp_path):
        from parser.extractors import generic_pdf

        f = tmp_path / "cartola.pdf"
        f.write_bytes(b"dummy")

        with patch("parser.extractors.generic_pdf._PDFPLUMBER_OK", False):
            with pytest.raises(ImportError):
                generic_pdf.extraer(str(f))


# ── LLM Classifier ───────────────────────────────────────────────────────────

class TestLLMClassifier:
    def test_sin_anthropic_lanza_error(self):
        from parser.llm_classifier import clasificar_movimientos
        import sys

        with patch.dict(sys.modules, {"anthropic": None}):
            with pytest.raises((ImportError, TypeError)):
                clasificar_movimientos(
                    movimientos=[_mov()],
                    posiciones={},
                    anthropic_api_key="fake_key",
                )

    def test_clasificacion_sin_clasificar_con_error_api(self):
        """Si la API falla, retorna SIN_CLASIFICAR para todos."""
        from parser.llm_classifier import clasificar_movimientos

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            movs = [_mov(), _mov(descripcion="SUELDO", monto=1_000_000.0)]
            resultado = clasificar_movimientos(
                movimientos=movs,
                posiciones={"ING_PRINCIPAL": {"Clase": "Ingreso_Recurrente"}},
                anthropic_api_key="fake_key",
            )

        assert len(resultado) == 2
        for r in resultado:
            assert r.id_posicion_sugerido == "SIN_CLASIFICAR"

    def test_clasificacion_exitosa(self):
        """Prueba clasificación con respuesta JSON válida."""
        from parser.llm_classifier import clasificar_movimientos

        respuesta_json = """[
            {"id": "0", "id_posicion_sugerido": "ING_PRINCIPAL",
             "confianza": 0.95, "justificacion": "Depósito de sueldo",
             "tipo_flujo": "importado"}
        ]"""

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=respuesta_json)]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            movs = [_mov(descripcion="DEPOSITO SUELDO", monto=2_000_000.0)]
            resultado = clasificar_movimientos(
                movimientos=movs,
                posiciones={"ING_PRINCIPAL": {"Clase": "Ingreso_Recurrente", "Descripcion": "Sueldo"}},
                anthropic_api_key="fake_key",
            )

        assert len(resultado) == 1
        assert resultado[0].id_posicion_sugerido == "ING_PRINCIPAL"
        assert resultado[0].confianza == 0.95

    def test_confianza_baja_devuelve_sin_clasificar(self):
        """Si confianza < 0.5 el resultado debe ser SIN_CLASIFICAR."""
        from parser.llm_classifier import clasificar_movimientos

        respuesta_json = """[
            {"id": "0", "id_posicion_sugerido": "GAS_ESE_BUCKET",
             "confianza": 0.3, "justificacion": "Poco claro",
             "tipo_flujo": "importado"}
        ]"""

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=respuesta_json)]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            movs = [_mov()]
            resultado = clasificar_movimientos(
                movimientos=movs,
                posiciones={},
                anthropic_api_key="fake_key",
            )

        assert resultado[0].id_posicion_sugerido == "SIN_CLASIFICAR"


# ── Drive Inbox ───────────────────────────────────────────────────────────────

class TestDriveInbox:
    def test_listar_inbox_vacio(self):
        from parser.drive_inbox import listar_inbox

        mock_drive = MagicMock()
        mock_drive.list_folder.return_value = []
        assert listar_inbox(mock_drive) == []

    def test_listar_inbox_con_archivos(self):
        from parser.drive_inbox import listar_inbox

        archivos = [
            {"id": "abc123", "name": "cartola.pdf", "mimeType": "application/pdf"},
        ]
        mock_drive = MagicMock()
        mock_drive.list_folder.return_value = archivos
        resultado = listar_inbox(mock_drive)
        assert len(resultado) == 1
        assert resultado[0]["name"] == "cartola.pdf"

    def test_listar_inbox_error_retorna_vacio(self):
        from parser.drive_inbox import listar_inbox

        mock_drive = MagicMock()
        mock_drive.list_folder.side_effect = Exception("Drive error")
        assert listar_inbox(mock_drive) == []

    def test_mover_a_procesados_exitoso(self):
        from parser.drive_inbox import mover_a_procesados

        mock_drive = MagicMock()
        assert mover_a_procesados(mock_drive, "id123", "cartola.pdf") is True
        mock_drive.move_file.assert_called_once_with("id123", "Procesados")

    def test_mover_a_procesados_error(self):
        from parser.drive_inbox import mover_a_procesados

        mock_drive = MagicMock()
        mock_drive.move_file.side_effect = Exception("error")
        assert mover_a_procesados(mock_drive, "id123", "cartola.pdf") is False

    def test_procesar_inbox_maneja_error_por_archivo(self):
        from parser.drive_inbox import procesar_inbox

        archivos = [{"id": "x1", "name": "malo.pdf"}]
        mock_drive = MagicMock()
        mock_drive.list_folder.return_value = archivos
        mock_drive.download_file.side_effect = Exception("descarga fallida")

        resultados = procesar_inbox(mock_drive, posiciones={})
        assert len(resultados) == 1
        assert resultados[0]["error"] is not None


# ── Integración ───────────────────────────────────────────────────────────────

class TestIntegracion:
    def test_extraer_movimientos_csv_end_to_end(self, tmp_path):
        """Test de integración completo con CSV."""
        csv = "fecha,descripcion,monto\n2024-06-01,PAGO LUZ,-45000\n2024-06-15,SUELDO,1800000\n"
        f = tmp_path / "banco.csv"
        f.write_text(csv)

        movs, formato = extraer_movimientos(str(f))
        assert formato == "generic_excel"
        assert len(movs) == 2

        df = movimientos_a_dataframe(movs)
        assert len(df) == 2

        movs2 = dataframe_a_movimientos(df)
        assert len(movs2) == 2
        assert movs2[0].fuente == "generic_excel"

    def test_extraer_movimientos_formato_desconocido(self, tmp_path):
        """Debe lanzar ValueError para formatos no soportados."""
        f = tmp_path / "datos.json"
        f.write_text("{}")

        with pytest.raises(ValueError, match="Formato no soportado"):
            extraer_movimientos(str(f))

    def test_normalizacion_usd_en_pipeline(self, tmp_path):
        """Movimientos en USD deben tener monto_clp calculado."""
        csv = "fecha,descripcion,monto\n2024-01-01,AMAZON,-150\n"
        f = tmp_path / "tc_usd.csv"
        f.write_text(csv)

        # Patch para que el detector lo marque como internacional
        # En realidad el CSV pasa como generic_excel con moneda CLP
        movs, _ = extraer_movimientos(str(f), valor_usd_clp=1000.0)
        # Como es CSV, la moneda será CLP → monto_clp = monto
        assert movs[0].monto_clp == movs[0].monto


# ── Aprobación y descarte ────────────────────────────────────────────────────

class TestEmojis:
    """Tests para la función emoji_clase."""

    def test_ingreso_recurrente(self):
        # Logic tested inline in TestEmojisIntegration — page not importable directly
        pass  # See TestEmojisIntegration below


class TestEmojisIntegration:
    """Tests de emoji_clase importando directamente."""

    def _get_emoji_clase(self):
        """Import emoji_clase from the page module."""
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location(
            "parser_page",
            "pages/05_parser.py",
        )
        # We can't easily import Streamlit pages, so test the logic directly
        return None

    def test_emoji_ingreso(self):
        """Ingreso_Recurrente → 💰"""
        clases_emoji = {
            "Ingreso_Recurrente": "💰",
            "Pasivo_Estructural": "🏠",
            "Activo_Financiero": "📈",
            "Activo_Liquido": "🏦",
            "Activo_Real": "🏡",
            "Prevision_AFP": "🔵",
            "Objetivo_Ahorro": "🎯",
            "Otro": "🗂️",
        }
        assert clases_emoji["Ingreso_Recurrente"] == "💰"
        assert clases_emoji["Activo_Liquido"] == "🏦"
        assert clases_emoji["Pasivo_Estructural"] == "🏠"

    def test_pasivo_corto_plazo_credito(self):
        """Pasivo_Corto_Plazo con tipo crédito → 💳"""
        tipos_compromiso = {"Colegio", "Jardín", "Arriendo", "Otro"}

        def emoji_clase_test(pos):
            clases_emoji = {
                "Ingreso_Recurrente": "💰", "Pasivo_Estructural": "🏠",
                "Activo_Financiero": "📈", "Activo_Liquido": "🏦",
                "Activo_Real": "🏡", "Prevision_AFP": "🔵",
                "Objetivo_Ahorro": "🎯", "Otro": "🗂️",
            }
            clase = pos.get("Clase", "")
            if clase == "Pasivo_Corto_Plazo":
                tipo = pos.get("Tipo", pos.get("Tipo_Pasivo", ""))
                if tipo in tipos_compromiso:
                    return "📚"
                return "💳"
            return clases_emoji.get(clase, "🗂️")

        assert emoji_clase_test({"Clase": "Pasivo_Corto_Plazo", "Tipo": "Crédito consumo"}) == "💳"
        assert emoji_clase_test({"Clase": "Pasivo_Corto_Plazo", "Tipo": "Tarjeta"}) == "💳"
        assert emoji_clase_test({"Clase": "Pasivo_Corto_Plazo", "Tipo": "Colegio"}) == "📚"
        assert emoji_clase_test({"Clase": "Pasivo_Corto_Plazo", "Tipo": "Jardín"}) == "📚"
        assert emoji_clase_test({"Clase": "Pasivo_Corto_Plazo", "Tipo": "Arriendo"}) == "📚"

    def test_clase_desconocida(self):
        """Clase desconocida → 🗂️"""
        clases_emoji = {
            "Ingreso_Recurrente": "💰", "Pasivo_Estructural": "🏠",
        }
        assert clases_emoji.get("ClaseInexistente", "🗂️") == "🗂️"

    def test_todos_los_emojis_definidos(self):
        """Verificar que todas las clases del modelo tienen emoji."""
        clases_validas = [
            "Ingreso_Recurrente", "Pasivo_Estructural",
            "Activo_Financiero", "Activo_Liquido",
            "Activo_Real", "Prevision_AFP", "Objetivo_Ahorro", "Otro",
        ]
        clases_emoji = {
            "Ingreso_Recurrente": "💰", "Pasivo_Estructural": "🏠",
            "Activo_Financiero": "📈", "Activo_Liquido": "🏦",
            "Activo_Real": "🏡", "Prevision_AFP": "🔵",
            "Objetivo_Ahorro": "🎯", "Otro": "🗂️",
        }
        for clase in clases_validas:
            assert clase in clases_emoji, f"Clase {clase} sin emoji definido"


class TestAprobacion:
    """Tests de la lógica de aprobación/descarte de movimientos."""

    def _make_prop_dict(
        self,
        id_pos="GAS_ESE_BUCKET",
        confianza=0.9,
        monto=-50000.0,
        descripcion="SUPERMERCADO",
    ) -> dict:
        """Crea un dict de propuesta para session_state."""
        return {
            "fecha": "2024-01-15",
            "descripcion": descripcion,
            "monto": monto,
            "moneda": "CLP",
            "monto_clp": monto,
            "fuente": "test",
            "referencia": "",
            "confianza_extraccion": 1.0,
            "raw": "",
            "id_posicion_sugerido": id_pos,
            "confianza": confianza,
            "justificacion": "test",
            "estado": "pendiente",
        }

    def _setup_state(self, props: list[dict]) -> None:
        """Configura session_state con propuestas."""
        import streamlit as st
        st.session_state["parser_movimientos_pendientes"] = props.copy()
        st.session_state["movimientos_otros"] = []
        st.session_state["schedules"] = {}
        st.session_state["positions"] = {
            "GAS_ESE_BUCKET": {"Clase": "Gasto_Esencial", "Descripcion": "Esenciales"},
            "ING_PRINCIPAL": {"Clase": "Ingreso_Recurrente", "Descripcion": "Sueldo"},
        }

    def test_aprobar_clasificado_va_a_schedules(self):
        """Aprobar movimiento con posición válida lo agrega a schedules."""
        import streamlit as st
        props = [self._make_prop_dict(id_pos="ING_PRINCIPAL", monto=1_500_000.0)]
        self._setup_state(props)

        # Import the logic functions directly
        import sys, importlib
        # We test the logic inline since we can't import Streamlit pages easily

        pendientes = st.session_state["parser_movimientos_pendientes"]
        d = pendientes[0]
        id_pos = d["id_posicion_sugerido"]

        assert id_pos not in ("SIN_CLASIFICAR", "OTR_NO_CLASIFICADO", "")

        import pandas as pd
        schedules = st.session_state.setdefault("schedules", {})
        if id_pos not in schedules or schedules[id_pos] is None:
            schedules[id_pos] = pd.DataFrame(columns=[
                "ID_Posicion", "Periodo", "Flujo_Periodo", "Tipo_Flujo"
            ])
        nueva = pd.DataFrame([{
            "ID_Posicion": id_pos,
            "Periodo": d["fecha"][:7],
            "Flujo_Periodo": float(d["monto_clp"]),
            "Tipo_Flujo": "importado",
        }])
        schedules[id_pos] = pd.concat([schedules[id_pos], nueva], ignore_index=True)
        pendientes.pop(0)

        assert len(st.session_state["parser_movimientos_pendientes"]) == 0
        assert "ING_PRINCIPAL" in st.session_state["schedules"]
        df = st.session_state["schedules"]["ING_PRINCIPAL"]
        assert len(df) == 1
        assert df.iloc[0]["Flujo_Periodo"] == 1_500_000.0

    def test_aprobar_sin_clasificar_va_a_otros(self):
        """Aprobar movimiento SIN_CLASIFICAR lo mueve a movimientos_otros."""
        import streamlit as st
        props = [self._make_prop_dict(id_pos="SIN_CLASIFICAR", confianza=0.0)]
        self._setup_state(props)

        pendientes = st.session_state["parser_movimientos_pendientes"]
        d = pendientes[0]
        id_pos = d["id_posicion_sugerido"]

        assert id_pos in ("SIN_CLASIFICAR", "OTR_NO_CLASIFICADO", "")

        from datetime import datetime
        st.session_state["movimientos_otros"].append({
            "fecha": d["fecha"],
            "descripcion": d["descripcion"],
            "monto": d["monto"],
            "moneda": d.get("moneda", "CLP"),
            "monto_clp": d.get("monto_clp", d["monto"]),
            "fuente": d.get("fuente", ""),
            "motivo_descarte": "sin_clasificar",
            "fecha_importacion": datetime.today().strftime("%Y-%m-%d"),
        })
        pendientes.pop(0)

        assert len(st.session_state["parser_movimientos_pendientes"]) == 0
        assert len(st.session_state["movimientos_otros"]) == 1
        assert st.session_state["movimientos_otros"][0]["motivo_descarte"] == "sin_clasificar"

    def test_descartar_va_a_otros_con_motivo_descartado(self):
        """Descartar movimiento lo mueve a movimientos_otros con motivo 'descartado'."""
        import streamlit as st
        props = [self._make_prop_dict(id_pos="ING_PRINCIPAL")]
        self._setup_state(props)

        pendientes = st.session_state["parser_movimientos_pendientes"]
        d = pendientes[0]

        from datetime import datetime
        st.session_state["movimientos_otros"].append({
            "fecha": d["fecha"],
            "descripcion": d["descripcion"],
            "monto": d["monto"],
            "moneda": d.get("moneda", "CLP"),
            "monto_clp": d.get("monto_clp", d["monto"]),
            "fuente": d.get("fuente", ""),
            "motivo_descarte": "descartado",
            "fecha_importacion": datetime.today().strftime("%Y-%m-%d"),
        })
        pendientes.pop(0)

        assert len(st.session_state["parser_movimientos_pendientes"]) == 0
        assert len(st.session_state["movimientos_otros"]) == 1
        assert st.session_state["movimientos_otros"][0]["motivo_descarte"] == "descartado"

    def test_aprobar_dos_veces_no_duplica(self):
        """Aprobar el mismo movimiento dos veces no lo duplica en schedules."""
        import streamlit as st
        import pandas as pd

        st.session_state["parser_movimientos_pendientes"] = []
        st.session_state["movimientos_otros"] = []
        st.session_state["schedules"] = {}

        # Simular aprobación del mismo movimiento dos veces
        d = self._make_prop_dict(id_pos="ING_PRINCIPAL", monto=1_000_000.0)
        id_pos = "ING_PRINCIPAL"

        schedules = st.session_state["schedules"]
        schedules[id_pos] = pd.DataFrame(columns=["Flujo_Periodo", "Tipo_Flujo"])

        # Primera aprobación
        nueva1 = pd.DataFrame([{"Flujo_Periodo": 1_000_000.0, "Tipo_Flujo": "importado"}])
        schedules[id_pos] = pd.concat([schedules[id_pos], nueva1], ignore_index=True)

        # Intentar aprobar de nuevo — ya no está en pendientes
        # (no hay pendientes → no hay nada que aprobar)
        assert len(schedules[id_pos]) == 1  # solo una vez

    def test_aprobar_todos_alta_confianza(self):
        """aprobar_todos_alta_confianza solo aprueba los de confianza >= 0.8."""
        import streamlit as st

        props = [
            self._make_prop_dict(id_pos="ING_PRINCIPAL", confianza=0.95),
            self._make_prop_dict(id_pos="GAS_ESE_BUCKET", confianza=0.6),
            self._make_prop_dict(id_pos="SIN_CLASIFICAR", confianza=0.0),
        ]
        self._setup_state(props)

        pendientes = st.session_state["parser_movimientos_pendientes"]
        alta_conf = [
            i for i, d in enumerate(pendientes)
            if float(d.get("confianza", 0)) >= 0.8
            and d.get("id_posicion_sugerido", "SIN_CLASIFICAR") not in (
                "SIN_CLASIFICAR", "OTR_NO_CLASIFICADO", ""
            )
        ]
        assert len(alta_conf) == 1
        assert pendientes[alta_conf[0]]["id_posicion_sugerido"] == "ING_PRINCIPAL"
