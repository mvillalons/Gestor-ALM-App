"""
Monitor de inbox en Google Drive para cartolas PDF.

Detecta archivos nuevos en /ALM_Data/Inbox/,
los extrae, clasifica y mueve a /ALM_Data/Procesados/.
"""
from __future__ import annotations
import tempfile
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def listar_inbox(drive_client) -> list[dict]:
    """
    Lista archivos en /ALM_Data/Inbox/ pendientes de procesar.

    Args:
        drive_client: Cliente de Google Drive (core/drive.py).

    Returns:
        Lista de dicts con 'id', 'name', 'mimeType'.
    """
    try:
        return drive_client.list_folder("Inbox") or []
    except Exception:
        return []


def descargar_y_extraer(
    drive_client,
    file_id: str,
    file_name: str,
    valor_usd_clp: float = 950.0,
    valor_uf_clp: float = 39700.0,
) -> tuple[list, str]:
    """
    Descarga un archivo de Drive y extrae sus movimientos.

    Returns:
        Tupla (movimientos, formato_detectado).
    """
    from parser.normalizer import extraer_movimientos

    suffix = Path(file_name).suffix or ".pdf"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name

    try:
        drive_client.download_file(file_id, tmp_path)
        movimientos, formato = extraer_movimientos(
            tmp_path,
            valor_usd_clp=valor_usd_clp,
            valor_uf_clp=valor_uf_clp,
        )
        return movimientos, formato
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def mover_a_procesados(drive_client, file_id: str, file_name: str) -> bool:
    """
    Mueve un archivo de Inbox a Procesados en Drive.

    Returns:
        True si tuvo éxito, False si falló.
    """
    try:
        drive_client.move_file(file_id, "Procesados")
        return True
    except Exception:
        return False


def procesar_inbox(
    drive_client,
    posiciones: dict,
    anthropic_api_key: str | None = None,
    valor_usd_clp: float = 950.0,
    valor_uf_clp: float = 39700.0,
) -> list[dict]:
    """
    Procesa todos los archivos pendientes en el Inbox.

    Para cada archivo:
    1. Descarga y extrae movimientos.
    2. Si hay API key, clasifica con LLM.
    3. Retorna propuestas para revisión en UI.
    4. NO mueve a Procesados — eso lo hace la UI después de la aprobación.

    Returns:
        Lista de dicts con 'file_id', 'file_name', 'formato',
        'movimientos', 'propuestas'.
    """
    archivos = listar_inbox(drive_client)
    resultados = []

    for archivo in archivos:
        file_id = archivo.get("id", "")
        file_name = archivo.get("name", "")

        try:
            movimientos, formato = descargar_y_extraer(
                drive_client, file_id, file_name,
                valor_usd_clp=valor_usd_clp,
                valor_uf_clp=valor_uf_clp,
            )

            propuestas = []
            if anthropic_api_key and movimientos:
                from parser.llm_classifier import clasificar_movimientos
                propuestas = clasificar_movimientos(
                    movimientos, posiciones, anthropic_api_key
                )

            resultados.append({
                "file_id": file_id,
                "file_name": file_name,
                "formato": formato,
                "movimientos": movimientos,
                "propuestas": propuestas,
                "error": None,
            })

        except Exception as exc:
            resultados.append({
                "file_id": file_id,
                "file_name": file_name,
                "formato": "error",
                "movimientos": [],
                "propuestas": [],
                "error": str(exc),
            })

    return resultados
