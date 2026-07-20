// API error model (focused) — same contract as Encore: a JSON body
// `{code, message, details}` with gRPC-style codes mapped onto HTTP status
// codes. The full table lives in the SDK (api.py); here, the minimum the
// server emits itself (routing 404s, internal 500s, panics).

/// Builds the JSON body of an API error.
pub fn error_json(code: &str, message: &str, details: Option<serde_json::Value>) -> Vec<u8> {
    serde_json::json!({
        "code": code,
        "message": message,
        "details": details,
    })
    .to_string()
    .into_bytes()
}
