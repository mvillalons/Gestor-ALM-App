"""tests/test_drive.py — Tests para core/drive.py (v2 API).

Todos los tests usan mocks de la API de Google. No se realizan llamadas
reales a Drive. El servicio y las carpetas se pasan directamente a las
funciones públicas (patrón de inyección explícita).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from core import drive


# ---------------------------------------------------------------------------
# Fixtures compartidos
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc() -> MagicMock:
    """Mock del recurso de Drive v3."""
    return MagicMock()


@pytest.fixture()
def folders() -> dict[str, str]:
    """Dict de IDs de carpetas (simula ensure_folder_structure)."""
    return {
        "root": "root_id",
        "tablas": "tablas_id",
        "objetivos": "objetivos_id",
        "inbox": "inbox_id",
        "procesados": "procesados_id",
    }


def _make_fake_downloader(csv_content: str):
    """Fábrica de FakeDownloader para sustituir MediaIoBaseDownload.

    Escribe el contenido CSV en el buffer en __init__ (antes de que
    pd.read_csv lo lea), y retorna done=True en next_chunk.

    Args:
        csv_content: Texto CSV a inyectar en el buffer.
    """
    class FakeDownloader:
        def __init__(self, buf, request):  # buf primero — MediaIoBaseDownload(buf, request)
            buf.write(csv_content.encode("utf-8"))

        def next_chunk(self):
            return None, True

    return FakeDownloader


# ---------------------------------------------------------------------------
# authenticate_drive
# ---------------------------------------------------------------------------


class TestAuthenticateDrive:
    def test_usa_token_existente(self, tmp_path):
        """Si el token existe y es válido, no llama al flujo OAuth."""
        token_file = tmp_path / "token.json"
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = True

        with (
            patch("core.drive.os.path.exists", return_value=True),
            patch(
                "core.drive.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
            patch("core.drive.build", return_value=MagicMock()) as mock_build,
        ):
            result = drive.authenticate_drive(
                credentials_path=str(tmp_path / "creds.json"),
                token_path=str(token_file),
            )

        mock_build.assert_called_once_with("drive", "v3", credentials=mock_creds)
        assert result is not None

    def test_refresca_token_expirado(self, tmp_path):
        """Si el token existe pero expiró, lo refresca antes de construir."""
        token_file = tmp_path / "token.json"
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_tok"
        mock_creds.to_json.return_value = '{"refreshed": true}'

        with (
            patch("core.drive.os.path.exists", return_value=True),
            patch(
                "core.drive.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
            patch("core.drive.Request"),
            patch("core.drive.build", return_value=MagicMock()),
        ):
            drive.authenticate_drive(
                credentials_path=str(tmp_path / "creds.json"),
                token_path=str(token_file),
            )

        mock_creds.refresh.assert_called_once()

    def test_flujo_oauth_sin_token(self, tmp_path):
        """Si no hay token, lanza el flujo OAuth2 local."""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.to_json.return_value = "{}"

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds

        with (
            patch("core.drive.os.path.exists", return_value=False),
            patch(
                "core.drive.InstalledAppFlow.from_client_secrets_file",
                return_value=mock_flow,
            ),
            patch("core.drive.build", return_value=MagicMock()),
        ):
            drive.authenticate_drive(
                credentials_path=str(tmp_path / "creds.json"),
                token_path=str(tmp_path / "token.json"),
            )

        mock_flow.run_local_server.assert_called_once_with(port=0)


# ---------------------------------------------------------------------------
# get_or_create_folder
# ---------------------------------------------------------------------------


class TestGetOrCreateFolder:
    def test_retorna_id_si_existe(self, svc):
        """Si la carpeta ya existe, retorna su ID sin crear."""
        svc.files().list().execute.return_value = {
            "files": [{"id": "folder_abc"}]
        }

        result = drive.get_or_create_folder(svc, "ALM_Data")

        assert result == "folder_abc"
        svc.files().create.assert_not_called()

    def test_crea_si_no_existe(self, svc):
        """Si la carpeta no existe, la crea y retorna el nuevo ID."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_folder_id"}

        result = drive.get_or_create_folder(svc, "ALM_Data")

        assert result == "new_folder_id"

    def test_incluye_parent_en_query(self, svc):
        """Si se pasa parent_id, lo incluye en la query de búsqueda."""
        svc.files().list().execute.return_value = {"files": [{"id": "x"}]}

        drive.get_or_create_folder(svc, "Tablas_Desarrollo", parent_id="root_id")

        call_kwargs = svc.files().list.call_args
        q_arg = call_kwargs[1]["q"]
        assert "'root_id' in parents" in q_arg

    def test_incluye_parent_en_metadata_al_crear(self, svc):
        """Al crear, incluye el parent_id en el metadata de la carpeta."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        drive.get_or_create_folder(svc, "Inbox", parent_id="root_id")

        create_kwargs = svc.files().create.call_args
        body = create_kwargs[1]["body"]
        assert "parents" in body
        assert body["parents"] == ["root_id"]


# ---------------------------------------------------------------------------
# ensure_folder_structure
# ---------------------------------------------------------------------------


class TestEnsureFolderStructure:
    def test_retorna_dict_con_cinco_claves(self, svc):
        """Retorna dict con las 5 claves esperadas."""
        # Primera llamada (root) → "root_id"; las siguientes → ids únicos
        svc.files().list().execute.side_effect = [
            {"files": [{"id": "root_id"}]},
            {"files": [{"id": "tablas_id"}]},
            {"files": [{"id": "obj_id"}]},
            {"files": [{"id": "inbox_id"}]},
            {"files": [{"id": "proc_id"}]},
        ]

        result = drive.ensure_folder_structure(svc)

        assert set(result.keys()) == {"root", "tablas", "objetivos", "inbox", "procesados"}
        assert result["root"] == "root_id"
        assert result["tablas"] == "tablas_id"
        assert result["objetivos"] == "obj_id"
        assert result["inbox"] == "inbox_id"
        assert result["procesados"] == "proc_id"

    def test_crea_carpetas_faltantes(self, svc):
        """Si las carpetas no existen, las crea."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "created_id"}

        result = drive.ensure_folder_structure(svc)

        assert all(v == "created_id" for v in result.values())


