# Pre-entrega 7: API de producción y monitoreo activo

Curso AI Engineering (Coderhouse), módulo 7.

Una API REST con FastAPI que expone el orquestador multi-agente del módulo 6 (Supervisor, Agente de Investigación y Agente de Análisis). La API recibe la tarea, la encola y devuelve un `job_id` sin esperar; un worker la ejecuta en segundo plano. El estado de los trabajos y los checkpoints de LangGraph se guardan en Redis, cada ejecución envía sus trazas a Arize Phoenix, y las tareas críticas se pausan hasta que una persona las aprueba.

## Estructura

```
├── app/
│   ├── main.py            # FastAPI: POST /tasks, GET /tasks/{id}, POST /tasks/{id}/approve, GET /health
│   ├── graph.py           # orquestador del módulo 6 + checkpointer de Redis (AsyncRedisSaver)
│   ├── worker.py          # cola y workers asíncronos; estado del job en Redis (PENDING/RUNNING/WAITING_APPROVAL/DONE/FAILED)
│   ├── observability.py   # Arize Phoenix + OpenInference, decorador de trazas y métricas de cada llamada al modelo
│   ├── hitl.py            # nodo de aprobación humana con interrupt
│   ├── state.py, llm.py, knowledge.py, agents/   # orquestador del módulo 6
├── data/                  # documentación técnica que consulta el Agente de Investigación
├── scripts/load_test.py   # prueba de carga: 5 peticiones concurrentes
├── screenshots/           # capturas del dashboard de Phoenix y resultado de la prueba de carga
├── docker-compose.yml     # Redis Stack + Phoenix (+ la API, opcional)
├── Dockerfile
├── requirements.txt
├── .env.example
└── test_main.py
```

## Cómo levantar el entorno

Requiere Python 3.12 o superior y Docker.

### 1. Redis y Phoenix

```bash
git clone https://github.com/mgrandinelli/coder_ia_engineering_pre_entrega_7.git
cd coder_ia_engineering_pre_entrega_7

docker compose up -d redis phoenix
```

- **Redis Stack** queda en `localhost:6380`. Se usa Redis Stack y no Redis común porque el checkpointer de LangGraph para Redis necesita los módulos RedisJSON y RediSearch.
- **Phoenix** queda en http://localhost:6006: ahí se ven las trazas y las métricas.

### 2. La API

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # y completar la clave del proveedor
uvicorn app.main:app --port 8000
```

La documentación interactiva de la API queda en http://localhost:8000/docs. También se puede levantar todo con Docker: `docker compose --profile api up -d`.

### Variables de entorno

| Variable | Por defecto | Qué es |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6380` | Conexión a Redis Stack |
| `LLM_PROVIDER` | `openai` | `openai` o `anthropic` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | | Clave del proveedor. Sin clave, la API corre en modo demostración |
| `OPENAI_MODEL` / `ANTHROPIC_MODEL` | `gpt-4o-mini` / `claude-haiku-4-5` | Modelo a usar |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://localhost:6006/v1/traces` | Adónde se envían las trazas |
| `PHOENIX_PROJECT_NAME` | `atlas-orquestador` | Proyecto de Phoenix |
| `WORKER_CONCURRENCY` | `5` | Trabajos que se ejecutan a la vez |

Las claves se leen del archivo `.env`, que está en `.gitignore`. Al repositorio solo se sube `.env.example`, sin valores.

## Uso de la API

```bash
# 1. Encolar una tarea: responde enseguida con el job_id (202 Accepted)
curl -X POST localhost:8000/tasks -H "Content-Type: application/json" \
     -d '{"solicitud": "¿Cuántos minutos de caída por mes permite el objetivo de disponibilidad de Atlas?"}'
# {"job_id": "93b2bc57...", "status": "PENDING"}

# 2. Consultar el estado
curl localhost:8000/tasks/93b2bc57...

# 3. Si el estado es WAITING_APPROVAL, aprobar o rechazar
curl -X POST localhost:8000/tasks/93b2bc57.../approve -H "Content-Type: application/json" \
     -d '{"aprobado": true, "comentario": "Revisado"}'
```

| Endpoint | Qué hace |
|---|---|
| `POST /tasks` | Registra el trabajo en Redis como `PENDING`, lo encola y devuelve el `job_id`. No espera a que termine. |
| `GET /tasks/{job_id}` | Estado del trabajo, resultado, aprobación pendiente, métricas o error. |
| `POST /tasks/{job_id}/approve` | Decisión del operador sobre una tarea crítica. Reanuda el grafo desde donde se pausó. Devuelve 409 si el trabajo no espera aprobación. |
| `GET /health` | Verifica la conexión con Redis. |

## Arquitectura y decisiones de diseño

### Orquestación asíncrona (`app/main.py`, `app/worker.py`)

