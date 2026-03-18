"""
Clasificador de movimientos bancarios usando Claude API.
"""
from __future__ import annotations
import json
import re

from parser.models import Movimiento, PropuestaClasificacion

SYSTEM_PROMPT = """\
Eres un asistente financiero personal especializado en clasificar \
movimientos bancarios chilenos.

Tu tarea es asignar cada movimiento a una posición del portafolio \
financiero del usuario. Las posiciones disponibles se incluyen en \
cada solicitud.

Reglas:
1. Responde SOLO con JSON válido, sin texto adicional.
2. Si no puedes clasificar con confianza >= 0.5, usa \
   id_posicion_sugerido = "SIN_CLASIFICAR".
3. El tipo_flujo siempre es "importado".
4. La justificacion debe ser breve (máximo 1 oración).

Formato de respuesta para cada movimiento:
{
  "id": "<id del movimiento>",
  "id_posicion_sugerido": "<ID_POSICION o SIN_CLASIFICAR>",
  "confianza": <0.0 a 1.0>,
  "justificacion": "<breve explicación>",
  "tipo_flujo": "importado"
}
"""


def _construir_catalogo(posiciones: dict) -> str:
    """Construye texto del catálogo de posiciones para el prompt."""
    lineas = ["Posiciones disponibles:"]
    for pid, params in posiciones.items():
        clase = str(params.get("Clase", "") or "")
        if clase in ("nan", "None"):
            clase = ""
        desc_raw = params.get("Descripcion", pid)
        desc = str(desc_raw if desc_raw is not None else pid)
        if desc in ("nan", "None", ""):
            desc = pid
        tipo_raw = params.get("Tipo_Pasivo", params.get("Tipo", ""))
        tipo = str(tipo_raw or "")
        if tipo in ("nan", "None"):
            tipo = ""
        lineas.append(f"  - {pid}: {desc} ({clase}{', ' + tipo if tipo else ''})")
    return "\n".join(lineas)


def _parsear_respuesta(
    texto: str,
    movimientos_batch: list[Movimiento],
    indices_batch: list[int],
) -> list[tuple[int, PropuestaClasificacion]]:
    """Parsea la respuesta JSON del LLM."""
    resultados = []

    # Intentar parsear como lista o como objetos individuales
    try:
        data = json.loads(texto)
        if isinstance(data, dict):
            data = [data]
    except json.JSONDecodeError:
        # Intentar extraer JSON con regex
        matches = re.findall(r"\{[^{}]+\}", texto, re.DOTALL)
        data = []
        for m in matches:
            try:
                data.append(json.loads(m))
            except json.JSONDecodeError:
                continue

    for item in data:
        # Buscar el índice del movimiento por "id"
        item_id = str(item.get("id", ""))
        idx_mov = None
        for i, orig_idx in enumerate(indices_batch):
            if str(orig_idx) == item_id or str(i) == item_id:
                idx_mov = (i, orig_idx)
                break

        if idx_mov is None:
            continue

        i, orig_idx = idx_mov
        mov = movimientos_batch[i]

        confianza = float(item.get("confianza", 0.0))
        id_pos = item.get("id_posicion_sugerido", "SIN_CLASIFICAR")
        if confianza < 0.5:
            id_pos = "SIN_CLASIFICAR"

        resultados.append((orig_idx, PropuestaClasificacion(
            movimiento=mov,
            id_posicion_sugerido=id_pos,
            confianza=confianza,
            justificacion=item.get("justificacion", ""),
            tipo_flujo="importado",
            estado="pendiente",
        )))

    return resultados


def clasificar_movimientos(
    movimientos: list[Movimiento],
    posiciones: dict,
    anthropic_api_key: str,
    batch_size: int = 10,
) -> list[PropuestaClasificacion]:
    """
    Clasifica movimientos usando Claude API en batches.

    Args:
        movimientos: Lista de movimientos a clasificar.
        posiciones: Dict de posiciones del portafolio.
        anthropic_api_key: API key de Anthropic.
        batch_size: Movimientos por llamada a la API.

    Returns:
        Lista de PropuestaClasificacion, una por movimiento.
        Si la API falla, retorna SIN_CLASIFICAR para todos.
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "anthropic no está instalado. Instala con: pip install anthropic"
        )

    client = anthropic.Anthropic(api_key=anthropic_api_key)
    catalogo = _construir_catalogo(posiciones)

    # Inicializar todas como SIN_CLASIFICAR
    resultados: list[PropuestaClasificacion | None] = [None] * len(movimientos)

    for batch_start in range(0, len(movimientos), batch_size):
        batch = movimientos[batch_start : batch_start + batch_size]
        indices = list(range(batch_start, batch_start + len(batch)))

        # Construir lista de movimientos para el prompt
        movs_texto = []
        for i, mov in enumerate(batch):
            signo = "+" if mov.monto >= 0 else ""
            movs_texto.append(
                f'  {{"id": "{batch_start + i}", "fecha": "{mov.fecha}", '
                f'"descripcion": "{mov.descripcion}", '
                f'"monto": "{signo}{mov.monto:,.0f} {mov.moneda}"}}'
            )

        user_prompt = (
            f"{catalogo}\n\n"
            f"Clasifica estos {len(batch)} movimientos:\n"
            f"[\n{chr(10).join(movs_texto)}\n]\n\n"
            f"Responde con un array JSON con una clasificación por movimiento."
        )

        try:
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            texto_respuesta = response.content[0].text
            clasificados = _parsear_respuesta(texto_respuesta, batch, indices)

            for orig_idx, propuesta in clasificados:
                resultados[orig_idx] = propuesta

        except Exception:
            # En caso de error, dejar como SIN_CLASIFICAR
            pass

    # Rellenar los que no se clasificaron
    for i, mov in enumerate(movimientos):
        if resultados[i] is None:
            resultados[i] = PropuestaClasificacion(
                movimiento=mov,
                id_posicion_sugerido="SIN_CLASIFICAR",
                confianza=0.0,
                justificacion="Error en clasificación automática",
                tipo_flujo="importado",
                estado="pendiente",
            )

    return resultados  # type: ignore[return-value]
