# Personal Finance OS — CLAUDE.md

Archivo de contexto persistente para Claude Code.
Lee esto antes de cualquier tarea en este proyecto.

---

## Qué es este proyecto

Una aplicación de finanzas personales con arquitectura **BYOS (Bring Your Own Storage)**.
El motor financiero corre localmente en Python/Streamlit. Los datos viven exclusivamente
en el Google Drive del usuario — la app no tiene base de datos propia.

El diseño es una **calculadora financiera progresiva**, no una planilla:
- El usuario edita **parámetros** → el motor calcula todo lo demás.
- Ningún resultado calculado es editable directamente.
- Complejidad progresiva en 4 capas que se desbloquean a medida que el usuario avanza.

---

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Frontend / UI | Streamlit |
| Lógica financiera | Python (Pandas, NumPy) |
| Gráficos | Plotly |
| Persistencia | Google Drive API (`drive.file` scope) |
| Parser PDF | pdfplumber + camelot-py |
| Agente clasificador | Anthropic API (claude-sonnet-4-6) |
| Control de versiones | Git / GitHub |

**Regla de arquitectura:** el motor financiero (cálculos, parser, lógica ALM) debe estar
desacoplado del frontend. Toda la lógica vive en módulos Python puros — Streamlit solo
hace render. Esto permite migrar a FastAPI + Next.js en el futuro sin tocar el motor.

---

## Estructura del repositorio

```
/
├── CLAUDE.md                        ← este archivo
├── app.py                           ← entrada principal Streamlit
├── requirements.txt
├── credentials.json                 ← OAuth2 Google (NO commitear — está en .gitignore)
├── token.json                       ← token de sesión (NO commitear)
├── .gitignore
│
├── core/                            ← motor financiero puro (sin Streamlit)
│   ├── __init__.py
│   ├── drive.py                     ← leer/escribir CSVs en Google Drive
│   ├── state.py                     ← estado de sesión + lógica de "cambios sin guardar"
│   ├── calculator.py                ← cálculos: fondo reserva, posición de vida, margen libre
│   ├── schedule.py                  ← generación de tablas de desarrollo mensual
│   └── alm.py                       ← lógica ALM: calce de monedas, flujo neto, stress (Capa 4)
│
├── parser/                          ← agente parser de cartolas PDF (Capa 3)
│   ├── __init__.py
│   ├── pdf_extractor.py             ← extrae texto y tablas con pdfplumber / camelot
│   ├── llm_classifier.py            ← clasifica movimientos con Anthropic API
│   └── drive_inbox.py               ← monitorea /Inbox y mueve a /Procesados
│
├── pages/                           ← páginas Streamlit multi-page
│   ├── 01_onboarding.py
│   ├── 02_capa1_claridad.py
│   ├── 03_capa2_control.py
│   ├── 04_capa3_crecimiento.py
│   └── 05_capa4_pro.py
│
└── tests/
    ├── test_calculator.py
    ├── test_schedule.py
    └── test_parser.py
```

---

## Modelo de datos — archivos CSV en Google Drive

Todos los archivos viven en `/ALM_Data/` dentro del Drive del usuario.
La app accede a ellos mediante `drive.file` scope (solo archivos que ella misma crea).

### ALM_Posiciones_Balance.csv — registro maestro de parámetros

```
ID_Posicion, Descripcion, Clase, Moneda, Capa_Activacion, [parámetros específicos por tipo]
```

**Clases válidas:**
- `Ingreso_Recurrente` — ingresos fijos o variables
- `Gasto_Esencial` / `Gasto_Importante` / `Gasto_Aspiracion` — los 3 buckets
- `Activo_Liquido` — cuentas, fondos mutuos, efectivo
- `Activo_Financiero` — ETFs, acciones, renta fija
- `Activo_Real` — propiedades
- `Pasivo_Estructural` — hipotecarios
- `Pasivo_Corto_Plazo` — créditos, colegios, tarjetas
- `Objetivo_Ahorro` — metas con plazo y monto
- `Prevision_AFP` — saldo y proyección previsional