- **Los endpoints no bloquean el event loop.** Todos son `async` y usan el cliente asíncrono de Redis (`redis.asyncio`). `POST /tasks` solo escribe el trabajo en Redis y lo pone en una cola: el grafo nunca corre dentro del request.
- **Patrón worker.** Al arrancar, la API lanza 5 tareas asíncronas que consumen una cola `asyncio.Queue`. Cada una toma un trabajo, ejecuta el grafo y actualiza su estado en Redis. Cinco trabajos corren a la vez sin bloquearse entre sí.
- **Ciclo de vida de un trabajo:** `PENDING` → `RUNNING` → (`WAITING_APPROVAL` → `RUNNING`) → `DONE` o `FAILED`.
- **Errores en segundo plano.** Toda excepción del grafo se captura en el worker y el trabajo pasa a `FAILED` con el error, así el cliente que consulta el estado no queda esperando indefinidamente.

### Persistencia con Redis

Redis guarda dos cosas:

- **El estado de cada trabajo**, en un hash `job:<id>` con estado, solicitud, resultado, aprobación pendiente, métricas y error. Se conserva una semana.
- **Los checkpoints de LangGraph**, con `AsyncRedisSaver`. Cada trabajo es un hilo del grafo (`thread_id = job_id`). Cuando el grafo se pausa para la aprobación humana, su estado queda en Redis, y la ejecución se retoma desde ahí aunque la API se haya reiniciado en el medio.

### Human-in-the-loop (`app/hitl.py`)

Antes de la síntesis final, toda tarea pasa por el nodo `aprobacion_humana`:

- Si la solicitud pide una **acción con efectos secundarios** (notificar a un canal del equipo o suspender despliegues), la tarea es crítica. El nodo llama a `interrupt` de LangGraph: el grafo se detiene, el trabajo pasa a `WAITING_APPROVAL` y la respuesta de `GET /tasks/{id}` muestra el motivo y el resultado parcial.
- `POST /tasks/{id}/approve` reanuda el grafo con la decisión. Si se aprueba, se ejecuta la acción; si se rechaza, no se ejecuta y la respuesta final lo dice.
- Si la tarea no es crítica, el nodo no hace nada y el flujo sigue.

La pausa no ocupa un worker: el estado queda guardado en Redis y el worker queda libre para otros trabajos.

### Observabilidad (`app/observability.py`)

- **Trazas en Arize Phoenix con OpenInference.** `LangChainInstrumentor` genera un span por cada nodo del grafo, cada llamada al modelo (con sus tokens) y cada herramienta. Cada ejecución de un trabajo queda agrupada bajo un span `job` con el `job_id` como atributo, mediante el decorador `@traced`.
- **Costo por ejecución.** Phoenix calcula el costo a partir de los tokens de entrada y salida de cada llamada al modelo y del precio del modelo.
- **Métricas en el log.** `MetricsCallback` registra la latencia y los tokens de cada llamada al modelo, y el total de cada trabajo queda en el campo `metricas` del job.
- **Sin ruido.** Las consultas de estado (`GET /tasks/{id}`) y `/health` no generan trazas: son sondeos repetitivos que taparían las ejecuciones en el dashboard.

### Modelo y `temperature`

El orquestador crea `ChatOpenAI` y `ChatAnthropic` con `temperature=0`. El modelo por defecto de Anthropic es `claude-haiku-4-5` porque acepta ese parámetro; los modelos Claude más recientes (por ejemplo `claude-opus-5-5`) ya no lo usan y lo rechazan con un error 400.

### Modo demostración

Sin clave del proveedor, los agentes usan modelos guionados en lugar de un LLM real. La API, Redis, los workers, la aprobación humana y las trazas funcionan igual; lo único simulado son las respuestas de los modelos.

## Prueba de carga: 5 peticiones concurrentes

```bash
python scripts/load_test.py
```

El script envía 5 peticiones a la vez. Tres de ellas piden notificar el resultado a un canal, así que son críticas: el script hace de operador y las aprueba un segundo después de que quedan en espera. Mide la latencia de punta a punta de cada trabajo, calcula p50 y p95, el costo de cada ejecución, y guarda el resultado en `screenshots/carga.json`.

Resultado:

```
job_id                            estado  crítica   latencia (s)  tokens  costo (USD)
cbb83753d42240a8a228928d9730b0f2  DONE    sí               2.664    4732     0.000965
19ef6727c71c4b5eb8b2d4730ec71a70  DONE    no               1.406    4667     0.000955
f92ecc9bb6e144929ad30189502d45ca  DONE    sí               2.617    4732     0.000965
6aee399347db4aafbd8a86c9569391cf  DONE    no               1.341    4667     0.000955
4a660b38c41c40adb71a7385e8390d57  DONE    sí               2.615    4732     0.000965

5 peticiones concurrentes en 2.665 s | p50 2.615 s | p95 2.655 s | tokens 23530 | costo medio por ejecución 0.000961 USD
```

La latencia de punta a punta de las tareas críticas incluye el segundo que tarda el operador en aprobar. En Phoenix, la latencia de cada traza mide solo la ejecución del grafo.

## Capturas del dashboard

Están en `screenshots/`, tomadas de Phoenix después de la prueba de carga:

| Archivo | Qué muestra |
|---|---|
| `01_trazas.png` | Lista de trazas del proyecto, con el costo total y la latencia de cada una: 8 ejecuciones de trabajos (5 iniciales y 3 reanudaciones tras la aprobación) y los requests a la API |
| `02_detalle_traza.png` | Una ejecución completa con su costo y latencia: supervisor, research_agent con su llamada a `buscar_documentacion`, analyst_agent, y los tokens de cada llamada al modelo |
| `03_metricas.png` | Panel de métricas: volumen de trazas, latencia por percentiles, costo y modelos por costo |
| `04_latencia_p95.png` | Latencia por percentiles de las trazas: p95 de 1,26 s |
| `05_costo.png` | Costo por tipo de token (entrada y salida) del modelo `gpt-4o-mini` |
| `carga.json` | Resultado de la prueba de carga: latencia y costo de cada ejecución |

Costo estimado a partir de los tokens de las respuestas de prueba, con el precio de `gpt-4o-mini`; con una clave real, `python scripts/load_test.py` lo reemplaza por el medido.

## Tests

`test_main.py` tiene 19 tests. No usan la red, claves, Redis ni Phoenix reales: Redis se reemplaza por `fakeredis`, el checkpointer por uno en memoria y los modelos por guiones.

```bash
pytest -v
```

- **API asíncrona:** `POST /tasks` responde en menos de medio segundo aunque el grafo tarde; una tarea termina en `DONE` con resultado y métricas; 5 tareas concurrentes terminan todas; 404 y 422 para entradas inválidas.
- **Redis:** el estado del trabajo se guarda y se recupera con su vencimiento; si el grafo falla, el trabajo queda en `FAILED` con el error; una salida inesperada del modelo también termina en `FAILED`.
- **Human-in-the-loop:** qué solicitudes son críticas; una tarea crítica se pausa y continúa al aprobarla; si se rechaza, la acción no se ejecuta; no se puede aprobar una tarea que no espera aprobación; el grafo pasa por la aprobación antes de cerrar.
- **Observabilidad:** `MetricsCallback` registra latencia y tokens; la instrumentación se puede desactivar.

## Reporte de tests

Salida de `pytest -v`:

```
============================= test session starts ==============================
collecting ... collected 19 items

test_main.py::test_post_encola_y_devuelve_job_id_sin_esperar_al_grafo PASSED [  5%]
test_main.py::test_una_tarea_comun_termina_en_done_con_resultado_y_metricas PASSED [ 10%]
test_main.py::test_cinco_peticiones_concurrentes_terminan_todas PASSED   [ 15%]
test_main.py::test_trabajo_inexistente_devuelve_404 PASSED               [ 21%]
test_main.py::test_solicitud_vacia_se_rechaza PASSED                     [ 26%]
test_main.py::test_job_store_guarda_y_recupera_el_estado PASSED          [ 31%]
test_main.py::test_si_el_agente_falla_el_trabajo_queda_en_failed PASSED  [ 36%]
test_main.py::test_una_salida_inesperada_del_modelo_termina_en_failed PASSED [ 42%]
test_main.py::test_evaluar_criticidad[Seg\xfan la documentaci\xf3n de Atlas, \xbfcu\xe1ntos minutos de ca\xedda por mes permite el objetivo de disponibilidad? Si en lo que va del mes hubo 12 minutos de ca\xedda, \xbfqu\xe9 porcentaje del presupuesto de error se consumi\xf3 y corresponde suspender los despliegues?-False] PASSED [ 47%]
test_main.py::test_evaluar_criticidad[Seg\xfan la documentaci\xf3n de Atlas, \xbfcu\xe1ntos minutos de ca\xedda por mes permite el objetivo de disponibilidad? Si en lo que va del mes hubo 12 minutos de ca\xedda, \xbfqu\xe9 porcentaje del presupuesto de error se consumi\xf3 y corresponde suspender los despliegues? Notificar el resultado al canal #atlas-despliegues.-True] PASSED [ 52%]
test_main.py::test_evaluar_criticidad[Suspender los despliegues.-True] PASSED [ 57%]
test_main.py::test_evaluar_criticidad[\xbfCorresponde suspender los despliegues?-False] PASSED [ 63%]
test_main.py::test_una_tarea_critica_se_pausa_hasta_la_aprobacion PASSED [ 68%]
test_main.py::test_si_el_operador_rechaza_no_se_ejecuta_la_accion PASSED [ 73%]
test_main.py::test_no_se_puede_aprobar_una_tarea_que_no_espera_aprobacion PASSED [ 78%]
test_main.py::test_el_grafo_pasa_por_la_aprobacion_humana_antes_de_cerrar PASSED [ 84%]
test_main.py::test_metrics_callback_registra_latencia_y_tokens PASSED    [ 89%]
test_main.py::test_sin_phoenix_la_instrumentacion_queda_desactivada PASSED [ 94%]
test_main.py::test_las_claves_no_estan_en_el_repositorio PASSED          [100%]

============================== 19 passed in 1.96s ==============================
```