# ---------------------------------------------------------------------------
# _find_file (helper interno)
# ---------------------------------------------------------------------------


class TestFindFile:
    def test_retorna_id_si_existe(self, svc):
        svc.files().list().execute.return_value = {
            "files": [{"id": "file_xyz"}]
        }
        result = drive._find_file(svc, "test.csv", "folder_id")
        assert result == "file_xyz"

    def test_retorna_none_si_no_existe(self, svc):
        svc.files().list().execute.return_value = {"files": []}
        result = drive._find_file(svc, "test.csv", "folder_id")
        assert result is None

    def test_query_excluye_carpetas(self, svc):
        """La query filtra mimeType != folder y trashed=false."""
        svc.files().list().execute.return_value = {"files": []}
        drive._find_file(svc, "test.csv", "folder_id")

        q_arg = svc.files().list.call_args[1]["q"]
        assert f"mimeType!='{drive._MIME_FOLDER}'" in q_arg
        assert "trashed=false" in q_arg


# ---------------------------------------------------------------------------
# load_csv
# ---------------------------------------------------------------------------


class TestLoadCsv:
    def test_retorna_none_si_no_existe(self, svc, folders):
        """Si el archivo no existe, retorna None."""
        svc.files().list().execute.return_value = {"files": []}

        result = drive.load_csv(svc, "test.csv", folders["root"])

        assert result is None

    def test_retorna_dataframe_si_existe(self, svc, folders):
        """Si el archivo existe, descarga y retorna DataFrame."""
        svc.files().list().execute.return_value = {"files": [{"id": "fid"}]}
        svc.files().get_media.return_value = MagicMock()

        csv_content = "col1,col2\n1,2\n3,4\n"
        with patch(
            "core.drive.MediaIoBaseDownload",
            _make_fake_downloader(csv_content),
        ):
            df = drive.load_csv(svc, "test.csv", folders["root"])

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["col1", "col2"]
        assert len(df) == 2

    def test_llama_get_media_con_file_id(self, svc, folders):
        """Llama a get_media con el file_id correcto."""
        svc.files().list().execute.return_value = {"files": [{"id": "fid_123"}]}
        svc.files().get_media.return_value = MagicMock()

        with patch(
            "core.drive.MediaIoBaseDownload",
            _make_fake_downloader("a,b\n1,2\n"),
        ):
            drive.load_csv(svc, "test.csv", folders["root"])

        svc.files().get_media.assert_called_once_with(fileId="fid_123")


