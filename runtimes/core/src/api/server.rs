// Serveur HTTP focalisé (axum) : handlers classiques ET streaming (SSE).
//
// Les handlers sont appelés hors de l'exécuteur async (`spawn_blocking`) car le
// code app (Python) est bloquant et tient le GIL.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use axum::body::{Body, Bytes};
use axum::extract::{RawPathParams, RawQuery};
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

fn make_request(
    params: RawPathParams,
    query: Option<String>,
    headers: &HeaderMap,
    body: Bytes,
    request_id: String,
    auth_data: Option<String>,
) -> Request {
    Request {
        params: params
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect(),
        query: form_urlencoded::parse(query.unwrap_or_default().as_bytes())
            .map(|(k, v)| (k.into_owned(), v.into_owned()))
            .collect(),
        headers: headers
            .iter()
            .filter_map(|(k, v)| {
                v.to_str()
                    .ok()
                    .map(|v| (k.as_str().to_lowercase(), v.to_string()))
            })
            .collect(),
        body: body.to_vec(),
        request_id,
        auth_data,
    }
}

/// Couche CORS : tout ouvert par défaut (dev) ; `VIGNEMALE_CORS_ALLOW_ORIGINS`
/// (liste d'origines séparées par des virgules, ou `*`) restreint en prod.
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

fn log_request(endpoint: &str, method: &str, path: &str, status: u16, ms: u64, id: &str) {
    if status >= 500 {
        tracing::error!(
            target: "vignemale::api",
            endpoint, method, path, status, duration_ms = ms, request_id = id,
            "requête en erreur"
        );
    } else {
        tracing::info!(
            target: "vignemale::api",
            endpoint, method, path, status, duration_ms = ms, request_id = id,
            "requête traitée"
        );
    }
}

pub fn build_router(
    endpoints: Vec<(Endpoint, HandlerKind)>,
    auth: Option<Arc<dyn AuthHandler>>,
) -> anyhow::Result<Router> {
    let mut app = Router::new();
    for (ep, kind) in endpoints {
        let filter = method_filter(&ep.method)
            .ok_or_else(|| anyhow::anyhow!("méthode HTTP non supportée: {}", ep.method))?;
        let (name, method, path) = (Arc::<str>::from(ep.name), ep.method, ep.path);
        let route_path = path.clone();
        let path: Arc<str> = Arc::from(path);
        let method: Arc<str> = Arc::from(method);
        let requires_auth = ep.requires_auth;
        let auth = auth.clone();
        app = match kind {
            HandlerKind::Unary(handler) => app.route(
                &route_path,
                on(
                    filter,
                    move |params: RawPathParams,
                          RawQuery(query): RawQuery,
                          headers: HeaderMap,
                          body: Bytes| {
                        let handler = handler.clone();
                        let auth = auth.clone();
                        let (name, method, path) = (name.clone(), method.clone(), path.clone());
                        async move {
                            let request_id = crate::observability::request_id();
                            let started = std::time::Instant::now();
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
                                        );
                                        return deny_response(denied, &request_id);
                                    }
                                }
                            } else {
                                None
                            };
                            let req = make_request(
                                params,
                                query,
                                &headers,
                                body,
                                request_id.clone(),
                                auth_data,
                            );
                            let resp = tokio::task::spawn_blocking(move || handler.call(req))
                                .await
                                .unwrap_or(Response {
                                    status: 500,
                                    body: error_json("internal", "handler panicked", None),
                                });
                            log_request(
                                &name,
                                &method,
                                &path,
                                resp.status,
                                started.elapsed().as_millis() as u64,
                                &request_id,
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
                ),
            ),
            HandlerKind::Stream(handler) => app.route(
                &route_path,
                on(
                    filter,
                    move |params: RawPathParams,
                          RawQuery(query): RawQuery,
                          headers: HeaderMap,
                          body: Bytes| {
                        let handler = handler.clone();
                        let auth = auth.clone();
                        let (name, method, path) = (name.clone(), method.clone(), path.clone());
                        async move {
                            let request_id = crate::observability::request_id();
                            let started = std::time::Instant::now();
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
                                        );
                                        return deny_response(denied, &request_id);
                                    }
                                }
                            } else {
                                None
                            };
                            let req = make_request(
                                params,
                                query,
                                &headers,
                                body,
                                request_id.clone(),
                                auth_data,
                            );
                            let (tx, rx) = tokio::sync::mpsc::channel::<String>(64);
                            let sink = StreamSink { tx };
                            // Le handler (bloquant) pousse des fragments via `sink` ;
                            // on logge à la fin du flux (durée = vie du handler).
                            let log_id = request_id.clone();
                            tokio::task::spawn_blocking(move || {
                                handler.call(req, sink);
                                log_request(
                                    &name,
                                    &method,
                                    &path,
                                    200,
                                    started.elapsed().as_millis() as u64,
                                    &log_id,
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
                ),
            ),
        };
    }

    // Routes internes : health check (pour les load balancers / containers).
    app = app.route(
        "/__vignemale/healthz",
        get(|| async {
            let mut r = AxumResponse::new(Body::from(error_json("ok", "vignemale up", None)));
            r.headers_mut()
                .insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
            r
        }),
    );

    // Route inconnue → 404 structuré (même contrat d'erreur que le reste).
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

    Ok(app.layer(cors_layer()))
}

pub async fn serve(
    endpoints: Vec<(Endpoint, HandlerKind)>,
    addr: SocketAddr,
    auth: Option<Arc<dyn AuthHandler>>,
) -> anyhow::Result<()> {
    crate::observability::init_tracing();
    let count = endpoints.len();
    let app = build_router(endpoints, auth)?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!(target: "vignemale::api", addr = %addr, endpoints = count, "serveur démarré");
    axum::serve(listener, app).await?;
    Ok(())
}
