// Modèle d'erreur API (focalisé) — même contrat qu'Encore : un corps JSON
// `{code, message, details}` avec des codes gRPC-style mappés sur les statuts
// HTTP. La table complète vit dans le SDK (api.py) ; ici, le minimum que le
// serveur émet lui-même (404 de routing, 500 internes, panics).

/// Construit le corps JSON d'une erreur API.
pub fn error_json(code: &str, message: &str, details: Option<serde_json::Value>) -> Vec<u8> {
    serde_json::json!({
        "code": code,
        "message": message,
        "details": details,
    })
    .to_string()
    .into_bytes()
}