**Prefijos de ID por clase:**
```
ING_  →  ingresos
GAS_  →  gastos (bucket en el nombre: GAS_ESE_, GAS_IMP_, GAS_ASP_)
ACT_  →  activos
PAS_  →  pasivos
OBJ_  →  objetivos
AFP_  →  previsión
```

### Tablas_Desarrollo/Tabla_[ID_Posicion].csv — curva mensual por posición

```
ID_Posicion, Periodo (YYYY-MM), Saldo_Inicial, Flujo_Periodo,
Rendimiento_Costo, Amortizacion, Saldo_Final, Moneda, Tipo_Flujo, Notas
```

**Tipo_Flujo válidos:** `calculado` | `manual_recurrente` | `manual_puntual` | `importado`

**Regla crítica:** las filas con `Tipo_Flujo = calculado` son generadas por el motor
desde los parámetros. NUNCA se escriben manualmente ni se editan directamente.
Los flujos `manual_puntual` (ej: prepago hipotecario) se agregan encima y el motor
recalcula el saldo desde esa fecha en adelante.

### ALM_Flujos_Settlement.csv — flujos consolidados (generado, no editar)

Agregación de todas las tablas de desarrollo. Lo genera `core/alm.py` automáticamente.
El usuario nunca edita este archivo directamente.

### Objetivos/OBJ_[ID].csv — por objetivo de ahorro

```
ID_Objetivo, Nombre, Monto_Meta, Moneda, Plazo_Meses,
Saldo_Actual, Instrumento, Tasa_Esperada_Anual, Aporte_Mensual_Requerido
```

---

## Lógica de capas progresivas

Las capas se desbloquean secuencialmente. Cada una extiende el modelo sin romper lo anterior.

### Capa 1 — Claridad (siempre activa)
**Parámetros mínimos:** ingreso mensual + 3 buckets de gasto + activo líquido + meta fondo reserva

**Métricas calculadas:**
```python
margen_libre = ingreso - (esenciales + importantes + aspiraciones)
meta_fondo = esenciales * meses_meta          # default: 3
gap_fondo = meta_fondo - activo_liquido
meses_para_fondo = gap_fondo / margen_libre   # si > 0
posicion_vida_v1 = activo_liquido / esenciales
```

**Condición de desbloqueo Capa 2:**
`meta_fondo_definida == True AND buckets_confirmados == True`

### Capa 2 — Control
**Nuevos datos:** pasivos con tabla de desarrollo + saldo AFP

**Métricas adicionales:**
```python
carga_financiera = sum(cuotas_pasivos) / ingreso  # benchmark: < 0.35
posicion_vida_v2 = activo_liquido / (esenciales + sum(cuotas_pasivos))
```

**Generación automática de tablas:** cuando el usuario agrega un pasivo hipotecario,
`core/schedule.py` genera la tabla de amortización completa (método francés por defecto).

**Condición de desbloqueo Capa 3:**
`len(pasivos_con_tabla) >= 1 AND afp_saldo is not None`

### Capa 3 — Crecimiento
**Nuevos datos:** activos financieros + objetivos + parser cartolas disponible

**Condición de desbloqueo Capa 4:**
`len(activos_con_tabla) >= 1 AND len(objetivos_activos) >= 1`

### Capa 4 — Pro
ALM completo, stress testing, módulo EB2-NIW, exportación de reportes.

---

## Gestión de estado y guardado

```python
# core/state.py — patrón a seguir
st.session_state["dirty"] = False   # True cuando hay cambios sin guardar
st.session_state["positions"] = {}  # dict de parámetros por ID_Posicion
st.session_state["layer_unlocked"] = 1  # capa máxima desbloqueada
```

**Flujo de guardado:**
1. El usuario edita un parámetro → `st.session_state["dirty"] = True`
2. El indicador en el header cambia a `● cambios sin guardar`
3. El usuario hace clic en el botón **Guardar** → `core/drive.py` serializa y escribe los CSVs
4. El indicador vuelve a `✓ sincronizado`

**Al abrir la app:** `core/drive.py` lee todos los CSVs desde Drive y reconstruye
`st.session_state` completo antes de renderizar cualquier página.

