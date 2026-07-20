//! PyO3 binding for the Vignemale core — the Python mirror of Encore's `runtimes/js` (NAPI).
//! Exposes `vignemale-runtime-core` to Python so we can test as we go.

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

/// Shared tokio runtime for the binding (sqldb & co) — created lazily.
fn shared_runtime() -> &'static tokio::runtime::Runtime {
    static RT: std::sync::OnceLock<tokio::runtime::Runtime> = std::sync::OnceLock::new();
    RT.get_or_init(|| tokio::runtime::Runtime::new().expect("tokio runtime"))
}

/// Version of the binding crate.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Test helper: builds a demo `RuntimeConfig` and returns it in base64
/// (the format the core can decode from the environment).
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

/// Summarizes a `RuntimeConfig` into a Python dict `{app_id, hosted_services}`.
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

/// Decodes a base64 `RuntimeConfig` and returns its summary.
#[pyfunction]
fn parse_runtime_config_b64(py: Python<'_>, b64: String) -> PyResult<PyObject> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let cfg = rt::RuntimeConfig::decode(&bytes[..])
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    summarize(py, &cfg)
}

/// Loads the `RuntimeConfig` from the environment via the core (`config`).
/// Returns `None` if no config is present.
#[pyfunction]
fn load_config_from_env(py: Python<'_>) -> PyResult<Option<PyObject>> {
    match config::runtime_config_from_env() {
        Ok(cfg) => Ok(Some(summarize(py, &cfg)?)),
        Err(config::ParseError::EnvNotPresent) => Ok(None),
        Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string())),
    }
}

// --- secrets (the core's `secrets` module) ---

fn resolve_to_bytes(py: Python<'_>, data: rt::SecretData) -> PyResult<Py<PyBytes>> {
    let mgr = secrets::Manager::new(vec![]);
    let bytes = mgr
        .load(data)
        .get()
        .map(|b| b.to_vec())
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new_bound(py, &bytes).unbind())
}

/// Resolves a secret read from an environment variable.
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

/// Resolves an embedded secret encoded in base64.
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

/// Resolves a sub-key of an embedded JSON secret.
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

/// End-to-end test of the S3 provider: creates the bucket (idempotent), writes
/// the value under `key`, then reads it back. Returns the value read back.
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

// --- objects (Bucket): S3 operations via the core, config resolved by the SDK ---

#[allow(clippy::too_many_arguments)]
fn bucket_handle(
    endpoint: String,
    region: String,
    access_key: String,
    secret_key: String,
    cloud_name: String,
) -> anyhow::Result<objects::Bucket> {
    let cluster = rt::BucketCluster {
        rid: "vignemale".to_string(),
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
        rid: "vignemale".to_string(),
        vignemale_name: cloud_name.clone(),
        cloud_name,
        key_prefix: None,
        public_base_url: None,
    };
    objects::bucket_from_cluster(&cluster, &b)
}

/// A bucket operation: `op` ∈ {create, put, get, exists, list, delete}.
/// `cfg` = (endpoint, region, access_key, secret_key, cloud_name).
#[pyfunction]
#[pyo3(signature = (cfg, op, key=String::new(), value=None))]
fn bucket_op(
    py: Python<'_>,
    cfg: (String, String, String, String, String),
    op: String,
    key: String,
    value: Option<Vec<u8>>,
) -> PyResult<PyObject> {
    enum R {
        None,
        Bytes(Vec<u8>),
        Bool(bool),
        Keys(Vec<String>),
    }
    let (endpoint, region, access_key, secret_key, cloud_name) = cfg;
    // all the async in ONE block_on (GIL released); PyObject conversion after.
    let result: anyhow::Result<R> = py.allow_threads(|| {
        shared_runtime().block_on(async move {
            let bucket = bucket_handle(endpoint, region, access_key, secret_key, cloud_name)?;
            Ok(match op.as_str() {
                "create" => {
                    bucket.create_if_not_exists().await?;
                    R::None
                }
                "put" => {
                    bucket.put(&key, value.unwrap_or_default()).await?;
                    R::None
                }
                "get" => R::Bytes(bucket.get(&key).await?),
                "exists" => R::Bool(bucket.exists(&key).await?),
                "list" => R::Keys(bucket.list(&key).await?),
                "delete" => {
                    bucket.delete(&key).await?;
                    R::None
                }
                other => anyhow::bail!("unknown bucket operation: {other}"),
            })
        })
    });
    match result.map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))? {
        R::None => Ok(py.None()),
        R::Bytes(b) => Ok(PyBytes::new_bound(py, &b).into()),
        R::Bool(b) => Ok(b.into_py(py)),
        R::Keys(k) => Ok(k.into_py(py)),
    }
}

