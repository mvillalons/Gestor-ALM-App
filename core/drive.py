"""core/drive.py — Persistencia con Google Drive (scope: drive.file).

Todas las funciones son independientes de Streamlit excepto
``authenticate_drive``, que se decora con ``@st.cache_resource`` para
reutilizar el recurso entre reruns de Streamlit sin reconectar.

Estructura de carpetas en Drive::

    ALM_Data/
    ├── ALM_Posiciones_Balance.csv
    ├── Tablas_Desarrollo/
    │   └── Tabla_<ID_Posicion>.csv
    ├── Objetivos/
    │   └── OBJ_<ID>.csv
    ├── Inbox/
    │   └── *.pdf
    └── Procesados/
        └── *.pdf

Primera ejecución:
    Las carpetas y archivos se crean automáticamente si no existen.
    ``load_positions()`` retorna ``{}`` hasta que se guarden datos.
    ``load_schedule()`` retorna ``None`` hasta que se genere la tabla.

Patrón de uso (producción)::

    service = authenticate_drive()
    folders = ensure_folder_structure(service)
    positions = load_positions(service, folders)
    save_positions(service, folders, positions)   # → llama state.mark_clean()

Patrón de uso (tests)::

    # Pasar mocks de service y folders directamente a cada función.
    # No se necesita parchear authenticate_drive.
"""

from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from core import state

# ---------------------------------------------------------------------------
# Constantes públicas
# ---------------------------------------------------------------------------

SCOPES: list[str] = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_PATH: str = "token.json"
CREDENTIALS_PATH: str = "credentials.json"

DRIVE_ROOT: str = "ALM_Data"
TABLAS_FOLDER: str = "Tablas_Desarrollo"
OBJETIVOS_FOLDER: str = "Objetivos"
INBOX_FOLDER: str = "Inbox"
PROCESADOS_FOLDER: str = "Procesados"
POSITIONS_FILENAME: str = "ALM_Posiciones_Balance.csv"

_MIME_CSV: str = "text/csv"
_MIME_FOLDER: str = "application/vnd.google-apps.folder"
_MIME_PDF: str = "application/pdf"


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------


def authenticate_drive(
    credentials_path: str = CREDENTIALS_PATH,
    token_path: str = TOKEN_PATH,
) -> Any:
    """Autentica contra Google Drive con scope ``drive.file``.

    Carga el token desde ``token_path`` si existe. Si está expirado lo
    refresca automáticamente. Si no existe token (primera ejecución),
    lanza el flujo OAuth2 local y abre el navegador.

    Decorado con ``@st.cache_resource`` en producción para reutilizar el
    recurso entre reruns de Streamlit sin reconectar en cada petición.

    Args:
        credentials_path: Ruta al ``credentials.json`` (Google Cloud Console,
            tipo Desktop app).
        token_path: Ruta donde se guarda y lee el token de sesión.

    Returns:
        Recurso ``googleapiclient.discovery.Resource`` de Drive v3 autenticado.

    Raises:
        FileNotFoundError: Si ``credentials_path`` no existe y no hay token.
        google.auth.exceptions.RefreshError: Si el token expiró y no pudo
            refrescarse (ej: acceso revocado por el usuario).
    """
    creds: Credentials | None = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as fh:
            fh.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Gestión de carpetas
# ---------------------------------------------------------------------------


