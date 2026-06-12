// Serveur HTTP focalisé (axum) : handlers classiques ET streaming (SSE).
//
// Les handlers sont appelés hors de l'exécuteur async (`spawn_blocking`) car le
// code app (Python) est bloquant et tient le GIL.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use axum::body::{Body, Bytes};
use axum::extract::rejection::BytesRejection;
use axum::extract::{DefaultBodyLimit, RawPathParams, RawQuery};
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

/// Requête transmise à un handler.
pub struct Request {
    pub params: Vec<(String, String)>,
    /// Paramètres de query string (`?q=midi&limit=3`), décodés.
    pub query: Vec<(String, String)>,
    /// En-têtes HTTP (noms en minuscules ; valeurs non-UTF-8 ignorées).
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
    /// Identifiant unique de la requête — renvoyé dans `x-vignemale-request-id`
    /// et présent dans chaque ligne de log qui la concerne.
    pub request_id: String,
    /// Données d'auth (JSON) si l'endpoint est protégé et le token validé.
    pub auth_data: Option<String>,
}

/// Réponse d'un handler classique.
pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}

/// Handler classique (requête → réponse).
pub trait Handler: Send + Sync + 'static {
    fn call(&self, req: Request) -> Response;
}

/// Puits d'écriture d'un handler streaming : chaque `write` devient un événement
/// SSE `data:`. À appeler hors contexte async (le handler tourne sur un thread
/// bloquant).
#[derive(Clone)]
pub struct StreamSink {
    tx: tokio::sync::mpsc::Sender<String>,
}

impl StreamSink {
    /// Pousse un fragment. Renvoie `false` si le flux est fermé (client parti).
    pub fn write(&self, chunk: String) -> bool {
        self.tx.blocking_send(chunk).is_ok()
    }
}

/// Handler streaming : pousse des fragments via `sink` au fil de l'eau.
pub trait StreamHandler: Send + Sync + 'static {
    fn call(&self, req: Request, sink: StreamSink);
}

/// Un handler, classique ou streaming.
pub enum HandlerKind {
    Unary(Arc<dyn Handler>),
    Stream(Arc<dyn StreamHandler>),
}

/// Fichiers statiques servis par le CORE (miroir du static_assets.rs
/// d'Encore) : zéro code app exécuté — un front (Next.js `output: 'export'`,
/// Vite…) est servi directement par le runtime Rust.
#[derive(Debug, Clone)]
pub struct StaticRoute {
    /// Préfixe d'URL ("/assets") ou "/" (devient la route fallback).
    pub path: String,
    /// Dossier servi.
    pub dir: String,
    /// Fichier renvoyé pour les chemins inconnus (SPA : index.html).
    pub not_found: Option<String>,
    /// Sert comme fallback (toute route non matchée par l'API).
    pub fallback: bool,
}

/// Résultat d'une tentative d'authentification.
pub enum AuthOutcome {
    /// Token valide — les données d'auth (JSON) sont transmises au handler.
    Authenticated(String),
    /// Refusé — statut + corps d'erreur à renvoyer tels quels.
    Denied { status: u16, body: Vec<u8> },
}

/// Handler d'authentification fourni par l'app (via le binding) : reçoit le
/// token, décide. Appelé par le serveur AVANT le handler de l'endpoint.
pub trait AuthHandler: Send + Sync + 'static {
    fn authenticate(&self, token: &str) -> AuthOutcome;
}

/// Extrait le token d'une requête : `Authorization: Bearer …` (ou valeur brute),
/// sinon `?token=` (clients sans en-têtes, ex. EventSource).
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

