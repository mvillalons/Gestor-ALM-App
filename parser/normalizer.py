"""
Normaliza montos de movimientos a CLP y coordina la extracción.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import pandas as pd

from parser.models import Movimiento
from parser.detector import detectar_formato


# ── Extractor LLM ─────────────────────────────────────────────────────────────

_LLM_EXTRACTION_PROMPT = """\
Eres un experto en documentos bancarios chilenos.
Extrae TODOS los movimientos del siguiente documento.
Ignora totales, resúmenes y encabezados.

Para cada movimiento retorna un objeto JSON con estos campos exactos:
{{
  "fecha": "YYYY-MM-DD",
  "descripcion": "texto original del movimiento",
  "monto": -1500000,
  "moneda": "CLP",
  "referencia": "N° operación si existe, si no cadena vacía"
}}

Reglas de monto:
- Cargos / Compras / Débitos → monto NEGATIVO
- Abonos / Pagos / Créditos → monto POSITIVO
- Moneda: "CLP" para pesos, "USD" para dólares, "UF" para UF

Retorna SOLO un arreglo JSON válido, sin texto adicional antes o después.

Documento:
{texto}"""


def _extraer_con_llm(
    filepath: str,
    api_key: str,
    valor_usd_clp: float,
    valor_uf_clp: float,
) -> list[Movimiento]:
    """
    Extrae movimientos usando Claude API como fallback.

    Para PDF: extrae texto con pdfplumber.
    Para Excel/CSV: convierte a texto con pandas.
    confianza_extraccion = 0.8, fuente = "llm_extractor".
    """
    import anthropic  # type: ignore

    ext = Path(filepath).suffix.lower()
    fp = str(filepath).lower()

    # ── Convertir archivo a texto ─────────────────────────────────────────────
    if ext == ".pdf":
        try:
            import pdfplumber  # type: ignore
            with pdfplumber.open(filepath) as pdf:
                paginas = []
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    paginas.append(t)
            texto = "\n".join(paginas)
        except Exception:
            texto = ""
    else:
        try:
            if fp.endswith(".csv"):
                for sep in (",", ";", "\t"):
                    try:
                        df = pd.read_csv(filepath, sep=sep, dtype=str, nrows=200)
                        if len(df.columns) >= 2:
                            break
                    except Exception:
                        continue
                else:
                    df = pd.read_csv(filepath, dtype=str, nrows=200)
            else:
                df = pd.read_excel(filepath, dtype=str, nrows=200)
            texto = df.to_string(index=False, max_cols=20)
        except Exception:
            texto = ""

    if not texto.strip():
        return []

    # Truncar a ~12 000 caracteres para no exceder el contexto
    texto = texto[:12_000]

    # ── Llamar a Claude ───────────────────────────────────────────────────────
    cliente = anthropic.Anthropic(api_key=api_key)
    try:
        msg = cliente.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": _LLM_EXTRACTION_PROMPT.format(texto=texto),
            }],
        )
        respuesta = msg.content[0].text.strip()
    except Exception:
        return []

    # ── Parsear JSON ──────────────────────────────────────────────────────────
    try:
        data = json.loads(respuesta)
        if isinstance(data, dict):
            data = [data]
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", respuesta, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

    movimientos: list[Movimiento] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            monto = float(item.get("monto", 0))
            moneda = str(item.get("moneda", "CLP")).upper()
            if moneda == "USD":
                monto_clp = monto * valor_usd_clp
            elif moneda == "UF":
                monto_clp = monto * valor_uf_clp
            else:
                monto_clp = monto

            movimientos.append(Movimiento(
                fecha=str(item.get("fecha", "")),
                descripcion=str(item.get("descripcion", ""))[:200],
                monto=monto,
                moneda=moneda,
                monto_clp=monto_clp,
                fuente="llm_extractor",
                referencia=str(item.get("referencia", "")),
                confianza_extraccion=0.8,
                raw=json.dumps(item, ensure_ascii=False)[:500],
            ))
        except (ValueError, TypeError):
            continue

    return movimientos


# ── Extractor principal ───────────────────────────────────────────────────────

def extraer_movimientos(
    filepath: str,
    valor_usd_clp: float = 950.0,
    valor_uf_clp: float = 39700.0,
    anthropic_api_key: str = "",
) -> tuple[list[Movimiento], str]:
    """
    Detecta formato, extrae movimientos y normaliza montos a CLP.

    Lógica de fallback:
    1. Intentar extractor específico según formato detectado.
    2. Si resultado == [] o hubo excepción:
       - Si hay api_key → usar _extraer_con_llm() (fuente = "llm_extractor")
       - Si no hay api_key → retornar [] con formato "sin_movimientos"

    Args:
        filepath: Ruta al archivo de cartola.
        valor_usd_clp: Tipo de cambio USD/CLP.
        valor_uf_clp: Valor de la UF en CLP.
        anthropic_api_key: API key de Anthropic para fallback LLM.

    Returns:
        Tupla (movimientos, formato_detectado).
    """
    formato = detectar_formato(filepath)

    if formato == "desconocido":
        raise ValueError(
            f"Formato no soportado: {Path(filepath).suffix}. "
            "Soportados: PDF, XLSX, XLS, CSV."
        )

    _extractor_map = {
        "itau_cta_cte": "parser.extractors.itau_cta_cte",
        "itau_tc_nacional": "parser.extractors.itau_tc_nacional",
        "itau_tc_internacional": "parser.extractors.itau_tc_internacional",
        "generic_excel": "parser.extractors.generic_excel",
        "generic_pdf": "parser.extractors.generic_pdf",
    }

    movimientos: list[Movimiento] = []
    formato_usado = formato

    try:
        import importlib
        modulo = importlib.import_module(_extractor_map[formato])
        movimientos = modulo.extraer(filepath)
    except Exception:
        movimientos = []

    # Fallback LLM si extractor no devolvió nada
    if not movimientos:
        if anthropic_api_key:
            movimientos = _extraer_con_llm(
                filepath, anthropic_api_key, valor_usd_clp, valor_uf_clp
            )
            formato_usado = "llm_extractor" if movimientos else "sin_movimientos"
        else:
            formato_usado = "sin_movimientos"
        return movimientos, formato_usado

    # Normalizar monto_clp para extractores específicos
    for mov in movimientos:
        if mov.moneda == "USD":
            mov.monto_clp = mov.monto * valor_usd_clp
        elif mov.moneda == "UF":
            mov.monto_clp = mov.monto * valor_uf_clp
        elif mov.moneda == "CLP":
            mov.monto_clp = mov.monto
        # Otros: dejar monto_clp en 0 si FX desconocido

    return movimientos, formato_usado


def movimientos_a_dataframe(movimientos: list[Movimiento]) -> pd.DataFrame:
    """
    Convierte lista de movimientos a DataFrame para mostrar en UI.

    Columnas: fecha, descripcion, monto, moneda, monto_clp,
              fuente, confianza_extraccion
    """
    if not movimientos:
        return pd.DataFrame(columns=[
            "fecha", "descripcion", "monto", "moneda",
            "monto_clp", "fuente", "confianza_extraccion",
        ])

    return pd.DataFrame([
        {
            "fecha": m.fecha,
            "descripcion": m.descripcion,
            "monto": m.monto,
            "moneda": m.moneda,
            "monto_clp": m.monto_clp,
            "fuente": m.fuente,
            "referencia": m.referencia,
            "confianza_extraccion": m.confianza_extraccion,
        }
        for m in movimientos
    ])


def dataframe_a_movimientos(df: pd.DataFrame) -> list[Movimiento]:
    """
    Reconstruye lista de Movimiento desde un DataFrame editado en UI.
    """
    movimientos = []
    for _, fila in df.iterrows():
        movimientos.append(Movimiento(
            fecha=str(fila.get("fecha", "")),
            descripcion=str(fila.get("descripcion", "")),
            monto=float(fila.get("monto", 0)),
            moneda=str(fila.get("moneda", "CLP")),
            monto_clp=float(fila.get("monto_clp", 0)),
            fuente=str(fila.get("fuente", "editado")),
            referencia=str(fila.get("referencia", "")),
            confianza_extraccion=float(fila.get("confianza_extraccion", 1.0)),
            raw="",
        ))
    return movimientos
