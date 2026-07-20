// Focused HTTP server (axum): both classic AND streaming (SSE) handlers.
//
// Handlers are called outside the async executor (`spawn_blocking`) because the
// app code (Python) is blocking and holds the GIL.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use axum::body::{Body, Bytes};
use axum::extract::rejection::BytesRejection;
use axum::extract::{DefaultBodyLimit, OriginalUri, RawPathParams, RawQuery};
use axum::http::header::CONTENT_TYPE;
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::sse::{Event, Sse};
use axum::response::{IntoResponse, Response as AxumResponse};
use axum::routing::{get, on, MethodFilter};
use axum::Router;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt;

use super::error::error_json;
use super::Endpoint;

/// Request passed to a handler.
pub struct Request {
    pub params: Vec<(String, String)>,
    /// Query-string parameters (`?q=noon&limit=3`), decoded.
    pub query: Vec<(String, String)>,
    /// HTTP headers (lowercase names; non-UTF-8 values ignored).
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
    /// Unique identifier of the request — returned in `x-vignemale-request-id`
    /// and present in every log line that concerns it.
    pub request_id: String,
    /// Auth data (JSON) if the endpoint is protected and the token was validated.
    pub auth_data: Option<String>,
}

/// Response from a classic handler.
pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}

/// Classic handler (request → response).
pub trait Handler: Send + Sync + 'static {
    fn call(&self, req: Request) -> Response;
}

/// Write sink of a streaming handler: each `write` becomes an SSE `data:`
/// event. To be called outside an async context (the handler runs on a blocking
/// thread).
#[derive(Clone)]
pub struct StreamSink {
    tx: tokio::sync::mpsc::Sender<String>,
}

impl StreamSink {
    /// Pushes a chunk. Returns `false` if the stream is closed (client gone).
    pub fn write(&self, chunk: String) -> bool {
        self.tx.blocking_send(chunk).is_ok()
    }
}

/// Streaming handler: pushes chunks via `sink` as they come.
pub trait StreamHandler: Send + Sync + 'static {
    fn call(&self, req: Request, sink: StreamSink);
}

/// A handler, classic or streaming.
pub enum HandlerKind {
    Unary(Arc<dyn Handler>),
    Stream(Arc<dyn StreamHandler>),
}

/// Static files served by the CORE (mirror of Encore's static_assets.rs):
/// zero app code executed — a frontend (Next.js `output: 'export'`,
/// Vite…) is served directly by the Rust runtime.
#[derive(Debug, Clone)]
pub struct StaticRoute {
    /// URL prefix ("/assets") or "/" (becomes the fallback route).
    pub path: String,
    /// Directory served.
    pub dir: String,
    /// File returned for unknown paths (SPA: index.html).
    pub not_found: Option<String>,
    /// Serves as fallback (any route not matched by the API).
    pub fallback: bool,
}

/// Result of an authentication attempt.
pub enum AuthOutcome {
    /// Valid token — the auth data (JSON) is passed to the handler.
    Authenticated(String),
    /// Denied — status + error body to return as-is.
    Denied { status: u16, body: Vec<u8> },
}

/// Authentication handler provided by the app (via the binding): receives the
/// token, decides. Called by the server BEFORE the endpoint handler.
pub trait AuthHandler: Send + Sync + 'static {
    fn authenticate(&self, token: &str) -> AuthOutcome;
}

/// Extracts the token from a request: `Authorization: Bearer …` (or raw value),
/// otherwise `?token=` (clients without headers, e.g. EventSource).
fn extract_token(headers: &HeaderMap, query: &Option<String>) -> Option<String> {
    if let Some(raw) = headers.get("authorization").and_then(|v| v.to_str().ok()) {
        let token = raw.strip_prefix("Bearer ").or(raw.strip_prefix("bearer ")).unwrap_or(raw);
        if !token.is_empty() {
            return Some(token.to_string());
        }
    }
    form_urlencoded::parse(query.as_deref().unwrap_or_default().as_bytes())
        .find(|(k, _)| k == "token")
        .map(|(_, v)| v.into_owned())
        .filter(|t| !t.is_empty())
}

