// Route selection: longest prefix, on a segment boundary.

use super::GatewayRoute;

/// Finds the service for a path. The routes are sorted (longest prefix
/// first) at construction; `/` is the final catch-all.
pub(crate) fn pick_route<'a>(routes: &'a [GatewayRoute], path: &str) -> Option<&'a GatewayRoute> {
    routes.iter().find(|r| {
        r.prefix == "/"
            || path == r.prefix
            || path.starts_with(&format!("{}/", r.prefix))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn routes() -> Vec<GatewayRoute> {
        let mut r = vec![
            GatewayRoute {
                prefix: "/".into(),
                service: "web".into(),
                upstream: "http://web".into(),
                requires_auth: false,
            },
            GatewayRoute {
                prefix: "/orders".into(),
                service: "orders".into(),
                upstream: "http://orders".into(),
                requires_auth: true,
            },
        ];
        r.sort_by(|a, b| b.prefix.len().cmp(&a.prefix.len()));
        r
    }

    #[test]
    fn longest_prefix_wins() {
        let r = routes();
        assert_eq!(pick_route(&r, "/orders/42").unwrap().service, "orders");
        assert_eq!(pick_route(&r, "/orders").unwrap().service, "orders");
        assert_eq!(pick_route(&r, "/other").unwrap().service, "web"); // catch-all
        // segment boundary: "/ordersxyz" does NOT match "/orders"
        assert_eq!(pick_route(&r, "/ordersxyz").unwrap().service, "web");
    }

    #[test]
    fn no_catch_all_means_404() {
        let r = vec![GatewayRoute {
            prefix: "/api".into(),
            service: "api".into(),
            upstream: "http://api".into(),
            requires_auth: false,
        }];
        assert!(pick_route(&r, "/other").is_none());
    }
}
