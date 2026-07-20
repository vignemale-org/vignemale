// Gateway — the single entry point of a deployed multi-service app (mirror of
// Encore's gateway/, with an axum engine instead of Pingora).
//
// Receives the public traffic, **authenticates at the edge** (via the core's
// AuthHandler), **routes by path prefix** (`routing`) to the right service, and
// **forwards as svcauth-signed HTTP** (`proxy`) with the auth and the W3C trace-id
// propagated. The backend services thus only expose the signed internal route.

mod proxy;
mod routing;

use std::net::SocketAddr;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use axum::Router;

use super::AuthHandler;

/// A gateway route: a path prefix -> the URL of a backend service.
#[derive(Debug, Clone)]
pub struct GatewayRoute {
    /// Public URL prefix (e.g. "/orders").
    pub prefix: String,
    /// Service name (for the logs and the svcauth caller).
    pub service: String,
    /// Base URL of the backend service (e.g. "http://orders:8080").
    pub upstream: String,
    /// Requests under this prefix require authentication.
    pub requires_auth: bool,
}

pub(crate) struct GwState {
    pub routes: Vec<GatewayRoute>,
    pub auth: Option<Arc<dyn AuthHandler>>,
    pub secret: Option<String>,
    /// Invocation token for private containers (X-Auth-Token) — services topology.
    pub container_token: Option<String>,
    pub client: reqwest::Client,
}

pub async fn serve(
    mut routes: Vec<GatewayRoute>,
    addr: SocketAddr,
    auth: Option<Arc<dyn AuthHandler>>,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
    _shutting_down: Arc<AtomicBool>,
    reuse_port: bool,
) -> anyhow::Result<()> {
    crate::observability::init_tracing();
    // longest prefix first ("/" last) — cf. routing::pick_route
    routes.sort_by(|a, b| b.prefix.len().cmp(&a.prefix.len()));
    let n = routes.len();
    let state = Arc::new(GwState {
        routes,
        auth,
        secret: std::env::var("VIGNEMALE_SERVICE_SECRET").ok(),
        container_token: std::env::var("VIGNEMALE_CONTAINER_TOKEN").ok().filter(|s| !s.is_empty()),
        client: reqwest::Client::new(),
    });
    let app = Router::new()
        .fallback(proxy::handle)
        .with_state(state)
        .layer(super::server::cors_layer_pub());
    let listener = super::server::make_listener(addr, reuse_port)?;
    tracing::info!(target: "vignemale::gateway", addr = %addr, services = n, "gateway started");
    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            let _ = shutdown.changed().await;
            tracing::info!(target: "vignemale::gateway", "shutting down — draining");
        })
        .await?;
    Ok(())
}