// --- sqldb (Postgres): queries via the core's pool, params/rows as JSON ---

fn parse_sql_params(params_json: &str) -> PyResult<Vec<sqldb::SqlParam>> {
    let values: Vec<serde_json::Value> = serde_json::from_str(params_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid params: {e}")))?;
    Ok(values.into_iter().map(sqldb::SqlParam::from_json).collect())
}

/// Runs a SELECT query and returns the rows (JSON: array of objects).
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

/// Runs a multi-statement SQL script (migrations).
#[pyfunction]
fn sqldb_batch(py: Python<'_>, dsn: String, sql: String) -> PyResult<()> {
    py.allow_threads(|| {
        shared_runtime().block_on(async {
            let pool = sqldb::pool_for_dsn(&dsn)?;
            sqldb::batch(&pool, &sql).await
        })
    })
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

/// Validates a query via PREPARE (without running it) → JSON {params, columns}.
#[pyfunction]
fn sqldb_prepare(py: Python<'_>, dsn: String, sql: String) -> PyResult<String> {
    py.allow_threads(|| {
        shared_runtime().block_on(async {
            let pool = sqldb::pool_for_dsn(&dsn)?;
            sqldb::prepare(&pool, &sql).await
        })
    })
    .map(|v| v.to_string())
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

/// Runs a command (INSERT/UPDATE/DELETE/DDL), returns the affected rows.
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

// --- ORM (the engine lives in the CORE; the SDKs send a descriptor) ---

/// Generic ORM operation: `op` ∈ {ensure, insert, get, find, count,
/// update, update_where, delete, delete_where}. `schema_json` describes the
/// table; `args_json` carries {values, where, pk} depending on the operation.
/// Returns the result as JSON (row, rows, count, or null).
#[pyfunction]
fn sqldb_orm(
    py: Python<'_>,
    dsn: String,
    op: String,
    schema_json: String,
    args_json: String,
) -> PyResult<String> {
    let schema: sqldb::orm::TableSchema = serde_json::from_str(&schema_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid schema: {e}")))?;
    let args: serde_json::Value = serde_json::from_str(&args_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid args: {e}")))?;
    let empty = serde_json::Map::new();
    let map_of = |key: &str| -> serde_json::Map<String, serde_json::Value> {
        args.get(key)
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_else(|| empty.clone())
    };
    py.allow_threads(|| {
        shared_runtime().block_on(async {
            let pool = sqldb::pool_for_dsn(&dsn)?;
            use sqldb::orm;
            let result: serde_json::Value = match op.as_str() {
                "ensure" => {
                    orm::ensure(&pool, &schema).await?;
                    serde_json::Value::Null
                }
                "insert" => orm::insert(&pool, &schema, &map_of("values")).await?,
                "get" => {
                    orm::get(&pool, &schema, args.get("pk").cloned().unwrap_or_default())
                        .await?
                }
                "find" => orm::find(&pool, &schema, &map_of("where")).await?,
                "count" => orm::count(&pool, &schema, &map_of("where")).await?.into(),
                "update" => orm::update(
                    &pool,
                    &schema,
                    args.get("pk").cloned().unwrap_or_default(),
                    &map_of("values"),
                )
                .await?
                .into(),
                "update_where" => {
                    orm::update_where(&pool, &schema, &map_of("values"), &map_of("where"))
                        .await?
                        .into()
                }
                "delete" => {
                    orm::delete(&pool, &schema, args.get("pk").cloned().unwrap_or_default())
                        .await?
                        .into()
                }
                "delete_where" => orm::delete_where(&pool, &schema, &map_of("where"))
                    .await?
                    .into(),
                other => anyhow::bail!("unknown ORM operation: {other:?}"),
            };
            Ok(result)
        })
    })
    .map(|v: serde_json::Value| v.to_string())
    .map_err(|e: anyhow::Error| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

// --- sqldb: transactions (begin/commit/rollback without lifetime, Encore style) ---

/// Opens a transaction and returns its identifier.
#[pyfunction]
fn sqldb_begin(py: Python<'_>, dsn: String) -> PyResult<u64> {
    py.allow_threads(|| {
        shared_runtime().block_on(async {
            let pool = sqldb::pool_for_dsn(&dsn)?;
            sqldb::tx_begin(&pool).await
        })
    })
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

/// SELECT within a transaction → rows (JSON).
#[pyfunction]
fn sqldb_tx_query(
    py: Python<'_>,
    tx: u64,
    sql: String,
    params_json: String,
) -> PyResult<String> {
    let params = parse_sql_params(&params_json)?;
    py.allow_threads(|| shared_runtime().block_on(sqldb::tx_query(tx, &sql, params)))
        .map(|rows| rows.to_string())
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

/// Command within a transaction → affected rows.
#[pyfunction]
fn sqldb_tx_execute(
    py: Python<'_>,
    tx: u64,
    sql: String,
    params_json: String,
) -> PyResult<u64> {
    let params = parse_sql_params(&params_json)?;
    py.allow_threads(|| shared_runtime().block_on(sqldb::tx_execute(tx, &sql, params)))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

#[pyfunction]
fn sqldb_tx_commit(py: Python<'_>, tx: u64) -> PyResult<()> {
    py.allow_threads(|| shared_runtime().block_on(sqldb::tx_commit(tx)))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

#[pyfunction]
fn sqldb_tx_rollback(py: Python<'_>, tx: u64) -> PyResult<()> {
    py.allow_threads(|| shared_runtime().block_on(sqldb::tx_rollback(tx)))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e:#}")))
}

// --- api (HTTP server): the binding implements the core's `Handler` trait
//     by calling the Python handler (with the GIL), Encore's `runtimes/js` style. ---

/// Formats the Python exception (full traceback) for structured logs.
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

/// Implements the core's `AuthHandler` trait by calling the Python function
/// (`@auth_handler`). `None` → 401; a raised APIError → its status/body;
/// any other exception → 500 (traceback logged).
struct PyAuthHandler {
    func: Py<PyAny>,
}

impl api::AuthHandler for PyAuthHandler {
    fn authenticate(&self, token: &str) -> api::AuthOutcome {
        Python::with_gil(|py| match self.func.bind(py).call1((token,)) {
            Ok(result) => {
                if result.is_none() {
                    return api::AuthOutcome::Denied {
                        status: 401,
                        body: api::error_json("unauthenticated", "invalid token", None),
                    };
                }
                match py
                    .import_bound("json")
                    .and_then(|json| json.call_method1("dumps", (result,)))
                    .and_then(|s| s.extract::<String>())
                {
                    Ok(data) => api::AuthOutcome::Authenticated(data),
                    Err(_) => api::AuthOutcome::Denied {
                        status: 500,
                        body: api::error_json(
                            "internal",
                            "auth data not serializable",
                            None,
                        ),
                    },
                }
            }
            Err(e) => {
                if let Some(resp) = http_error_response(py, &e) {
                    return api::AuthOutcome::Denied {
                        status: resp.status,
                        body: resp.body,
                    };
                }
                tracing::error!(
                    target: "vignemale::app",
                    traceback = %format_py_err(py, &e),
                    "exception in the auth handler"
                );
                api::AuthOutcome::Denied {
                    status: 500,
                    body: api::error_json("internal", "internal error", None),
                }
            }
        })
    }
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
                    && e.to_string().contains("invalid JSON body")
                {
                    return api::Response {
                        status: 400,
                        body: api::error_json("invalid_argument", "invalid JSON body", None),
                    };
                }
                tracing::error!(
                    target: "vignemale::app",
                    request_id = %request_id,
                    traceback = %format_py_err(py, &e),
                    "unhandled exception in the handler"
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

/// If the exception is an SDK `HTTPError` (attributes `vignemale_status` /
/// `vignemale_body`), builds the corresponding HTTP response.
fn http_error_response(py: Python<'_>, e: &PyErr) -> Option<api::Response> {
    let val = e.value_bound(py);
    let status: u16 = val.getattr("vignemale_status").ok()?.extract().ok()?;
    let body: String = val.getattr("vignemale_body").ok()?.extract().ok()?;
    Some(api::Response {
        status,
        body: body.into_bytes(),
    })
}

/// Builds the common kwargs: path params, `query` (dict), `headers`
/// (dict, lowercase names) and `body` (parsed JSON) if present. The SDK then
/// filters them according to the handler's signature.
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
    if let Some(auth_json) = &req.auth_data {
        let parsed = py
            .import_bound("json")?
            .call_method1("loads", (auth_json.as_str(),))?;
        kwargs.set_item("auth", parsed)?;
    }
    if !req.body.is_empty() {
        let invalid =
            || pyo3::exceptions::PyValueError::new_err("invalid JSON body");
        let body_str = std::str::from_utf8(&req.body).map_err(|_| invalid())?;
        let parsed = py
            .import_bound("json")?
            .call_method1("loads", (body_str,))
            .map_err(|_| invalid())?;
        kwargs.set_item("body", parsed)?;
    }
    Ok(kwargs)
}

/// Calls the Python handler then serializes the return value to JSON.
fn call_py_handler(py: Python<'_>, func: &Py<PyAny>, req: api::Request) -> PyResult<Vec<u8>> {
    let kwargs = build_kwargs(py, &req)?;
    let result = func.bind(py).call((), Some(&kwargs))?;
    let dumped: String = py
        .import_bound("json")?
        .call_method1("dumps", (result,))?
        .extract()?;
    Ok(dumped.into_bytes())
}

// --- streaming (SSE): the binding implements the core's `StreamHandler` trait ---

#[pyclass]
struct PyStreamSink {
    // Option: the binding closes the stream explicitly when the handler
    // returns — the end of the stream does not depend on Python's GC (a
    // reference cycle on the app side would keep the channel open indefinitely).
    sink: std::sync::Mutex<Option<api::StreamSink>>,
}

#[pymethods]
impl PyStreamSink {
    /// Pushes a fragment into the SSE stream. `false` if the stream is closed.
    fn write(&self, py: Python<'_>, chunk: String) -> bool {
        let sink = self.sink.lock().expect("sink lock").clone();
        match sink {
            Some(s) => py.allow_threads(move || s.write(chunk)),
            None => false,
        }
    }
}

impl PyStreamSink {
    fn close(&self) {
        self.sink.lock().expect("sink lock").take();
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
                    "unhandled exception in the streaming handler"
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
    let py_sink = Py::new(py, PyStreamSink {
        sink: std::sync::Mutex::new(Some(sink)),
    })?;
    kwargs.set_item("stream", py_sink.clone_ref(py))?;
    let result = func.bind(py).call((), Some(&kwargs));
    // handler finished (success or not) → we close the SSE stream explicitly
    py_sink.borrow(py).close();
    result?;
    Ok(())
}

/// Graceful shutdown sequence (mirror of Encore's shutdown.rs):
/// 1. healthz → 503 immediately (the load balancer sees it);
/// 2. `keep_accepting`: we KEEP accepting during this window, giving the LB
///    time to stop routing to us (otherwise it would send requests to a
///    process that no longer accepts → refused connections). Set by
///    `VIGNEMALE_SHUTDOWN_KEEP_ACCEPTING` (seconds; 0 by default, useful in
///    K8s/Scaleway prod);
/// 3. stop-accept + drain in-flight requests, bounded by
///    `VIGNEMALE_SHUTDOWN_TIMEOUT` (10 s by default).
fn graceful_shutdown(
    py: Python<'_>,
    shutting_down: &Arc<std::sync::atomic::AtomicBool>,
    shutdown_tx: &tokio::sync::watch::Sender<bool>,
    server_thread: &std::thread::JoinHandle<Result<(), String>>,
) {
    let env_secs = |name: &str, default: u64| {
        std::env::var(name).ok().and_then(|v| v.parse::<u64>().ok()).unwrap_or(default)
    };
    shutting_down.store(true, std::sync::atomic::Ordering::SeqCst); // healthz → 503
    let keep = env_secs("VIGNEMALE_SHUTDOWN_KEEP_ACCEPTING", 0);
    if keep > 0 {
        py.allow_threads(|| std::thread::sleep(std::time::Duration::from_secs(keep)));
    }
    let _ = shutdown_tx.send(true); // stop-accept + drain
    let drain = env_secs("VIGNEMALE_SHUTDOWN_TIMEOUT", 10);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(drain);
    py.allow_threads(|| {
        while !server_thread.is_finished() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    });
}

/// Starts the HTTP server with the given endpoints (blocks until shutdown).
/// `endpoints` = list of (name, method, path, handler, stream, auth,
/// timeout_s, body_limit).
#[pyfunction]
#[pyo3(signature = (endpoints, addr, auth_handler=None, statics=vec![], reuse_port=false))]
#[allow(clippy::type_complexity)]
fn serve(
    py: Python<'_>,
    endpoints: Vec<(
        String,
        String,
        String,
        Py<PyAny>,
        bool,
        bool,
        Option<f64>,
        Option<u64>,
        bool,
    )>,
    addr: String,
    auth_handler: Option<Py<PyAny>>,
    statics: Vec<(String, String, Option<String>, bool)>,
    reuse_port: bool,
) -> PyResult<()> {
    let socket: std::net::SocketAddr = addr
        .parse()
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid address: {e}")))?;
    let mut mgr = api::Manager::new();
    for (name, method, path, func, stream, requires_auth, timeout_s, body_limit, expose) in endpoints
    {
        let kind = if stream {
            api::HandlerKind::Stream(Arc::new(PyStreamHandler { func }))
        } else {
            api::HandlerKind::Unary(Arc::new(PyHandler { func }))
        };
        mgr.register(
            api::Endpoint {
                name,
                method,
                path,
                requires_auth,
                expose,
                timeout_ms: timeout_s.map(|s| (s * 1000.0) as u64),
                body_limit,
            },
            kind,
        );
    }
    if let Some(func) = auth_handler {
        mgr.set_auth_handler(Arc::new(PyAuthHandler { func }));
    }
    for (path, dir, not_found, fallback) in statics {
        mgr.add_static(api::StaticRoute {
            path,
            dir,
            not_found,
            fallback,
        });
    }
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
    let shutting_down = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let sd_flag = shutting_down.clone();
    // The server runs on a dedicated thread (which does not hold the GIL); the
    // main thread releases the GIL and waits, so handlers can acquire it.
    let server_thread = std::thread::spawn(move || -> Result<(), String> {
        let runtime = tokio::runtime::Runtime::new().map_err(|e| e.to_string())?;
        runtime
            .block_on(mgr.serve(socket, shutdown_rx, sd_flag, reuse_port))
            .map_err(|e| e.to_string())
    });
    // Wait in slices (not a blocking `join`): between two slices we go back
    // through `check_signals`, otherwise Ctrl-C would never raise
    // KeyboardInterrupt — the signal would be recorded by CPython but the
    // interpreter would never regain control.
    loop {
        if server_thread.is_finished() {
            let outcome = match server_thread.join() {
                Ok(r) => r,
                Err(_) => Err("server thread panicked".to_string()),
            };
            return outcome.map_err(pyo3::exceptions::PyRuntimeError::new_err);
        }
        py.allow_threads(|| std::thread::sleep(std::time::Duration::from_millis(100)));
        if let Err(signal) = py.check_signals() {
            graceful_shutdown(py, &shutting_down, &shutdown_tx, &server_thread);
            return Err(signal);
        }
    }
}

/// Starts the GATEWAY: routes public traffic by path prefix to the backend
/// services, authenticates at the edge, forwards signed. `routes` = list of
/// (prefix, service, upstream_url, requires_auth).
#[pyfunction]
#[pyo3(signature = (routes, addr, auth_handler=None, reuse_port=false))]
fn serve_gateway(
    py: Python<'_>,
    routes: Vec<(String, String, String, bool)>,
    addr: String,
    auth_handler: Option<Py<PyAny>>,
    reuse_port: bool,
) -> PyResult<()> {
    let socket: std::net::SocketAddr = addr
        .parse()
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid address: {e}")))?;
    let gw_routes: Vec<api::GatewayRoute> = routes
        .into_iter()
        .map(|(prefix, service, upstream, requires_auth)| api::GatewayRoute {
            prefix,
            service,
            upstream,
            requires_auth,
        })
        .collect();
    let auth: Option<Arc<dyn api::AuthHandler>> =
        auth_handler.map(|func| Arc::new(PyAuthHandler { func }) as Arc<dyn api::AuthHandler>);

    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
    let shutting_down = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let sd_flag = shutting_down.clone();
    let server_thread = std::thread::spawn(move || -> Result<(), String> {
        let runtime = tokio::runtime::Runtime::new().map_err(|e| e.to_string())?;
        runtime
            .block_on(api::gateway::serve(gw_routes, socket, auth, shutdown_rx, sd_flag, reuse_port))
            .map_err(|e| e.to_string())
    });
    loop {
        if server_thread.is_finished() {
            return match server_thread.join() {
                Ok(r) => r,
                Err(_) => Err("gateway thread panicked".to_string()),
            }
            .map_err(pyo3::exceptions::PyRuntimeError::new_err);
        }
        py.allow_threads(|| std::thread::sleep(std::time::Duration::from_millis(100)));
        if let Err(signal) = py.check_signals() {
            graceful_shutdown(py, &shutting_down, &shutdown_tx, &server_thread);
            return Err(signal);
        }
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
    m.add_function(wrap_pyfunction!(bucket_op, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_query, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_execute, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_prepare, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_batch, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_orm, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_begin, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_tx_query, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_tx_execute, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_tx_commit, m)?)?;
    m.add_function(wrap_pyfunction!(sqldb_tx_rollback, m)?)?;
    m.add_function(wrap_pyfunction!(serve, m)?)?;
    m.add_function(wrap_pyfunction!(serve_gateway, m)?)?;
    Ok(())
}
