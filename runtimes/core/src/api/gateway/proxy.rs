// The proxy handler: auth at the edge -> svcauth-signed forward -> streamed
// response. All public traffic goes through here (the gateway's fallback route).

use std::sync::Arc;

use axum::body::{Body, Bytes};
use axum::extract::{RawQuery, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode, Uri};
use axum::response::Response as AxumResponse;

use super::super::error::error_json;
use super::super::svcauth;
use super::routing::pick_route;
use super::GwState;

pub(crate) async fn handle(
    State(st): State<Arc<GwState>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    body: Bytes,
) -> AxumResponse {
    let request_id = crate::observability::request_id();
    let started = std::time::Instant::now();
    let path = uri.path().to_string();
    let (trace_id, traceparent) = super::super::server::trace_context_pub(&headers);

    let err = |status: u16, code: &str, msg: &str| reply(status, error_json(code, msg, None), &request_id);

    let Some(route) = pick_route(&st.routes, &path) else {
        return err(404, "not_found", "no service for this path");
    };

    // 1) auth at the edge
    let mut auth_data: Option<String> = None;
    if route.requires_auth {
        match super::super::server::run_auth_pub(&st.auth, &headers, &query).await {
            Ok(data) => auth_data = data,
            Err(resp) => {
                log_routed(&route.service, &method, &path, resp.status, started, &request_id, &trace_id);
                return reply(resp.status, resp.body, &request_id);
            }
        }
    }

    // 2) signed forward to the backend service
    let Some(secret) = &st.secret else {
        return err(500, "internal", "VIGNEMALE_SERVICE_SECRET required for the gateway");
    };
    let date = svcauth::now_epoch().to_string();
    // the propagated identity is signed exactly as the header (empty if absent)
    let sig = svcauth::sign(
        secret,
        &date,
        "gateway",
        &path,
        &body,
        auth_data.as_deref().unwrap_or("").as_bytes(),
    );
    let url = format!(
        "{}{}{}",
        route.upstream.trim_end_matches('/'),
        &path,
        query.map(|q| format!("?{q}")).unwrap_or_default()
    );

    let mut req = st
        .client
        .request(method.clone(), &url)
        .body(body.to_vec())
        .header("x-vignemale-date", &date)
        .header("x-vignemale-caller", "gateway")
        .header("x-vignemale-signature", &sig)
        .header("traceparent", &traceparent);
    if let Some(data) = &auth_data {
        req = req.header("x-vignemale-auth-data", data);
    }
    // private backends: Scaleway invocation token (otherwise 403 at the cloud edge)
    if let Some(token) = &st.container_token {
        req = req.header("X-Auth-Token", token);
    }
    for (k, v) in headers.iter() {
        let name = k.as_str();
        if !is_hop_by_hop(name) && !name.starts_with("x-vignemale-") && name != "traceparent" {
            req = req.header(k, v);
        }
    }

    match req.send().await {
        Ok(resp) => {
            let status = resp.status();
            let mut builder = AxumResponse::builder().status(status);
            for (k, v) in resp.headers().iter() {
                if !is_hop_by_hop(k.as_str()) {
                    builder = builder.header(k, v);
                }
            }
            log_routed(&route.service, &method, &path, status.as_u16(), started, &request_id, &trace_id);
            builder
                .header("x-vignemale-request-id", &request_id)
                .body(Body::from_stream(resp.bytes_stream()))
                .unwrap_or_else(|_| reply(502, error_json("unavailable", "invalid upstream response", None), &request_id))
        }
        Err(e) => {
            log_routed(&route.service, &method, &path, 502, started, &request_id, &trace_id);
            tracing::error!(target: "vignemale::gateway", service = %route.service, error = %e, "upstream unreachable");
            err(502, "unavailable", &format!("service {} unreachable", route.service))
        }
    }
}

fn reply(status: u16, body: Vec<u8>, request_id: &str) -> AxumResponse {
    let mut r = AxumResponse::new(Body::from(body));
    *r.status_mut() = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    r.headers_mut().insert(
        axum::http::header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    if let Ok(v) = HeaderValue::from_str(request_id) {
        r.headers_mut().insert("x-vignemale-request-id", v);
    }
    r
}

fn is_hop_by_hop(name: &str) -> bool {
    matches!(
        name,
        "connection" | "keep-alive" | "proxy-authenticate" | "proxy-authorization"
            | "te" | "trailers" | "transfer-encoding" | "upgrade" | "host" | "content-length"
    )
}

#[allow(clippy::too_many_arguments)]
fn log_routed(
    service: &str,
    method: &Method,
    path: &str,
    status: u16,
    started: std::time::Instant,
    request_id: &str,
    trace_id: &str,
) {
    tracing::info!(
        target: "vignemale::gateway",
        service, method = %method, path, status,
        duration_ms = started.elapsed().as_millis() as u64,
        request_id, trace_id,
        "request routed"
    );
}
