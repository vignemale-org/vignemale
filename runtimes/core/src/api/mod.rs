//! API framework — HTTP server, routing, bridge to the app's handlers.
//!
//! **Focused** version, structure modeled on `encore/runtimes/core/src/api`
//! (`mod` / `endpoint` / `server` / `manager`), but WITHOUT Encore's complete
//! subsystem (schema · jsonschema · cors · reqauth · gateway · auth · websocket…).
//! We will add these submodules progressively.
//!
//! The core defines the server + the `Handler` trait; the **binding** (PyO3)
//! implements `Handler` to call the Python handler — just as `runtimes/js`
//! does on the JS side in Encore.

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
