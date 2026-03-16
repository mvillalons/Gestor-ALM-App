"""Modelos de datos del parser de cartolas."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Movimiento:
    """Representa un movimiento bancario extraído de una cartola."""

    fecha: str                    # YYYY-MM-DD
    descripcion: str              # texto original del banco
    monto: float                  # positivo=ingreso, negativo=egreso
    moneda: str                   # CLP | USD | UF | otro
    monto_clp: float              # normalizado a CLP (0.0 si FX desconocido)
    fuente: str                   # nombre del extractor usado
    referencia: str = ""          # N° operación si existe
    confianza_extraccion: float = 1.0  # 1.0=extractor específico, 0.9=excel, 0.7=genérico
    raw: str = ""                 # línea original para debug


@dataclass
class PropuestaClasificacion:
    """Propuesta de clasificación de un movimiento generada por LLM."""

    movimiento: Movimiento
    id_posicion_sugerido: str   # ID de posición o "SIN_CLASIFICAR"
    confianza: float
    justificacion: str
    tipo_flujo: str = "importado"
    estado: str = "pendiente"   # pendiente | aprobado | descartado
