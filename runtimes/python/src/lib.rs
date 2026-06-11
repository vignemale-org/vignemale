//! Binding PyO3 du cœur Vignemale — le miroir Python de `runtimes/js` (NAPI) d'Encore.
//! Expose `vignemale-runtime-core` à Python pour qu'on puisse tester au fil de l'eau.

use std::sync::Arc;

use base64::Engine;
use prost::Message;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use vignemale_runtime_core::api;
use vignemale_runtime_core::config;
use vignemale_runtime_core::objects;
use vignemale_runtime_core::secrets;
use vignemale_runtime_core::sqldb;
use vignemale_runtime_core::vignemale::runtime::v1 as rt;

/// Runtime tokio partagé du binding (sqldb & co) — créé paresseusement.
fn shared_runtime() -> &'static tokio::runtime::Runtime {
    static RT: std::sync::OnceLock<tokio::runtime::Runtime> = std::sync::OnceLock::new();
    RT.get_or_init(|| tokio::runtime::Runtime::new().expect("tokio runtime"))
}

/// Version de la crate binding.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Helper de test : construit une `RuntimeConfig` de démo et la renvoie en base64
/// (le format que le core sait décoder depuis l'environnement).
#[pyfunction]
fn encode_demo_config(app_id: String, services: Vec<String>) -> String {
    let cfg = rt::RuntimeConfig {
        environment: Some(rt::Environment {
            app_id,
            ..Default::default()
        }),
        infra: None,
        deployment: Some(rt::Deployment {
            hosted_services: services
                .into_iter()
                .map(|name| rt::HostedService {
                    name,
                    worker_threads: None,
                    log_config: None,
                })
                .collect(),
            ..Default::default()
        }),
        vignemale_platform: None,
    };
    base64::engine::general_purpose::STANDARD.encode(cfg.encode_to_vec())
}

/// Résume une `RuntimeConfig` en dict Python `{app_id, hosted_services}`.
fn summarize(py: Python<'_>, cfg: &rt::RuntimeConfig) -> PyResult<PyObject> {
    let d = PyDict::new_bound(py);
    let app_id = cfg
        .environment
        .as_ref()
        .map(|e| e.app_id.clone())
        .unwrap_or_default();
    let services: Vec<String> = cfg
        .deployment
        .as_ref()
        .map(|dep| dep.hosted_services.iter().map(|s| s.name.clone()).collect())
        .unwrap_or_default();
    d.set_item("app_id", app_id)?;
    d.set_item("hosted_services", services)?;
    Ok(d.into())
}

/// Décode une `RuntimeConfig` base64 et renvoie son résumé.
#[pyfunction]
fn parse_runtime_config_b64(py: Python<'_>, b64: String) -> PyResult<PyObject> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let cfg = rt::RuntimeConfig::decode(&bytes[..])
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    summarize(py, &cfg)
}

/// Charge la `RuntimeConfig` depuis l'environnement via le core (`config`).
/// Renvoie `None` si aucune config n'est présente.
#[pyfunction]
fn load_config_from_env(py: Python<'_>) -> PyResult<Option<PyObject>> {
    match config::runtime_config_from_env() {
        Ok(cfg) => Ok(Some(summarize(py, &cfg)?)),
        Err(config::ParseError::EnvNotPresent) => Ok(None),
        Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string())),
    }
}

// --- secrets (module `secrets` du core) ---

fn resolve_to_bytes(py: Python<'_>, data: rt::SecretData) -> PyResult<Py<PyBytes>> {
    let mgr = secrets::Manager::new(vec![]);
    let bytes = mgr
        .load(data)
        .get()
        .map(|b| b.to_vec())
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new_bound(py, &bytes).unbind())
}

/// Résout un secret lu depuis une variable d'environnement.
#[pyfunction]
fn resolve_env_secret(py: Python<'_>, name: String) -> PyResult<Py<PyBytes>> {
    resolve_to_bytes(
        py,
        rt::SecretData {
            source: Some(rt::secret_data::Source::Env(name)),
            sub_path: None,
            encoding: rt::secret_data::Encoding::None as i32,
        },
    )
}

