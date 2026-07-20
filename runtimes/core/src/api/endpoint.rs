// An HTTP endpoint declared by the application (focused: name, method, path).
// In Encore, `endpoint.rs` carries much more (request/response schemas, auth,
// gateway exposure…) — we will enrich it as needed.

#[derive(Debug, Clone)]
pub struct Endpoint {
    pub name: String,
    pub method: String,
    pub path: String,
    /// Access requires authentication (the server goes through the `AuthHandler`
    /// BEFORE calling the handler — and before opening the flow for a stream).
    pub requires_auth: bool,
    /// Exposed to public traffic. If `false` (PRIVATE), the endpoint is NOT
    /// routed publicly — it remains reachable only service-to-service
    /// via the signed internal route `/__vignemale/call/` (like Encore's
    /// `expose:false`: an external caller gets a 404).
    pub expose: bool,
    /// Max processing time (ms); `None` -> global default
    /// (`VIGNEMALE_REQUEST_TIMEOUT`, 30 s; 0 = disabled). Ignored for streaming.
    pub timeout_ms: Option<u64>,
    /// Max body size (bytes); `None` -> global default
    /// (`VIGNEMALE_MAX_BODY`, 10 MiB).
    pub body_limit: Option<u64>,
}
