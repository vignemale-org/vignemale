//! Observabilité — logs structurés JSON sur stderr, niveau via `VIGNEMALE_LOG`.
//!
//! Chaque requête HTTP est loggée par le serveur (méthode, chemin, endpoint,
//! statut, durée, request_id) ; les erreurs applicatives y rattachent leur
//! traceback via le même request_id. Fondation pour l'export OTel (phase 5).

use std::sync::Once;

static INIT: Once = Once::new();

/// Initialise le subscriber tracing (idempotent). Niveau par défaut : `info`,
/// surchargable avec `VIGNEMALE_LOG` (syntaxe EnvFilter, ex. `debug` ou
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

/// Génère un identifiant de requête court (uuid v4 sans tirets).
pub fn request_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}
