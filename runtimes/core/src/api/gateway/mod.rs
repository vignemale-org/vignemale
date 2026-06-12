// Gateway — l'entrée unique d'une app multi-services déployée (miroir du
// gateway/ d'Encore, moteur axum au lieu de Pingora).
//
// Reçoit le trafic public, **authentifie à l'edge** (via l'AuthHandler du
// core), **route par préfixe de path** (`routing`) vers le bon service, et
// **forwarde en HTTP signé svcauth** (`proxy`) avec l'auth et le trace-id W3C
// propagés. Les services backend n'exposent ainsi que la route interne signée.

mod proxy;
mod routing;

use std::net::SocketAddr;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use axum::Router;

use super::AuthHandler;

/// Une route de la gateway : un préfixe de path → l'URL d'un service backend.
#[derive(Debug, Clone)]
pub struct GatewayRoute {
    /// Préfixe d'URL public (ex. "/orders").
    pub prefix: String,
    /// Nom du service (pour les logs et le caller svcauth).
    pub service: String,
    /// URL de base du service backend (ex. "http://orders:8080").
    pub upstream: String,
    /// Les requêtes sous ce préfixe exigent l'authentification.
    pub requires_auth: bool,
}

pub(crate) struct GwState {
    pub routes: Vec<GatewayRoute>,
    pub auth: Option<Arc<dyn AuthHandler>>,
    pub secret: Option<String>,
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
    // préfixe le plus long d'abord ("/" en dernier) — cf. routing::pick_route
    routes.sort_by(|a, b| b.prefix.len().cmp(&a.prefix.len()));
    let n = routes.len();
    let state = Arc::new(GwState {
        routes,
        auth,
        secret: std::env::var("VIGNEMALE_SERVICE_SECRET").ok(),
        client: reqwest::Client::new(),
    });
    let app = Router::new()
        .fallback(proxy::handle)
        .with_state(state)
        .layer(super::server::cors_layer_pub());
    let listener = super::server::make_listener(addr, reuse_port)?;
    tracing::info!(target: "vignemale::gateway", addr = %addr, services = n, "gateway démarrée");
    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            let _ = shutdown.changed().await;
            tracing::info!(target: "vignemale::gateway", "arrêt — drain");
        })
        .await?;
    Ok(())
}