/// Joue l'authentification pour un endpoint protégé. `Ok(json)` = autorisé.
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
            body: error_json("internal", "endpoint protégé sans auth handler", None),
        });
    };
    let Some(token) = extract_token(headers, query) else {
        return Err(Response {
            status: 401,
            body: error_json("unauthenticated", "authentification requise", None),
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

/// Délai max de traitement effectif d'un endpoint (None = désactivé).
fn effective_timeout(ep_timeout_ms: Option<u64>) -> Option<std::time::Duration> {
    let ms = ep_timeout_ms.unwrap_or_else(|| env_u64("VIGNEMALE_REQUEST_TIMEOUT", 30) * 1000);
    (ms > 0).then(|| std::time::Duration::from_millis(ms))
}

/// Valide le body extrait ; un dépassement de `body_limit` → 413 structuré.
fn check_body(
    body: Result<Bytes, BytesRejection>,
) -> Result<Bytes, Response> {
    body.map_err(|_| Response {
        status: 413,
        body: error_json("resource_exhausted", "corps de requête trop volumineux", None),
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

/// Contexte de trace W3C : reprend le `traceparent` entrant s'il est valide,
/// sinon en crée un. Renvoie (trace_id, traceparent).
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
    // toujours présent (repris ou généré) → l'app peut le propager via call()
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

/// Couche CORS : tout ouvert par défaut (dev) ; `VIGNEMALE_CORS_ALLOW_ORIGINS`
/// (liste d'origines séparées par des virgules, ou `*`) restreint en prod.
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
            "requête en erreur"
        );
    } else {
        tracing::info!(
            target: "vignemale::api",
            endpoint, method, path, status, duration_ms = ms, request_id = id,
            trace_id,
            "requête traitée"
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

    // Index des endpoints unaires pour les appels service-à-service signés.
    let mut internal: std::collections::HashMap<String, (Arc<dyn Handler>, bool)> =
        std::collections::HashMap::new();
    for (ep, kind) in &endpoints {
        if let HandlerKind::Unary(h) = kind {
            internal.insert(ep.name.clone(), (h.clone(), ep.requires_auth));
        }
    }

    let mut app = Router::new();
    for (ep, kind) in endpoints {
        let filter = method_filter(&ep.method)
            .ok_or_else(|| anyhow::anyhow!("méthode HTTP non supportée: {}", ep.method))?;
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
                          headers: HeaderMap,
                          body: Result<Bytes, BytesRejection>| {
                        let handler = handler.clone();
                        let auth = auth.clone();
                        let (name, method, path) = (name.clone(), method.clone(), path.clone());
                        async move {
                            let request_id = crate::observability::request_id();
                            let started = std::time::Instant::now();
                            let (trace_id, traceparent) = trace_context(&headers);
                            let auth_data = if requires_auth {
                                match run_auth(&auth, &headers, &query).await {
                                    Ok(data) => data,
                                    Err(denied) => {
                                        log_request(
                                            &name,
                                            &method,
                                            &path,
                                            denied.status,
                                            started.elapsed().as_millis() as u64,
                                            &request_id,
                                            &trace_id,
                                        );
                                        return deny_response(denied, &request_id);
                                    }
                                }
                            } else {
                                None
                            };
                            let body = match check_body(body) {
                                Ok(b) => b,
                                Err(denied) => {
                                    log_request(
                                        &name,
                                        &method,
                                        &path,
                                        denied.status,
                                        started.elapsed().as_millis() as u64,
                                        &request_id,
                                        &trace_id,
                                    );
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
                            // le handler bloquant part en arrière-plan ; au-delà
                            // du délai on répond 504 (le handler finit, ses logs
                            // sont conservés — façon CancellationGuard d'Encore)
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
                                            "délai de traitement dépassé",
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
                          headers: HeaderMap,
                          body: Result<Bytes, BytesRejection>| {
                        let handler = handler.clone();
                        let auth = auth.clone();
                        let (name, method, path) = (name.clone(), method.clone(), path.clone());
                        async move {
                            let request_id = crate::observability::request_id();
                            let started = std::time::Instant::now();
                            let (trace_id, traceparent) = trace_context(&headers);
                            // l'auth se joue AVANT d'ouvrir le flux → vrai 401
                            let auth_data = if requires_auth {
                                match run_auth(&auth, &headers, &query).await {
                                    Ok(data) => data,
                                    Err(denied) => {
                                        log_request(
                                            &name,
                                            &method,
                                            &path,
                                            denied.status,
                                            started.elapsed().as_millis() as u64,
                                            &request_id,
                                            &trace_id,
                                        );
                                        return deny_response(denied, &request_id);
                                    }
                                }
                            } else {
                                None
                            };
                            let body = match check_body(body) {
                                Ok(b) => b,
                                Err(denied) => {
                                    log_request(
                                        &name,
                                        &method,
                                        &path,
                                        denied.status,
                                        started.elapsed().as_millis() as u64,
                                        &request_id,
                                        &trace_id,
                                    );
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
                            // Le handler (bloquant) pousse des fragments via `sink` ;
                            // on logge à la fin du flux (durée = vie du handler).
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

    // Appels service-à-service : route interne signée (HMAC, secret partagé
    // VIGNEMALE_SERVICE_SECRET). Le payload est {"params": {...}, "body": …} ;
    // les données d'auth de l'appelant arrivent propagées dans
    // `x-vignemale-auth-data`, le contexte de trace dans `traceparent`.
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
                        let mut finish = |resp: Response| {
                            tracing::info!(
                                target: "vignemale::api",
                                endpoint = %endpoint, caller = %caller,
                                status = resp.status,
                                duration_ms = started.elapsed().as_millis() as u64,
                                request_id = %request_id, trace_id = %trace_id,
                                "appel interne traité"
                            );
                            deny_response(resp, &request_id)
                        };

                        // 1) signature obligatoire
                        let Ok(secret) = std::env::var("VIGNEMALE_SERVICE_SECRET") else {
                            return finish(Response {
                                status: 401,
                                body: error_json(
                                    "unauthenticated",
                                    "appels inter-services non configurés (VIGNEMALE_SERVICE_SECRET)",
                                    None,
                                ),
                            });
                        };
                        if let Err(reason) = super::svcauth::verify(
                            &secret,
                            &hdr("x-vignemale-date"),
                            &caller,
                            &endpoint,
                            &body,
                            &hdr("x-vignemale-signature"),
                            super::svcauth::now_epoch(),
                        ) {
                            return finish(Response {
                                status: 401,
                                body: error_json("unauthenticated", reason, None),
                            });
                        }

                        // 2) endpoint cible
                        let Some((handler, requires_auth)) = internal.get(&endpoint).cloned()
                        else {
                            return finish(Response {
                                status: 404,
                                body: error_json("not_found", "endpoint interne inconnu", None),
                            });
                        };

                        // 3) payload {"params": {...}, "body": …}
                        let Ok(payload) = serde_json::from_slice::<serde_json::Value>(&body)
                        else {
                            return finish(Response {
                                status: 400,
                                body: error_json("invalid_argument", "payload invalide", None),
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

                        // 4) auth propagée (les appels internes sont de confiance :
                        //    pas de re-passage par l'auth handler, façon Encore)
                        let auth_header = hdr("x-vignemale-auth-data");
                        let auth_data = if requires_auth {
                            if auth_header.is_empty() {
                                return finish(Response {
                                    status: 401,
                                    body: error_json(
                                        "unauthenticated",
                                        "endpoint protégé : données d'auth non propagées",
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

    // Routes internes : health check (pour les load balancers / containers).
    // Pendant l'arrêt gracieux → 503 shutting_down (l'orchestrateur sait).
    app = app.route(
        "/__vignemale/healthz",
        get(move || async move {
            let (status, body) = if shutting_down.load(Ordering::SeqCst) {
                (
                    StatusCode::SERVICE_UNAVAILABLE,
                    error_json("shutting_down", "arrêt en cours", None),
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

    // Fichiers statiques : servis par tower-http directement (zéro app code).
    let mut has_static_fallback = false;
    for s in &statics {
        use tower_http::services::{ServeDir, ServeFile};
        // `.fallback(...)` (et pas `not_found_service`, qui force un 404) :
        // une SPA doit renvoyer index.html en 200 pour le routing client.
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

    // Route inconnue → 404 structuré (sauf si un front sert le fallback).
    if !has_static_fallback {
        app = app.fallback(|| async {
            let mut r = AxumResponse::new(Body::from(error_json(
                "not_found",
                "endpoint inconnu",
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

/// Crée un listener TCP. Avec `reuse_port`, active SO_REUSEPORT pour que N
/// process worker (mode multi-process) partagent le même port — le noyau
/// répartit les connexions entre eux.
pub(crate) fn make_listener(addr: SocketAddr, reuse_port: bool) -> anyhow::Result<tokio::net::TcpListener> {
    use socket2::{Domain, Protocol, Socket, Type};
    let domain = if addr.is_ipv6() { Domain::IPV6 } else { Domain::IPV4 };
    let socket = Socket::new(domain, Type::STREAM, Some(Protocol::TCP))?;
    socket.set_reuse_address(true)?;
    if reuse_port {
        socket.set_reuse_port(true)?;
    }
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
    tracing::info!(target: "vignemale::api", addr = %addr, endpoints = count, statics = n_statics, "serveur démarré");
    // Arrêt gracieux : on cesse d'accepter, les requêtes en vol terminent.
    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            let _ = shutdown.changed().await;
            tracing::info!(target: "vignemale::api", "arrêt demandé — drain des requêtes en vol");
        })
        .await?;
    tracing::info!(target: "vignemale::api", "serveur arrêté (drain terminé)");
    Ok(())
}
