// Holds the endpoints + their handlers (classic or streaming) and
// the optional auth handler, and starts the server. (Focused version of
// Encore's `manager.rs`.)

use std::net::SocketAddr;
use std::sync::Arc;

use super::server::{self, AuthHandler, StaticRoute};
use super::{Endpoint, HandlerKind};

pub struct Manager {
    endpoints: Vec<(Endpoint, HandlerKind)>,
    auth: Option<Arc<dyn AuthHandler>>,
    statics: Vec<StaticRoute>,
}

impl Manager {
    pub fn new() -> Self {
        Self {
            endpoints: Vec::new(),
            auth: None,
            statics: Vec::new(),
        }
    }

    /// Registers an endpoint and its handler.
    pub fn register(&mut self, endpoint: Endpoint, handler: HandlerKind) {
        self.endpoints.push((endpoint, handler));
    }

    /// Declares the app's auth handler (called for protected endpoints).
    pub fn set_auth_handler(&mut self, auth: Arc<dyn AuthHandler>) {
        self.auth = Some(auth);
    }

    /// Declares a static files directory served by the core.
    pub fn add_static(&mut self, route: StaticRoute) {
        self.statics.push(route);
    }

    /// Starts the server. Shuts down gracefully (draining in-flight requests)
    /// when `shutdown` flips to `true`; `shutting_down` drives the healthz (503).
    #[allow(clippy::too_many_arguments)]
    pub async fn serve(
        self,
        addr: SocketAddr,
        shutdown: tokio::sync::watch::Receiver<bool>,
        shutting_down: std::sync::Arc<std::sync::atomic::AtomicBool>,
        reuse_port: bool,
    ) -> anyhow::Result<()> {
        server::serve(self.endpoints, addr, self.auth, shutdown, shutting_down, self.statics, reuse_port).await
    }
}

impl Default for Manager {
    fn default() -> Self {
        Self::new()
    }
}
