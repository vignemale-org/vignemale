// Détient les endpoints + leurs handlers (classiques ou streaming), et démarre
// le serveur. (Version focalisée du `manager.rs` d'Encore.)

use std::net::SocketAddr;

use super::server;
use super::{Endpoint, HandlerKind};

pub struct Manager {
    endpoints: Vec<(Endpoint, HandlerKind)>,
}

impl Manager {
    pub fn new() -> Self {
        Self {
            endpoints: Vec::new(),
        }
    }

    /// Enregistre un endpoint et son handler.
    pub fn register(&mut self, endpoint: Endpoint, handler: HandlerKind) {
        self.endpoints.push((endpoint, handler));
    }

    /// Démarre le serveur (boucle jusqu'à l'arrêt du process).
    pub async fn serve(self, addr: SocketAddr) -> anyhow::Result<()> {
        server::serve(self.endpoints, addr).await
    }
}

impl Default for Manager {
    fn default() -> Self {
        Self::new()
    }
}