# ---------------------------------------------------------------------------
# save_csv
# ---------------------------------------------------------------------------


class TestSaveCsv:
    def test_crea_archivo_si_no_existe(self, svc, folders):
        """Si el archivo no existe, llama a files().create()."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        df = pd.DataFrame({"col": [1, 2]})
        drive.save_csv(svc, "out.csv", folders["root"], df)

        svc.files().create.assert_called()

    def test_actualiza_archivo_si_existe(self, svc, folders):
        """Si el archivo existe, llama a files().update() con el file_id."""
        svc.files().list().execute.return_value = {"files": [{"id": "existing_id"}]}
        svc.files().update().execute.return_value = {"id": "existing_id"}

        df = pd.DataFrame({"col": [1, 2]})
        drive.save_csv(svc, "out.csv", folders["root"], df)

        update_call_kwargs = svc.files().update.call_args[1]
        assert update_call_kwargs["fileId"] == "existing_id"

    def test_no_crea_si_existe(self, svc, folders):
        """Si el archivo existe, NO llama a create."""
        svc.files().list().execute.return_value = {"files": [{"id": "existing_id"}]}
        svc.files().update().execute.return_value = {"id": "existing_id"}

        drive.save_csv(svc, "out.csv", folders["root"], pd.DataFrame())

        svc.files().create.assert_not_called()

    def test_serializa_csv_sin_indice(self, svc, folders):
        """El CSV subido no incluye el índice de pandas."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        captured: list[bytes] = []

        class CapturingUpload:
            def __init__(self, buf, mimetype, resumable):
                captured.append(buf.read())

        with patch("core.drive.MediaIoBaseUpload", CapturingUpload):
            df = pd.DataFrame({"a": [1], "b": [2]})
            drive.save_csv(svc, "out.csv", folders["root"], df)

        csv_text = captured[0].decode("utf-8")
        assert csv_text.startswith("a,b\n")


# ---------------------------------------------------------------------------
# load_positions
# ---------------------------------------------------------------------------


class TestLoadPositions:
    def test_retorna_dict_vacio_si_no_hay_archivo(self, svc, folders):
        svc.files().list().execute.return_value = {"files": []}
        result = drive.load_positions(svc, folders)
        assert result == {}

    def test_retorna_dict_con_posiciones(self, svc, folders):
        svc.files().list().execute.return_value = {"files": [{"id": "fid"}]}
        svc.files().get_media.return_value = MagicMock()

        csv_content = "ID_Posicion,Clase,Moneda\nING_001,Ingreso_Recurrente,CLP\n"
        with patch(
            "core.drive.MediaIoBaseDownload",
            _make_fake_downloader(csv_content),
        ):
            result = drive.load_positions(svc, folders)

        assert "ING_001" in result
        assert result["ING_001"]["Clase"] == "Ingreso_Recurrente"
        assert result["ING_001"]["Moneda"] == "CLP"

    def test_retorna_dict_vacio_si_csv_sin_columna_id(self, svc, folders):
        """Si el CSV no tiene columna ID_Posicion, retorna {}."""
        svc.files().list().execute.return_value = {"files": [{"id": "fid"}]}
        svc.files().get_media.return_value = MagicMock()

        csv_content = "Clase,Moneda\nIngreso_Recurrente,CLP\n"
        with patch(
            "core.drive.MediaIoBaseDownload",
            _make_fake_downloader(csv_content),
        ):
            result = drive.load_positions(svc, folders)

        assert result == {}

    def test_retorna_dict_vacio_si_csv_vacio(self, svc, folders):
        """Si el CSV solo tiene cabecera (sin filas), retorna {}."""
        svc.files().list().execute.return_value = {"files": [{"id": "fid"}]}
        svc.files().get_media.return_value = MagicMock()

        csv_content = "ID_Posicion,Clase\n"
        with patch(
            "core.drive.MediaIoBaseDownload",
            _make_fake_downloader(csv_content),
        ):
            result = drive.load_positions(svc, folders)

        assert result == {}