/// Résout un secret embarqué encodé en base64.
#[pyfunction]
fn resolve_b64_secret(py: Python<'_>, value_b64: String) -> PyResult<Py<PyBytes>> {
    resolve_to_bytes(
        py,
        rt::SecretData {
            source: Some(rt::secret_data::Source::Embedded(value_b64.into_bytes())),
            sub_path: None,
            encoding: rt::secret_data::Encoding::Base64 as i32,
        },
    )
}

/// Résout une sous-clé d'un secret JSON embarqué.
#[pyfunction]
fn resolve_json_key_secret(py: Python<'_>, json: String, key: String) -> PyResult<Py<PyBytes>> {
    resolve_to_bytes(
        py,
        rt::SecretData {
            source: Some(rt::secret_data::Source::Embedded(json.into_bytes())),
            sub_path: Some(rt::secret_data::SubPath::JsonKey(key)),
            encoding: rt::secret_data::Encoding::None as i32,
        },
    )
}

// --- objects (provider Object Storage / S3) ---

/// Test de bout en bout du provider S3 : crée le bucket (idempotent), écrit la
/// valeur sous `key`, puis la relit. Renvoie la valeur relue.
#[pyfunction]
#[pyo3(signature = (endpoint, region, access_key, secret_key, bucket, key, value))]
#[allow(clippy::too_many_arguments)]
fn s3_roundtrip(
    py: Python<'_>,
    endpoint: String,
    region: String,
    access_key: String,
    secret_key: String,
    bucket: String,
    key: String,
    value: Vec<u8>,
) -> PyResult<Py<PyBytes>> {
    let cluster = rt::BucketCluster {
        rid: "test-cluster".to_string(),
        buckets: vec![],
        provider: Some(rt::bucket_cluster::Provider::S3(rt::bucket_cluster::S3 {
            region,
            endpoint: Some(endpoint),
            access_key_id: Some(access_key),
            secret_access_key: Some(rt::SecretData {
                source: Some(rt::secret_data::Source::Embedded(secret_key.into_bytes())),
                sub_path: None,
                encoding: rt::secret_data::Encoding::None as i32,
            }),
        })),
    };
    let b = rt::Bucket {
        rid: "test-bucket".to_string(),
        vignemale_name: bucket.clone(),
        cloud_name: bucket,
        key_prefix: None,
        public_base_url: None,
    };
    let handle = objects::bucket_from_cluster(&cluster, &b)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let runtime = tokio::runtime::Runtime::new()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let result = py
        .allow_threads(|| {
            runtime.block_on(async {
                handle.create_if_not_exists().await?;
                handle.put(&key, value).await?;
                handle.get(&key).await
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(PyBytes::new_bound(py, &result).unbind())
}

// --- sqldb (Postgres) : requêtes via le pool du core, params/lignes en JSON ---

fn parse_sql_params(params_json: &str) -> PyResult<Vec<sqldb::SqlParam>> {
    let values: Vec<serde_json::Value> = serde_json::from_str(params_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("params invalides: {e}")))?;
    Ok(values.into_iter().map(sqldb::SqlParam::from_json).collect())
}

/// Exécute une requête SELECT et renvoie les lignes (JSON : tableau d'objets).
#[pyfunction]
fn sqldb_query(py: Python<'_>, dsn: String, sql: String, params_json: String) -> PyResult<String> {
    let params = parse_sql_params(&params_json)?;
    py.allow_threads(|| {
        shared_runtime().block_on(async {
            let pool = sqldb::pool_for_dsn(&dsn)?;
            sqldb::query(&pool, &sql, params).await
        })
    })
    .map(|rows| rows.to_string())
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

/// Exécute une commande (INSERT/UPDATE/DELETE/DDL), renvoie les lignes affectées.
#[pyfunction]
fn sqldb_execute(
    py: Python<'_>,
    dsn: String,
    sql: String,
    params_json: String,
) -> PyResult<u64> {
    let params = parse_sql_params(&params_json)?;
    py.allow_threads(|| {
        shared_runtime().block_on(async {
            let pool = sqldb::pool_for_dsn(&dsn)?;
            sqldb::execute(&pool, &sql, params).await
        })
    })
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

// --- api (serveur HTTP) : le binding implémente le trait `Handler` du core
//     en appelant le handler Python (avec le GIL), façon `runtimes/js` d'Encore. ---

/// Formate l'exception Python (traceback complet) pour les logs structurés.
fn format_py_err(py: Python<'_>, e: &PyErr) -> String {
    let fallback = || e.to_string();
    let Ok(tb_mod) = py.import_bound("traceback") else {
        return fallback();
    };
    tb_mod
        .call_method1(
            "format_exception",
            (e.get_type_bound(py), e.value_bound(py), e.traceback_bound(py)),
        )
        .and_then(|lines| lines.extract::<Vec<String>>())
        .map(|lines| lines.join(""))
        .unwrap_or_else(|_| fallback())
}

struct PyHandler {
    func: Py<PyAny>,
}

impl api::Handler for PyHandler {
    fn call(&self, req: api::Request) -> api::Response {
        let request_id = req.request_id.clone();
        Python::with_gil(|py| match call_py_handler(py, &self.func, req) {
            Ok(body) => api::Response { status: 200, body },
            Err(e) => http_error_response(py, &e).unwrap_or_else(|| {
                if e.is_instance_of::<pyo3::exceptions::PyValueError>(py)
                    && e.to_string().contains("corps JSON invalide")
                {
                    return api::Response {
                        status: 400,
                        body: api::error_json("invalid_argument", "corps JSON invalide", None),
                    };
                }
                tracing::error!(
                    target: "vignemale::app",
                    request_id = %request_id,
                    traceback = %format_py_err(py, &e),
                    "exception non gérée dans le handler"
                );
                api::Response {
                    status: 500,
                    body: api::error_json(
                        "internal",
                        "internal error",
                        Some(serde_json::json!({"request_id": request_id})),
                    ),
                }
            }),
        })
    }
}

/// Si l'exception est un `HTTPError` du SDK (attributs `vignemale_status` /
/// `vignemale_body`), construit la réponse HTTP correspondante.
fn http_error_response(py: Python<'_>, e: &PyErr) -> Option<api::Response> {
    let val = e.value_bound(py);
    let status: u16 = val.getattr("vignemale_status").ok()?.extract().ok()?;
    let body: String = val.getattr("vignemale_body").ok()?.extract().ok()?;
    Some(api::Response {
        status,
        body: body.into_bytes(),
    })
}

/// Construit les kwargs communs : params de chemin, `query` (dict), `headers`
/// (dict, noms en minuscules) et `body` (JSON parsé) si présent. Le SDK filtre
/// ensuite selon la signature du handler.
fn build_kwargs<'py>(
    py: Python<'py>,
    req: &api::Request,
) -> PyResult<pyo3::Bound<'py, PyDict>> {
    let kwargs = PyDict::new_bound(py);
    for (k, v) in &req.params {
        kwargs.set_item(k, v)?;
    }
    let query = PyDict::new_bound(py);
    for (k, v) in &req.query {
        query.set_item(k, v)?;
    }
    kwargs.set_item("query", query)?;
    let headers = PyDict::new_bound(py);
    for (k, v) in &req.headers {
        headers.set_item(k, v)?;
    }
    kwargs.set_item("headers", headers)?;
    if !req.body.is_empty() {
        let invalid =
            || pyo3::exceptions::PyValueError::new_err("corps JSON invalide");
        let body_str = std::str::from_utf8(&req.body).map_err(|_| invalid())?;
        let parsed = py
            .import_bound("json")?
            .call_method1("loads", (body_str,))
            .map_err(|_| invalid())?;
        kwargs.set_item("body", parsed)?;
    }
    Ok(kwargs)
}

/// Appelle le handler Python puis sérialise le retour en JSON.
fn call_py_handler(py: Python<'_>, func: &Py<PyAny>, req: api::Request) -> PyResult<Vec<u8>> {
    let kwargs = build_kwargs(py, &req)?;
    let result = func.bind(py).call((), Some(&kwargs))?;
    let dumped: String = py
        .import_bound("json")?
        .call_method1("dumps", (result,))?
        .extract()?;
    Ok(dumped.into_bytes())
}

// --- streaming (SSE) : le binding implémente le trait `StreamHandler` du core ---

#[pyclass]
struct PyStreamSink {
    sink: api::StreamSink,
}

#[pymethods]
impl PyStreamSink {
    /// Pousse un fragment dans le flux SSE.
    fn write(&self, py: Python<'_>, chunk: String) -> bool {
        py.allow_threads(|| self.sink.write(chunk))
    }
}

struct PyStreamHandler {
    func: Py<PyAny>,
}

impl api::StreamHandler for PyStreamHandler {
    fn call(&self, req: api::Request, sink: api::StreamSink) {
        let request_id = req.request_id.clone();
        Python::with_gil(|py| {
            if let Err(e) = call_py_stream_handler(py, &self.func, req, sink) {
                tracing::error!(
                    target: "vignemale::app",
                    request_id = %request_id,
                    traceback = %format_py_err(py, &e),
                    "exception non gérée dans le handler streaming"
                );
            }
        });
    }
}

fn call_py_stream_handler(
    py: Python<'_>,
    func: &Py<PyAny>,
    req: api::Request,
    sink: api::StreamSink,
) -> PyResult<()> {
    let kwargs = build_kwargs(py, &req)?;
    let py_sink = Py::new(py, PyStreamSink { sink })?;
    kwargs.set_item("stream", py_sink)?;
    func.bind(py).call((), Some(&kwargs))?;
    Ok(())
}

/// Démarre le serveur HTTP avec les endpoints donnés (bloque jusqu'à l'arrêt).
/// `endpoints` = liste de (name, method, path, handler, stream).
#[pyfunction]
fn serve(
    py: Python<'_>,
    endpoints: Vec<(String, String, String, Py<PyAny>, bool)>,
    addr: String,
) -> PyResult<()> {
    let socket: std::net::SocketAddr = addr
        .parse()
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("adresse invalide: {e}")))?;
    let mut mgr = api::Manager::new();
    for (name, method, path, func, stream) in endpoints {
        let kind = if stream {
            api::HandlerKind::Stream(Arc::new(PyStreamHandler { func }))
        } else {
            api::HandlerKind::Unary(Arc::new(PyHandler { func }))
        };
        mgr.register(api::Endpoint { name, method, path }, kind);
    }
    // Le serveur tourne sur un thread dédié (qui ne tient pas le GIL) ; le thread
    // principal relâche le GIL et attend, pour que les handlers puissent l'acquérir.
    let server_thread = std::thread::spawn(move || -> Result<(), String> {
        let runtime = tokio::runtime::Runtime::new().map_err(|e| e.to_string())?;
        runtime.block_on(mgr.serve(socket)).map_err(|e| e.to_string())
    });
    // Attente par tranches (pas un `join` bloquant) : entre deux tranches on
    // repasse par `check_signals`, sinon Ctrl-C ne lèverait jamais
    // KeyboardInterrupt — le signal serait noté par CPython mais l'interpréteur
    // ne reprendrait jamais la main.
    loop {
        if server_thread.is_finished() {
            let outcome = match server_thread.join() {
                Ok(r) => r,
                Err(_) => Err("server thread panicked".to_string()),
            };
            return outcome.map_err(pyo3::exceptions::PyRuntimeError::new_err);
        }
        py.allow_threads(|| std::thread::sleep(std::time::Duration::from_millis(100)));
        py.check_signals()?;
    }
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(encode_demo_config, m)?)?;
    m.add_function(wrap_pyfunction!(parse_runtime_config_b64, m)?)?;
    m.add_function(wrap_pyfunction!(load_config_from_env, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_env_secret, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_b64_secret, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_json_key_secret, m)?)?;
    m.add_function(wrap_pyfunction!(s3_roundtrip, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_query, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_execute, m)?)?;
    m.add_function(wrap_pyfunction!(serve, m)?)?;
    Ok(())
}
