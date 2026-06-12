//! Framework API — serveur HTTP, routing, pont vers les handlers de l'app.
//!
//! Version **focalisée**, structure calquée sur `encore/runtimes/core/src/api`
//! (`mod` / `endpoint` / `server` / `manager`), mais SANS le subsystem complet
//! d'Encore (schema · jsonschema · cors · reqauth · gateway · auth · websocket…).
//! On ajoutera ces sous-modules au fur et à mesure.
//!
//! Le core définit le serveur + le trait `Handler` ; le **binding** (PyO3)
//! implémente `Handler` pour appeler le handler Python — comme `runtimes/js`
//! le fait côté JS chez Encore.

mod endpoint;
mod error;
pub mod gateway;
mod manager;
mod server;
pub mod svcauth;

pub use endpoint::Endpoint;
pub use error::error_json;
pub use gateway::GatewayRoute;
pub use manager::Manager;
pub use server::{
    AuthHandler, AuthOutcome, Handler, HandlerKind, Request, Response, StaticRoute,
    StreamHandler, StreamSink,
};