---

## Generación de tablas de desarrollo — core/schedule.py

### Hipotecario (método francés)
```python
def gen_hipotecario(capital, tasa_anual, plazo_meses, fecha_inicio, moneda="CLP"):
    """
    Genera DataFrame con columnas estándar de tabla de desarrollo.
    Si moneda == "UF", el capital y cuota están en UF (el motor no convierte a CLP).
    """
```

### Parámetros de entrada por tipo de pasivo

| Tipo | Parámetros requeridos |
|---|---|
| Hipotecario | capital, tasa_anual, plazo_meses, fecha_inicio, moneda, metodo (frances/aleman) |
| Crédito consumo | monto, n_cuotas, tasa_anual, fecha_primer_pago |
| Colegio / cuotas | monto_anual, cuotas_por_ano, anos_restantes, meses_de_pago (lista) |
| Tarjeta crédito | deuda_total, pago_mensual, tasa_mensual |
| AFP | saldo_actual, aporte_mensual, tasa_anual, edad_actual, edad_jubilacion |
| Fondo mutuo / APV | saldo, aporte_mensual, tasa_anual, horizonte_meses |
| Objetivo ahorro | meta, plazo_meses, saldo_actual, tasa_anual |

---

## Parser de cartolas — parser/

**Flujo completo:**
1. `drive_inbox.py` detecta PDFs nuevos en `/ALM_Data/Inbox/`
2. `pdf_extractor.py` extrae DataFrame de movimientos (fecha, descripcion, monto)
3. `llm_classifier.py` llama a Anthropic API con el catálogo de posiciones como contexto
4. La UI muestra tabla editable: el usuario aprueba / corrige / descarta cada fila
5. Solo las filas aprobadas se escriben al CSV de flujos
6. El PDF se mueve a `/ALM_Data/Procesados/`

**Prompt base para clasificador (llm_classifier.py):**
```
Eres un clasificador financiero. Dado un movimiento bancario y el catálogo de
posiciones del usuario, propone la asignación más probable.
Responde SOLO en JSON: {"ID_Posicion": "...", "Tipo_Flujo": "importado",
"confianza": 0.97, "justificacion": "..."}
```

---

## Convenciones de código

- **Python 3.11+**
- **Tipos:** usar type hints en todas las funciones de `core/`
- **Docstrings:** Google style en funciones públicas
- **Tests:** pytest en `/tests/` — correr antes de cualquier commit a main
- **Formato:** black + isort (configurados en pyproject.toml)
- **Secrets:** `credentials.json` y `token.json` en `.gitignore` siempre
- **Constantes globales:** en `core/config.py` (monedas válidas, clases válidas, etc.)
- **Sin lógica de negocio en páginas Streamlit:** las páginas solo llaman funciones de `core/`

---

## Estado actual del proyecto (Marzo 2026)

| Fase | Capa | Estado |
|---|---|---|
| Fase 1 | Capa 1 | ✅ Base lista — auth Drive, CSVs de ejemplo, dashboard básico |
| Fase 2 | Capa 2-A | 🔄 En desarrollo — registro de pasivos + tablas de desarrollo |
| Fase 3 | Capa 2-B | 📋 Planificada — integración AFP |
| Fase 4 | Capa 3-A | 📋 Planificada — motor de objetivos |
| Fase 5 | Capa 3-B | 📋 Planificada — agente parser cartolas |
| Fase 6 | Capa 4 | 📋 Planificada — ALM completo |

---

## Lo que NO hacer

- **No editar `ALM_Flujos_Settlement.csv` manualmente** — es generado por `core/alm.py`
- **No agregar lógica financiera dentro de páginas Streamlit** — va en `core/`
- **No commitear `credentials.json` ni `token.json`** — están en `.gitignore`
- **No hacer campos de resultado editables** — solo parámetros son editables por el usuario
- **No asumir moneda CLP** — siempre respetar el campo `Moneda` de cada posición
- **No usar `drive.readonly` scope** — el scope correcto es `drive.file` desde Capa 1
