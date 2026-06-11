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
}