# ---------------------------------------------------------------------------
# save_positions
# ---------------------------------------------------------------------------


class TestSavePositions:
    def test_guarda_posiciones_no_vacias(self, svc, folders):
        """Con posiciones, construye DataFrame y llama save_csv."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        positions = {"ING_001": {"Clase": "Ingreso_Recurrente", "Moneda": "CLP"}}

        with patch("core.drive.state.mark_clean") as mock_clean:
            drive.save_positions(svc, folders, positions)
            mock_clean.assert_called_once()

    def test_guarda_dict_vacio_con_cabecera(self, svc, folders):
        """Con positions={}, escribe CSV con solo ID_Posicion como cabecera."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        captured: list[bytes] = []

        class CapturingUpload:
            def __init__(self, buf, mimetype, resumable):
                captured.append(buf.read())

        with (
            patch("core.drive.MediaIoBaseUpload", CapturingUpload),
            patch("core.drive.state.mark_clean"),
        ):
            drive.save_positions(svc, folders, {})

        csv_text = captured[0].decode("utf-8")
        assert "ID_Posicion" in csv_text

    def test_llama_mark_clean_despues_de_guardar(self, svc, folders):
        """mark_clean se llama con un timestamp datetime."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        with patch("core.drive.state.mark_clean") as mock_clean:
            drive.save_positions(svc, folders, {})

        args, _ = mock_clean.call_args
        assert isinstance(args[0], datetime)

    def test_mark_clean_con_timestamp_utc(self, svc, folders):
        """El timestamp pasado a mark_clean está en UTC."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        with patch("core.drive.state.mark_clean") as mock_clean:
            drive.save_positions(svc, folders, {})

        ts: datetime = mock_clean.call_args[0][0]
        assert ts.tzinfo is not None

    def test_no_llama_mark_clean_si_falla_subida(self, svc, folders):
        """Si save_csv lanza excepción, mark_clean no debe ser llamado."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.side_effect = RuntimeError("Drive error")

        with (
            patch("core.drive.state.mark_clean") as mock_clean,
            pytest.raises(RuntimeError),
        ):
            drive.save_positions(svc, folders, {})

        mock_clean.assert_not_called()


# ---------------------------------------------------------------------------
# load_schedule
# ---------------------------------------------------------------------------


class TestLoadSchedule:
    def test_retorna_none_si_no_existe(self, svc, folders):
        svc.files().list().execute.return_value = {"files": []}
        result = drive.load_schedule(svc, folders, "PAS_HIP_001")
        assert result is None

    def test_retorna_dataframe_si_existe(self, svc, folders):
        svc.files().list().execute.return_value = {"files": [{"id": "fid"}]}
        svc.files().get_media.return_value = MagicMock()

        csv_content = (
            "ID_Posicion,Periodo,Saldo_Inicial,Flujo_Periodo,"
            "Rendimiento_Costo,Amortizacion,Saldo_Final,Moneda,Tipo_Flujo,Notas\n"
            "PAS_HIP_001,2024-01,100000,-500,-300,200,99800,CLP,calculado,\n"
        )
        with patch(
            "core.drive.MediaIoBaseDownload",
            _make_fake_downloader(csv_content),
        ):
            df = drive.load_schedule(svc, folders, "PAS_HIP_001")

        assert isinstance(df, pd.DataFrame)
        assert df.iloc[0]["ID_Posicion"] == "PAS_HIP_001"

    def test_busca_en_carpeta_tablas(self, svc, folders):
        """Busca el archivo en la carpeta 'tablas', no en 'root'."""
        svc.files().list().execute.return_value = {"files": []}

        drive.load_schedule(svc, folders, "PAS_HIP_001")

        q_arg = svc.files().list.call_args[1]["q"]
        assert f"'{folders['tablas']}' in parents" in q_arg

    def test_nombre_archivo_correcto(self, svc, folders):
        """Busca el archivo con el nombre Tabla_<id_posicion>.csv."""
        svc.files().list().execute.return_value = {"files": []}

        drive.load_schedule(svc, folders, "PAS_HIP_001")

        q_arg = svc.files().list.call_args[1]["q"]
        assert "Tabla_PAS_HIP_001.csv" in q_arg


# ---------------------------------------------------------------------------
# save_schedule
# ---------------------------------------------------------------------------


class TestSaveSchedule:
    def test_crea_archivo_si_no_existe(self, svc, folders):
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        df = pd.DataFrame({"ID_Posicion": ["PAS_001"], "Periodo": ["2024-01"]})
        drive.save_schedule(svc, folders, "PAS_001", df)

        svc.files().create.assert_called()

    def test_actualiza_archivo_si_existe(self, svc, folders):
        svc.files().list().execute.return_value = {"files": [{"id": "exist_id"}]}
        svc.files().update().execute.return_value = {"id": "exist_id"}

        df = pd.DataFrame({"ID_Posicion": ["PAS_001"], "Periodo": ["2024-01"]})
        drive.save_schedule(svc, folders, "PAS_001", df)

        update_kwargs = svc.files().update.call_args[1]
        assert update_kwargs["fileId"] == "exist_id"

    def test_nombre_archivo_correcto(self, svc, folders):
        """El archivo guardado se llama Tabla_<id_posicion>.csv."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        drive.save_schedule(svc, folders, "ACT_FM_001", pd.DataFrame())

        create_kwargs = svc.files().create.call_args[1]
        body = create_kwargs["body"]
        assert body["name"] == "Tabla_ACT_FM_001.csv"

    def test_guarda_en_carpeta_tablas(self, svc, folders):
        """El archivo se crea dentro de la carpeta tablas."""
        svc.files().list().execute.return_value = {"files": []}
        svc.files().create().execute.return_value = {"id": "new_id"}

        drive.save_schedule(svc, folders, "PAS_001", pd.DataFrame())

        create_kwargs = svc.files().create.call_args[1]
        body = create_kwargs["body"]
        assert folders["tablas"] in body["parents"]


