// Un endpoint HTTP déclaré par l'application (focalisé : nom, méthode, chemin).
// Chez Encore, `endpoint.rs` porte bien plus (schémas requête/réponse, auth,
// exposition par gateway…) — on enrichira au besoin.

#[derive(Debug, Clone)]
pub struct Endpoint {
    pub name: String,
    pub method: String,
    pub path: String,
    /// L'accès exige l'authentification (le serveur passe par l'`AuthHandler`
    /// AVANT d'appeler le handler — et avant d'ouvrir le flux pour un stream).
    pub requires_auth: bool,
    /// Délai max de traitement (ms) ; `None` → défaut global
    /// (`VIGNEMALE_REQUEST_TIMEOUT`, 30 s ; 0 = désactivé). Ignoré en streaming.
    pub timeout_ms: Option<u64>,
    /// Taille max du body (octets) ; `None` → défaut global
    /// (`VIGNEMALE_MAX_BODY`, 10 Mio).
    pub body_limit: Option<u64>,
}
