// Détient les endpoints + leurs handlers (classiques ou streaming) et
// l'éventuel auth handler, et démarre le serveur. (Version focalisée du
// `manager.rs` d'Encore.)

use std::net::SocketAddr;
use std::sync::Arc;

use super::server::{self, AuthHandler};
use super::{Endpoint, HandlerKind};

pub struct Manager {
    endpoints: Vec<(Endpoint, HandlerKind)>,
    auth: Option<Arc<dyn AuthHandler>>,
}

impl Manager {
    pub fn new() -> Self {
        Self {
            endpoints: Vec::new(),
            auth: None,
        }
    }

    /// Enregistre un endpoint et son handler.
    pub fn register(&mut self, endpoint: Endpoint, handler: HandlerKind) {
        self.endpoints.push((endpoint, handler));
    }

    /// Déclare l'auth handler de l'app (appelé pour les endpoints protégés).
    pub fn set_auth_handler(&mut self, auth: Arc<dyn AuthHandler>) {
        self.auth = Some(auth);
    }

    /// Démarre le serveur (boucle jusqu'à l'arrêt du process).
    pub async fn serve(self, addr: SocketAddr) -> anyhow::Result<()> {
        server::serve(self.endpoints, addr, self.auth).await
    }
}

impl Default for Manager {
    fn default() -> Self {
        Self::new()
    }
}