# ---------------------------------------------------------------------------
# list_inbox
# ---------------------------------------------------------------------------


class TestListInbox:
    def test_retorna_lista_vacia_si_no_hay_pdfs(self, svc, folders):
        svc.files().list().execute.return_value = {"files": []}
        result = drive.list_inbox(svc, folders)
        assert result == []

    def test_retorna_lista_de_pdfs(self, svc, folders):
        svc.files().list().execute.return_value = {
            "files": [
                {"id": "pdf1", "name": "cartola_ene.pdf"},
                {"id": "pdf2", "name": "cartola_feb.pdf"},
            ]
        }
        result = drive.list_inbox(svc, folders)
        assert len(result) == 2
        assert result[0]["id"] == "pdf1"

    def test_filtra_por_mime_pdf(self, svc, folders):
        """La query incluye el filtro de mimeType PDF."""
        svc.files().list().execute.return_value = {"files": []}
        drive.list_inbox(svc, folders)

        q_arg = svc.files().list.call_args[1]["q"]
        assert f"mimeType='{drive._MIME_PDF}'" in q_arg

    def test_busca_en_carpeta_inbox(self, svc, folders):
        """La query usa el ID de la carpeta inbox."""
        svc.files().list().execute.return_value = {"files": []}
        drive.list_inbox(svc, folders)

        q_arg = svc.files().list.call_args[1]["q"]
        assert f"'{folders['inbox']}' in parents" in q_arg


# ---------------------------------------------------------------------------
# move_to_procesados
# ---------------------------------------------------------------------------


class TestMoveToProcesados:
    def test_llama_update_con_add_remove_parents(self, svc, folders):
        """Llama a files().update() con addParents y removeParents correctos."""
        svc.files().update().execute.return_value = {}

        drive.move_to_procesados(svc, folders, "file_abc")

        update_kwargs = svc.files().update.call_args[1]
        assert update_kwargs["fileId"] == "file_abc"
        assert update_kwargs["addParents"] == folders["procesados"]
        assert update_kwargs["removeParents"] == folders["inbox"]

    def test_no_crea_ni_descarga(self, svc, folders):
        """No llama a create ni get_media (solo mueve)."""
        svc.files().update().execute.return_value = {}

        drive.move_to_procesados(svc, folders, "file_abc")

        svc.files().create.assert_not_called()
        svc.files().get_media.assert_not_called()