def get_or_create_folder(
    service: Any,
    name: str,
    parent_id: str | None = None,
) -> str:
    """Retorna el ID de una carpeta, creándola si no existe.

    Args:
        service: Recurso de Drive autenticado.
        name: Nombre de la carpeta.
        parent_id: ID de la carpeta padre. ``None`` = raíz del Drive.

    Returns:
        ID de la carpeta (existente o recién creada).
    """
    q = f"name='{name}' and mimeType='{_MIME_FOLDER}' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"

    result = (
        service.files()
        .list(q=q, spaces="drive", fields="files(id)", pageSize=1)
        .execute()
    )
    files = result.get("files", [])
    if files:
        return files[0]["id"]

    metadata: dict[str, Any] = {"name": name, "mimeType": _MIME_FOLDER}
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def ensure_folder_structure(service: Any) -> dict[str, str]:
    """Garantiza que toda la estructura de carpetas existe en Drive.

    Crea las carpetas que no existan. Idempotente: si ya existen, solo
    resuelve y retorna sus IDs sin crear duplicados.

    Args:
        service: Recurso de Drive autenticado.

    Returns:
        Diccionario con los IDs de todas las carpetas::

            {
                "root":       "<id ALM_Data>",
                "tablas":     "<id Tablas_Desarrollo>",
                "objetivos":  "<id Objetivos>",
                "inbox":      "<id Inbox>",
                "procesados": "<id Procesados>",
            }
    """
    root_id = get_or_create_folder(service, DRIVE_ROOT)
    tablas_id = get_or_create_folder(service, TABLAS_FOLDER, root_id)
    objetivos_id = get_or_create_folder(service, OBJETIVOS_FOLDER, root_id)
    inbox_id = get_or_create_folder(service, INBOX_FOLDER, root_id)
    procesados_id = get_or_create_folder(service, PROCESADOS_FOLDER, root_id)

    return {
        "root": root_id,
        "tablas": tablas_id,
        "objetivos": objetivos_id,
        "inbox": inbox_id,
        "procesados": procesados_id,
    }


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _find_file(service: Any, name: str, folder_id: str) -> str | None:
    """Busca un archivo (no carpeta) por nombre dentro de una carpeta.

    Args:
        service: Recurso de Drive autenticado.
        name: Nombre exacto del archivo.
        folder_id: ID de la carpeta donde buscar.

    Returns:
        ID del archivo si existe, ``None`` si no.
    """
    q = (
        f"name='{name}' and '{folder_id}' in parents"
        f" and mimeType!='{_MIME_FOLDER}' and trashed=false"
    )
    result = (
        service.files()
        .list(q=q, spaces="drive", fields="files(id)", pageSize=1)
        .execute()
    )
    files = result.get("files", [])
    return files[0]["id"] if files else None


# ---------------------------------------------------------------------------
# Lectura y escritura genérica de CSVs
# ---------------------------------------------------------------------------


def load_csv(
    service: Any,
    file_name: str,
    folder_id: str,
) -> pd.DataFrame | None:
    """Descarga un CSV de Drive y lo retorna como DataFrame.

    Args:
        service: Recurso de Drive autenticado.
        file_name: Nombre exacto del archivo CSV.
        folder_id: ID de la carpeta donde buscar.

    Returns:
        DataFrame con el contenido del CSV, o ``None`` si no existe.
    """
    file_id = _find_file(service, file_name, folder_id)
    if file_id is None:
        return None

    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return pd.read_csv(buf)


def save_csv(
    service: Any,
    file_name: str,
    folder_id: str,
    df: pd.DataFrame,
) -> None:
    """Sube (crea o actualiza) un DataFrame como CSV en Drive.

    Args:
        service: Recurso de Drive autenticado.
        file_name: Nombre del archivo en Drive.
        folder_id: ID de la carpeta destino.
        df: DataFrame a serializar como CSV UTF-8.
    """
    existing_id = _find_file(service, file_name, folder_id)

    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    buf.seek(0)
    media = MediaIoBaseUpload(buf, mimetype=_MIME_CSV, resumable=False)

    if existing_id:
        service.files().update(
            fileId=existing_id,
            body={},
            media_body=media,
            fields="id",
        ).execute()
    else:
        metadata: dict[str, Any] = {"name": file_name, "parents": [folder_id]}
        service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()


# ---------------------------------------------------------------------------
# Posiciones
# ---------------------------------------------------------------------------


def load_positions(
    service: Any,
    folders: dict[str, str],
) -> dict[str, dict]:
    """Lee ``ALM_Posiciones_Balance.csv`` desde Drive.

    Args:
        service: Recurso de Drive autenticado.
        folders: Dict de IDs de carpetas (de :func:`ensure_folder_structure`).

    Returns:
        Diccionario ``{ID_Posicion: {campo: valor, ...}, ...}``.
        Retorna ``{}`` si no hay datos guardados aún o el CSV está vacío.
    """
    df = load_csv(service, POSITIONS_FILENAME, folders["root"])
    if df is None or df.empty or "ID_Posicion" not in df.columns:
        return {}
    return df.set_index("ID_Posicion").to_dict(orient="index")