/// Runs authentication for a protected endpoint. `Ok(json)` = authorized.
pub(crate) async fn run_auth_pub(
    auth: &Option<Arc<dyn AuthHandler>>,
    headers: &HeaderMap,
    query: &Option<String>,
) -> Result<Option<String>, Response> {
    run_auth(auth, headers, query).await
}
async fn run_auth(
    auth: &Option<Arc<dyn AuthHandler>>,
    headers: &HeaderMap,
    query: &Option<String>,
) -> Result<Option<String>, Response> {
    let Some(handler) = auth else {
        return Err(Response {
            status: 500,
            body: error_json("internal", "protected endpoint without auth handler", None),
        });
    };
    let Some(token) = extract_token(headers, query) else {
        return Err(Response {
            status: 401,
            body: error_json("unauthenticated", "authentication required", None),
        });
    };
    let handler = handler.clone();
    let outcome = tokio::task::spawn_blocking(move || handler.authenticate(&token))
        .await
        .unwrap_or(AuthOutcome::Denied {
            status: 500,
            body: error_json("internal", "auth handler panicked", None),
        });
    match outcome {
        AuthOutcome::Authenticated(data) => Ok(Some(data)),
        AuthOutcome::Denied { status, body } => Err(Response { status, body }),
    }
}

/// Resolves the identity of an incoming request on a public route.
///
/// - **mesh backend** (`VIGNEMALE_SERVICE_NAME` set → container behind a
///   gateway): the request MUST carry a valid svcauth signature (from the
///   gateway or a peer service); we then TRUST the propagated identity
///   (`x-vignemale-auth-data`) without replaying the auth handler. A direct
///   unsigned hit is rejected (401) → the gateway is the effective entrypoint.
/// - **edge** (mono / no mesh): authentication at the edge via `run_auth`.
#[allow(clippy::too_many_arguments)]
async fn resolve_inbound_auth(
    auth: &Option<Arc<dyn AuthHandler>>,
    headers: &HeaderMap,
    query: &Option<String>,
    requires_auth: bool,
    mesh: bool,
    sig_path: &str,
    body: &[u8],
) -> Result<Option<String>, Response> {
    if !mesh {
        return if requires_auth {
            run_auth(auth, headers, query).await
        } else {
            Ok(None)
        };
    }
    let h = |name: &str| {
        headers
            .get(name)
            .and_then(|v| v.to_str().ok())
            .unwrap_or_default()
    };
    let sig = h("x-vignemale-signature");
    if sig.is_empty() {
        return Err(Response {
            status: 401,
            body: error_json(
                "unauthenticated",
                "unsigned call on a service in mesh mode (go through the gateway)",
                None,
            ),
        });
    }
    let secrets = super::svcauth::accepted_secrets_from_env();
    if secrets.is_empty() {
        return Err(Response {
            status: 401,
            body: error_json("unauthenticated", "VIGNEMALE_SERVICE_SECRET not configured", None),
        });
    }
    let auth_header = h("x-vignemale-auth-data");
    if let Err(reason) = super::svcauth::verify_any(
        &secrets,
        h("x-vignemale-date"),
        h("x-vignemale-caller"),
        sig_path,
        body,
        auth_header.as_bytes(),
        sig,
        super::svcauth::now_epoch(),
    ) {
        return Err(Response {
            status: 401,
            body: error_json("unauthenticated", reason, None),
        });
    }
    if requires_auth && auth_header.is_empty() {
        return Err(Response {
            status: 401,
            body: error_json("unauthenticated", "protected endpoint: identity not propagated", None),
        });
    }
    Ok((!auth_header.is_empty()).then(|| auth_header.to_string()))
}

