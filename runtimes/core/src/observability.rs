//! Observability — structured JSON logs on stderr, level via `VIGNEMALE_LOG`.
//!
//! Each HTTP request is logged by the server (method, path, endpoint,
//! status, duration, request_id); application errors attach their
//! traceback to it via the same request_id. Foundation for OTel export (phase 5).

use std::sync::Once;

static INIT: Once = Once::new();

/// Initializes the tracing subscriber (idempotent). Default level: `info`,
/// overridable with `VIGNEMALE_LOG` (EnvFilter syntax, e.g. `debug` or
/// `vignemale=trace`).
pub fn init_tracing() {
    INIT.call_once(|| {
        let filter = tracing_subscriber::EnvFilter::try_from_env("VIGNEMALE_LOG")
            .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
        let _ = tracing_subscriber::fmt()
            .json()
            .flatten_event(true)
            .with_env_filter(filter)
            .with_writer(std::io::stderr)
            .try_init();
    });
}

/// Generates a short request identifier (uuid v4 without dashes).
pub fn request_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}