def save_positions(
    service: Any,
    folders: dict[str, str],
    positions: dict[str, dict],
) -> None:
    """Serializa y escribe ``ALM_Posiciones_Balance.csv`` en Drive.

    Llama a :func:`core.state.mark_clean` con el timestamp UTC actual
    después de guardar exitosamente.

    Args:
        service: Recurso de Drive autenticado.
        folders: Dict de IDs de carpetas (de :func:`ensure_folder_structure`).
        positions: Diccionario ``{ID_Posicion: {campo: valor, ...}, ...}``.
            Un diccionario vacío escribe un CSV con solo la cabecera.
    """
    if not positions:
        df = pd.DataFrame(columns=["ID_Posicion"])
    else:
        rows = [{"ID_Posicion": k, **v} for k, v in positions.items()]
        df = pd.DataFrame(rows)

    save_csv(service, POSITIONS_FILENAME, folders["root"], df)
    state.mark_clean(datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Tablas de desarrollo
# ---------------------------------------------------------------------------


def load_schedule(
    service: Any,
    folders: dict[str, str],
    id_posicion: str,
) -> pd.DataFrame | None:
    """Lee la tabla de desarrollo de una posición desde Drive.

    Args:
        service: Recurso de Drive autenticado.
        folders: Dict de IDs de carpetas.
        id_posicion: ID de la posición (p. ej. ``"PAS_HIP_001"``).

    Returns:
        DataFrame con las columnas estándar de tabla de desarrollo,
        o ``None`` si el archivo no existe aún.
    """
    filename = f"Tabla_{id_posicion}.csv"
    return load_csv(service, filename, folders["tablas"])


def save_schedule(
    service: Any,
    folders: dict[str, str],
    id_posicion: str,
    df: pd.DataFrame,
) -> None:
    """Escribe la tabla de desarrollo de una posición en Drive.

    Args:
        service: Recurso de Drive autenticado.
        folders: Dict de IDs de carpetas.
        id_posicion: ID de la posición (p. ej. ``"PAS_HIP_001"``).
        df: DataFrame con columnas estándar de tabla de desarrollo.
    """
    filename = f"Tabla_{id_posicion}.csv"
    save_csv(service, filename, folders["tablas"], df)


# ---------------------------------------------------------------------------
# Inbox / Procesados
# ---------------------------------------------------------------------------


def list_inbox(
    service: Any,
    folders: dict[str, str],
) -> list[dict]:
    """Lista los archivos PDF en la carpeta ``ALM_Data/Inbox/``.

    Args:
        service: Recurso de Drive autenticado.
        folders: Dict de IDs de carpetas.

    Returns:
        Lista de diccionarios ``[{"id": str, "name": str}, ...]``, uno por PDF.
        Lista vacía si no hay archivos pendientes.
    """
    q = (
        f"'{folders['inbox']}' in parents"
        f" and mimeType='{_MIME_PDF}'"
        " and trashed=false"
    )
    result = (
        service.files()
        .list(q=q, spaces="drive", fields="files(id, name)")
        .execute()
    )
    return result.get("files", [])


def move_to_procesados(
    service: Any,
    folders: dict[str, str],
    file_id: str,
) -> None:
    """Mueve un PDF de ``ALM_Data/Inbox/`` a ``ALM_Data/Procesados/``.

    Usa ``addParents`` / ``removeParents`` para reubicar sin copiar.

    Args:
        service: Recurso de Drive autenticado.
        folders: Dict de IDs de carpetas.
        file_id: ID del archivo a mover (obtenido de :func:`list_inbox`).
    """
    service.files().update(
        fileId=file_id,
        addParents=folders["procesados"],
        removeParents=folders["inbox"],
        fields="id, parents",
    ).execute()
