"""Detecta el formato de una cartola bancaria."""
from __future__ import annotations
from pathlib import Path

try:
    import pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False


FORMATOS_CONOCIDOS: dict[str, list[str]] = {
    "itau_cta_cte": [
        "Cartola Histórica Cuenta corriente",
        "Cartola Historica Cuenta corriente",
        "Cartola histórica cuenta corriente",
        "CARTOLA HISTORICA CUENTA CORRIENTE",
    ],
    "itau_tc_nacional": [
        "ESTADO DE CUENTA NACIONAL DE TARJETA",
        "Estado de Cuenta Nacional de Tarjeta",
        "ESTADO DE CUENTA NACIONAL",
    ],
    "itau_tc_internacional": [
        "ESTADO DE CUENTA INTERNACIONAL DE TARJETA",
        "Estado de Cuenta Internacional de Tarjeta",
        "ESTADO DE CUENTA INTERNACIONAL",
    ],
}


def detectar_formato(filepath: str) -> str:
    """
    Detecta el formato del archivo de cartola.

    Returns:
        "itau_cta_cte" | "itau_tc_nacional" | "itau_tc_internacional"
        | "generic_pdf" | "generic_excel" | "desconocido"
    """
    ext = Path(filepath).suffix.lower()

    if ext in (".xlsx", ".xls", ".csv"):
        return "generic_excel"

    if ext == ".pdf":
        if not _PDFPLUMBER_OK:
            return "generic_pdf"

        try:
            with pdfplumber.open(filepath) as pdf:
                texto = ""
                for page in pdf.pages[:2]:
                    texto += (page.extract_text() or "")

            for formato, keywords in FORMATOS_CONOCIDOS.items():
                if any(kw in texto for kw in keywords):
                    return formato

            return "generic_pdf"
        except Exception:
            return "generic_pdf"

    return "desconocido"
