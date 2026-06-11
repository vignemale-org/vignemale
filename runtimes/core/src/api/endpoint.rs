// Un endpoint HTTP déclaré par l'application (focalisé : nom, méthode, chemin).
// Chez Encore, `endpoint.rs` porte bien plus (schémas requête/réponse, auth,
// exposition par gateway…) — on enrichira au besoin.

#[derive(Debug, Clone)]
pub struct Endpoint {
    pub name: String,
    pub method: String,
    pub path: String,
}