fn deny_response(resp: Response, request_id: &str) -> AxumResponse {
    let mut r = AxumResponse::new(Body::from(resp.body));
    *r.status_mut() =
        StatusCode::from_u16(resp.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    r.headers_mut()
        .insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    set_request_id_header(&mut r, request_id);
    r
}

fn env_u64(name: &str, default: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

/// Effective max processing timeout for an endpoint (None = disabled).
fn effective_timeout(ep_timeout_ms: Option<u64>) -> Option<std::time::Duration> {
    let ms = ep_timeout_ms.unwrap_or_else(|| env_u64("VIGNEMALE_REQUEST_TIMEOUT", 30) * 1000);
    (ms > 0).then(|| std::time::Duration::from_millis(ms))
}

/// Validates the extracted body; exceeding `body_limit` → structured 413.
fn check_body(
    body: Result<Bytes, BytesRejection>,
) -> Result<Bytes, Response> {
    body.map_err(|_| Response {
        status: 413,
        body: error_json("resource_exhausted", "request body too large", None),
    })
}

fn method_filter(method: &str) -> Option<MethodFilter> {
    match method.to_uppercase().as_str() {
        "GET" => Some(MethodFilter::GET),
        "POST" => Some(MethodFilter::POST),
        "PUT" => Some(MethodFilter::PUT),
        "DELETE" => Some(MethodFilter::DELETE),
        "PATCH" => Some(MethodFilter::PATCH),
        "HEAD" => Some(MethodFilter::HEAD),
        "OPTIONS" => Some(MethodFilter::OPTIONS),
        _ => None,
    }
}

/// W3C trace context: reuses the incoming `traceparent` if valid, otherwise
/// creates one. Returns (trace_id, traceparent).
pub(crate) fn trace_context_pub(headers: &HeaderMap) -> (String, String) { trace_context(headers) }
fn trace_context(headers: &HeaderMap) -> (String, String) {
    if let Some(tp) = headers.get("traceparent").and_then(|v| v.to_str().ok()) {
        let parts: Vec<&str> = tp.split('-').collect();
        if parts.len() >= 4 && parts[1].len() == 32 {
            return (parts[1].to_string(), tp.to_string());
        }
    }
    let trace_id = uuid::Uuid::new_v4().simple().to_string(); // 32 hex
    let span_id = uuid::Uuid::new_v4().simple().to_string()[..16].to_string();
    (trace_id.clone(), format!("00-{trace_id}-{span_id}-01"))
}

fn make_request(
    params: RawPathParams,
    query: Option<String>,
    headers: &HeaderMap,
    body: Bytes,
    request_id: String,
    auth_data: Option<String>,
    traceparent: &str,
) -> Request {
    let mut hdrs: Vec<(String, String)> = headers
        .iter()
        .filter_map(|(k, v)| {
            v.to_str()
                .ok()
                .map(|v| (k.as_str().to_lowercase(), v.to_string()))
        })
        .filter(|(k, _)| k != "traceparent")
        .collect();
    // always present (reused or generated) → the app can propagate it via call()
    hdrs.push(("traceparent".to_string(), traceparent.to_string()));
    Request {
        params: params
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect(),
        query: form_urlencoded::parse(query.unwrap_or_default().as_bytes())
            .map(|(k, v)| (k.into_owned(), v.into_owned()))
            .collect(),
        headers: hdrs,
        body: body.to_vec(),
        request_id,
        auth_data,
    }
}

/// CORS layer: fully open by default (dev); `VIGNEMALE_CORS_ALLOW_ORIGINS`
/// (comma-separated list of origins, or `*`) restricts it in prod.
pub(crate) fn cors_layer_pub() -> tower_http::cors::CorsLayer { cors_layer() }
fn cors_layer() -> tower_http::cors::CorsLayer {
    use tower_http::cors::{Any, CorsLayer};
    let layer = CorsLayer::new()
        .allow_methods(Any)
        .allow_headers(Any)
        .expose_headers([REQUEST_ID_HEADER.parse::<axum::http::HeaderName>().unwrap()]);
    match std::env::var("VIGNEMALE_CORS_ALLOW_ORIGINS") {
        Ok(origins) if origins != "*" => layer.allow_origin(
            origins
                .split(',')
                .filter_map(|o| o.trim().parse::<HeaderValue>().ok())
                .collect::<Vec<_>>(),
        ),
        _ => layer.allow_origin(Any),
    }
}

const REQUEST_ID_HEADER: &str = "x-vignemale-request-id";

fn set_request_id_header(r: &mut AxumResponse, request_id: &str) {
    if let Ok(v) = HeaderValue::from_str(request_id) {
        r.headers_mut().insert(REQUEST_ID_HEADER, v);
    }
}

#[allow(clippy::too_many_arguments)]
fn log_request(
    endpoint: &str,
    method: &str,
    path: &str,
    status: u16,
    ms: u64,
    id: &str,
    trace_id: &str,
) {
    if status >= 500 {
        tracing::error!(
            target: "vignemale::api",
            endpoint, method, path, status, duration_ms = ms, request_id = id,
            trace_id,
            "request failed"
        );
    } else {
        tracing::info!(
            target: "vignemale::api",
            endpoint, method, path, status, duration_ms = ms, request_id = id,
            trace_id,
            "request handled"
        );
    }
}

pub fn build_router(
    endpoints: Vec<(Endpoint, HandlerKind)>,
    auth: Option<Arc<dyn AuthHandler>>,
    shutting_down: Arc<AtomicBool>,
    statics: Vec<StaticRoute>,
) -> anyhow::Result<Router> {
    let default_body_limit = env_u64("VIGNEMALE_MAX_BODY", 10 * 1024 * 1024) as usize;
    // Container behind a gateway: the deploy sets VIGNEMALE_REQUIRE_SVCAUTH=1
    // (only when a gateway is deployed). Public traffic then arrives signed by
    // the gateway → we require the signature and trust the propagated auth.
    // NB: distinct from VIGNEMALE_SERVICE_NAME (filtering/discovery), because a
    // named service can still be reachable at the edge without a gateway.
    let mesh_backend = std::env::var("VIGNEMALE_REQUIRE_SVCAUTH").is_ok_and(|v| v == "1");

    // Index of unary endpoints for signed service-to-service calls.
    let mut internal: std::collections::HashMap<String, (Arc<dyn Handler>, bool)> =
        std::collections::HashMap::new();
    for (ep, kind) in &endpoints {
        if let HandlerKind::Unary(h) = kind {
            internal.insert(ep.name.clone(), (h.clone(), ep.requires_auth));
        }
    }

    let mut app = Router::new();
    for (ep, kind) in endpoints {
        // PRIVATE (expose=false): no public route. The endpoint stays in the
        // `internal` map above → reachable only via a signed `call()`.
        if !ep.expose {
            continue;
        }
        let filter = method_filter(&ep.method)
            .ok_or_else(|| anyhow::anyhow!("unsupported HTTP method: {}", ep.method))?;
        let (name, method, path) = (Arc::<str>::from(ep.name), ep.method, ep.path);
        let route_path = path.clone();
        let path: Arc<str> = Arc::from(path);
        let method: Arc<str> = Arc::from(method);
        let requires_auth = ep.requires_auth;
        let timeout = effective_timeout(ep.timeout_ms);
        let body_limit = ep.body_limit.map(|n| n as usize).unwrap_or(default_body_limit);
        let auth = auth.clone();
        app = match kind {
            HandlerKind::Unary(handler) => app.route(
                &route_path,
                on(
                    filter,
                    move |params: RawPathParams,
                          RawQuery(query): RawQuery,
                          OriginalUri(orig_uri): OriginalUri,
                          headers: HeaderMap,
                          body: Result<Bytes, BytesRejection>| {
                        let handler = handler.clone();
                        let auth = auth.clone();
                        let (name, method, path) = (name.clone(), method.clone(), path.clone());
                        async move {
                            let request_id = crate::observability::request_id();
                            let started = std::time::Instant::now();
                            let (trace_id, traceparent) = trace_context(&headers);
                            // body first (needed to verify the signature in mesh mode)
                            let body = match check_body(body) {
                                Ok(b) => b,
                                Err(denied) => {
                                    log_request(&name, &method, &path, denied.status,
                                        started.elapsed().as_millis() as u64, &request_id, &trace_id);
                                    return deny_response(denied, &request_id);
                                }
                            };
                            let auth_data = match resolve_inbound_auth(
                                &auth, &headers, &query, requires_auth, mesh_backend,
                                orig_uri.path(), &body,
                            ).await {
                                Ok(data) => data,
                                Err(denied) => {
                                    log_request(&name, &method, &path, denied.status,
                                        started.elapsed().as_millis() as u64, &request_id, &trace_id);
                                    return deny_response(denied, &request_id);
                                }
                            };
                            let req = make_request(
                                params,
                                query,
                                &headers,
                                body,
                                request_id.clone(),
                                auth_data,
                                &traceparent,
                            );
                            // the blocking handler runs in the background; past
                            // the timeout we reply 504 (the handler still finishes,
                            // its logs are kept — Encore's CancellationGuard style)
                            let work = tokio::task::spawn_blocking(move || handler.call(req));
                            let resp = match timeout {
                                Some(d) => match tokio::time::timeout(d, work).await {
                                    Ok(joined) => joined.unwrap_or(Response {
                                        status: 500,
                                        body: error_json("internal", "handler panicked", None),
                                    }),
                                    Err(_) => Response {
                                        status: 504,
                                        body: error_json(
                                            "deadline_exceeded",
                                            "processing deadline exceeded",
                                            None,
                                        ),
                                    },
                                },
                                None => work.await.unwrap_or(Response {
                                    status: 500,
                                    body: error_json("internal", "handler panicked", None),
                                }),
                            };
                            log_request(
                                &name,
                                &method,
                                &path,
                                resp.status,
                                started.elapsed().as_millis() as u64,
                                &request_id,
                                &trace_id,
                            );
                            let mut r = AxumResponse::new(Body::from(resp.body));
                            *r.status_mut() = StatusCode::from_u16(resp.status)
                                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
                            r.headers_mut().insert(
                                CONTENT_TYPE,
                                HeaderValue::from_static("application/json"),
                            );
                            set_request_id_header(&mut r, &request_id);
                            r
                        }
                    },
                )
                .layer(DefaultBodyLimit::max(body_limit)),
            ),
            HandlerKind::Stream(handler) => app.route(
                &route_path,
                on(
                    filter,
                    move |params: RawPathParams,
                          RawQuery(query): RawQuery,
                          OriginalUri(orig_uri): OriginalUri,
                          headers: HeaderMap,
                          body: Result<Bytes, BytesRejection>| {
                        let handler = handler.clone();
                        let auth = auth.clone();
                        let (name, method, path) = (name.clone(), method.clone(), path.clone());
                        async move {
                            let request_id = crate::observability::request_id();
                            let started = std::time::Instant::now();
                            let (trace_id, traceparent) = trace_context(&headers);
                            let body = match check_body(body) {
                                Ok(b) => b,
                                Err(denied) => {
                                    log_request(&name, &method, &path, denied.status,
                                        started.elapsed().as_millis() as u64, &request_id, &trace_id);
                                    return deny_response(denied, &request_id);
                                }
                            };
                            // auth runs BEFORE opening the stream → a real 401
                            let auth_data = match resolve_inbound_auth(
                                &auth, &headers, &query, requires_auth, mesh_backend,
                                orig_uri.path(), &body,
                            ).await {
                                Ok(data) => data,
                                Err(denied) => {
                                    log_request(&name, &method, &path, denied.status,
                                        started.elapsed().as_millis() as u64, &request_id, &trace_id);
                                    return deny_response(denied, &request_id);
                                }
                            };
                            let req = make_request(
                                params,
                                query,
                                &headers,
                                body,
                                request_id.clone(),
                                auth_data,
                                &traceparent,
                            );
                            let (tx, rx) = tokio::sync::mpsc::channel::<String>(64);
                            let sink = StreamSink { tx };
                            // The (blocking) handler pushes fragments via `sink`;
                            // we log at the end of the stream (duration = handler lifetime).
                            let log_id = request_id.clone();
                            let log_trace = trace_id.clone();
                            tokio::task::spawn_blocking(move || {
                                handler.call(req, sink);
                                log_request(
                                    &name,
                                    &method,
                                    &path,
                                    200,
                                    started.elapsed().as_millis() as u64,
                                    &log_id,
                                    &log_trace,
                                );
                            });
                            let stream = ReceiverStream::new(rx).map(|chunk| {
                                Ok::<Event, Infallible>(Event::default().data(chunk))
                            });
                            let mut r = Sse::new(stream).into_response();
                            set_request_id_header(&mut r, &request_id);
                            r
                        }
                    },
                )
                .layer(DefaultBodyLimit::max(body_limit)),
            ),
        };
    }

    // Service-to-service calls: signed internal route (HMAC, shared secret
    // VIGNEMALE_SERVICE_SECRET). The payload is {"params": {...}, "body": …};
    // the caller's auth data arrives propagated in
    // `x-vignemale-auth-data`, the trace context in `traceparent`.
    {
        let internal = Arc::new(internal);
        app = app.route(
            "/__vignemale/call/:endpoint",
            axum::routing::post(
                move |axum::extract::Path(endpoint): axum::extract::Path<String>,
                      headers: HeaderMap,
                      body: Bytes| {
                    let internal = internal.clone();
                    async move {
                        let request_id = crate::observability::request_id();
                        let started = std::time::Instant::now();
                        let (trace_id, traceparent) = trace_context(&headers);
                        let hdr = |name: &str| -> String {
                            headers
                                .get(name)
                                .and_then(|v| v.to_str().ok())
                                .unwrap_or_default()
                                .to_string()
                        };
                        let caller = hdr("x-vignemale-caller");
                        // propagated identity — read BEFORE verification since it is covered by the signature.
                        let auth_header = hdr("x-vignemale-auth-data");
                        let finish = |resp: Response| {
                            tracing::info!(
                                target: "vignemale::api",
                                endpoint = %endpoint, caller = %caller,
                                status = resp.status,
                                duration_ms = started.elapsed().as_millis() as u64,
                                request_id = %request_id, trace_id = %trace_id,
                                "internal call handled"
                            );
                            deny_response(resp, &request_id)
                        };

                        // 1) mandatory signature (set of secrets: zero-downtime rotation)
                        let secrets = super::svcauth::accepted_secrets_from_env();
                        if secrets.is_empty() {
                            return finish(Response {
                                status: 401,
                                body: error_json(
                                    "unauthenticated",
                                    "service-to-service calls not configured (VIGNEMALE_SERVICE_SECRET)",
                                    None,
                                ),
                            });
                        }
                        if let Err(reason) = super::svcauth::verify_any(
                            &secrets,
                            &hdr("x-vignemale-date"),
                            &caller,
                            &endpoint,
                            &body,
                            auth_header.as_bytes(),
                            &hdr("x-vignemale-signature"),
                            super::svcauth::now_epoch(),
                        ) {
                            return finish(Response {
                                status: 401,
                                body: error_json("unauthenticated", reason, None),
                            });
                        }

                        // 2) target endpoint
                        let Some((handler, requires_auth)) = internal.get(&endpoint).cloned()
                        else {
                            return finish(Response {
                                status: 404,
                                body: error_json("not_found", "unknown internal endpoint", None),
                            });
                        };

                        // 3) payload {"params": {...}, "body": …}
                        let Ok(payload) = serde_json::from_slice::<serde_json::Value>(&body)
                        else {
                            return finish(Response {
                                status: 400,
                                body: error_json("invalid_argument", "invalid payload", None),
                            });
                        };
                        let params: Vec<(String, String)> = payload
                            .get("params")
                            .and_then(|p| p.as_object())
                            .map(|o| {
                                o.iter()
                                    .map(|(k, v)| {
                                        let s = v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string());
                                        (k.clone(), s)
                                    })
                                    .collect()
                            })
                            .unwrap_or_default();
                        let inner_body = match payload.get("body") {
                            None | Some(serde_json::Value::Null) => Vec::new(),
                            Some(v) => serde_json::to_vec(v).unwrap_or_default(),
                        };

                        // 4) propagated auth (internal calls are trusted:
                        //    no second pass through the auth handler, Encore style).
                        //    `auth_header` was already read and covered by the signature.
                        let auth_data = if requires_auth {
                            if auth_header.is_empty() {
                                return finish(Response {
                                    status: 401,
                                    body: error_json(
                                        "unauthenticated",
                                        "protected endpoint: auth data not propagated",
                                        None,
                                    ),
                                });
                            }
                            Some(auth_header)
                        } else {
                            None
                        };

                        let req = Request {
                            params,
                            query: vec![],
                            headers: vec![
                                ("traceparent".to_string(), traceparent.clone()),
                                ("x-vignemale-caller".to_string(), caller.clone()),
                            ],
                            body: inner_body,
                            request_id: request_id.clone(),
                            auth_data,
                        };
                        let resp = tokio::task::spawn_blocking(move || handler.call(req))
                            .await
                            .unwrap_or(Response {
                                status: 500,
                                body: error_json("internal", "handler panicked", None),
                            });
                        finish(resp)
                    }
                },
            ),
        );
    }

    // Internal routes: health check (for load balancers / containers).
    // During graceful shutdown → 503 shutting_down (the orchestrator knows).
    app = app.route(
        "/__vignemale/healthz",
        get(move || async move {
            let (status, body) = if shutting_down.load(Ordering::SeqCst) {
                (
                    StatusCode::SERVICE_UNAVAILABLE,
                    error_json("shutting_down", "shutting down", None),
                )
            } else {
                (StatusCode::OK, error_json("ok", "vignemale up", None))
            };
            let mut r = AxumResponse::new(Body::from(body));
            *r.status_mut() = status;
            r.headers_mut()
                .insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
            r
        }),
    );

    // Static files: served by tower-http directly (zero app code).
    let mut has_static_fallback = false;
    for s in &statics {
        use tower_http::services::{ServeDir, ServeFile};
        // `.fallback(...)` (not `not_found_service`, which forces a 404):
        // a SPA must return index.html with 200 for client-side routing.
        if s.fallback {
            has_static_fallback = true;
            match &s.not_found {
                Some(nf) => {
                    app = app
                        .fallback_service(ServeDir::new(&s.dir).fallback(ServeFile::new(nf)))
                }
                None => app = app.fallback_service(ServeDir::new(&s.dir)),
            }
        } else {
            match &s.not_found {
                Some(nf) => {
                    app = app.nest_service(
                        &s.path,
                        ServeDir::new(&s.dir).fallback(ServeFile::new(nf)),
                    )
                }
                None => app = app.nest_service(&s.path, ServeDir::new(&s.dir)),
            }
        }
    }

    // Unknown route → structured 404 (unless a frontend serves the fallback).
    if !has_static_fallback {
        app = app.fallback(|| async {
            let mut r = AxumResponse::new(Body::from(error_json(
                "not_found",
                "unknown endpoint",
                None,
            )));
            *r.status_mut() = StatusCode::NOT_FOUND;
            r.headers_mut()
                .insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
            r
        });
    }

    Ok(app.layer(cors_layer()))
}

/// Creates a TCP listener. With `reuse_port`, enables SO_REUSEPORT so that N
/// worker processes (multi-process mode) share the same port — the kernel
/// distributes connections among them.
pub(crate) fn make_listener(addr: SocketAddr, reuse_port: bool) -> anyhow::Result<tokio::net::TcpListener> {
    use socket2::{Domain, Protocol, Socket, Type};
    let domain = if addr.is_ipv6() { Domain::IPV6 } else { Domain::IPV4 };
    let socket = Socket::new(domain, Type::STREAM, Some(Protocol::TCP))?;
    socket.set_reuse_address(true)?;
    // SO_REUSEPORT (port sharing between worker processes) only exists on Unix.
    // On Windows, multi-process mode is not supported — we ignore the flag.
    #[cfg(unix)]
    if reuse_port {
        socket.set_reuse_port(true)?;
    }
    #[cfg(not(unix))]
    let _ = reuse_port;
    socket.bind(&addr.into())?;
    socket.listen(1024)?;
    socket.set_nonblocking(true)?;
    Ok(tokio::net::TcpListener::from_std(socket.into())?)
}

#[allow(clippy::too_many_arguments)]
pub async fn serve(
    endpoints: Vec<(Endpoint, HandlerKind)>,
    addr: SocketAddr,
    auth: Option<Arc<dyn AuthHandler>>,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
    shutting_down: Arc<AtomicBool>,
    statics: Vec<StaticRoute>,
    reuse_port: bool,
) -> anyhow::Result<()> {
    crate::observability::init_tracing();
    let count = endpoints.len();
    let n_statics = statics.len();
    let app = build_router(endpoints, auth, shutting_down, statics)?;
    let listener = make_listener(addr, reuse_port)?;
    tracing::info!(target: "vignemale::api", addr = %addr, endpoints = count, statics = n_statics, "server started");
    // Graceful shutdown: we stop accepting, in-flight requests finish.
    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            let _ = shutdown.changed().await;
            tracing::info!(target: "vignemale::api", "shutdown requested — draining in-flight requests");
        })
        .await?;
    tracing::info!(target: "vignemale::api", "server stopped (drain complete)");
    Ok(())
}
