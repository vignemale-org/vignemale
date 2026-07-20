// svcauth — authentication of service-to-service calls (like Encore's
// EncoreAuth, simplified): HMAC-SHA256 over (date, caller, endpoint, body
// hash, propagated-identity hash) with an anti-replay clock check. The
// shared secret comes from `VIGNEMALE_SERVICE_SECRET` (set by the deploy;
// never exposed to clients).
//
// `auth_data` (= the `x-vignemale-auth-data` header, the caller's propagated
// identity) is INCLUDED in the signature, just as Encore binds UserId/UserData
// into its OperationHash: without that, a service holding the secret could
// impersonate another user's identity on an internal call.

use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};

/// Clock tolerance between services (seconds).
pub const MAX_SKEW_SECS: i64 = 120;

/// Signs an internal call. `date` = epoch (seconds) as text. `auth_data` =
/// the propagated identity (bytes of `x-vignemale-auth-data`, empty if absent).
pub fn sign(
    secret: &str,
    date: &str,
    caller: &str,
    endpoint: &str,
    body: &[u8],
    auth_data: &[u8],
) -> String {
    let body_hash = hex::encode(Sha256::digest(body));
    let auth_hash = hex::encode(Sha256::digest(auth_data));
    let msg = format!("{date}\n{caller}\n{endpoint}\n{body_hash}\n{auth_hash}");
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("HMAC key");
    mac.update(msg.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Secrets accepted for verification, from the environment: the current secret
/// (`VIGNEMALE_SERVICE_SECRET`) + any previous ones
/// (`VIGNEMALE_SERVICE_SECRET_PREVIOUS`, comma-separated). Enables
/// **zero-downtime rotation**: deploy the new one as current while keeping
/// the old one as "previous", then remove the old one once all
/// services are up to date. (Encore uses keys with a rotating `key_id`; we simplify
/// by accepting a set of secrets — the signer always uses the current one.)
pub fn accepted_secrets_from_env() -> Vec<String> {
    let mut secrets = Vec::new();
    if let Ok(s) = std::env::var("VIGNEMALE_SERVICE_SECRET") {
        if !s.is_empty() {
            secrets.push(s);
        }
    }
    if let Ok(prev) = std::env::var("VIGNEMALE_SERVICE_SECRET_PREVIOUS") {
        for s in prev.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            secrets.push(s.to_string());
        }
    }
    secrets
}

/// Compares two hex signatures in constant time.
fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Verifies the signature against ONE secret (constant-time comparison).
#[allow(clippy::too_many_arguments)]
pub fn verify(
    secret: &str,
    date: &str,
    caller: &str,
    endpoint: &str,
    body: &[u8],
    auth_data: &[u8],
    signature: &str,
    now_epoch: i64,
) -> Result<(), &'static str> {
    verify_any(
        std::slice::from_ref(&secret),
        date,
        caller,
        endpoint,
        body,
        auth_data,
        signature,
        now_epoch,
    )
}

/// Verifies against a SET of secrets (current + previous) — for rotation.
#[allow(clippy::too_many_arguments)]
pub fn verify_any<S: AsRef<str>>(
    secrets: &[S],
    date: &str,
    caller: &str,
    endpoint: &str,
    body: &[u8],
    auth_data: &[u8],
    signature: &str,
    now_epoch: i64,
) -> Result<(), &'static str> {
    let ts: i64 = date.parse().map_err(|_| "invalid date")?;
    if (now_epoch - ts).abs() > MAX_SKEW_SECS {
        return Err("date out of tolerance (replay?)");
    }
    for secret in secrets {
        let expected = sign(secret.as_ref(), date, caller, endpoint, body, auth_data);
        if ct_eq(&expected, signature) {
            return Ok(());
        }
    }
    Err("invalid signature")
}

pub fn now_epoch() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sign_and_verify_roundtrip() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", b"");
        assert!(verify("secret", "1000", "orders", "get_item", b"{}", b"", &sig, 1010).is_ok());
    }

    #[test]
    fn tampered_body_rejected() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", b"");
        assert!(verify("secret", "1000", "orders", "get_item", b"{\"x\":1}", b"", &sig, 1010).is_err());
    }

    #[test]
    fn wrong_secret_rejected() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", b"");
        assert!(verify("other", "1000", "orders", "get_item", b"{}", b"", &sig, 1010).is_err());
    }

    #[test]
    fn stale_date_rejected() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", b"");
        assert!(verify("secret", "1000", "orders", "get_item", b"{}", b"", &sig, 5000).is_err());
    }

    #[test]
    fn cross_language_vector() {
        // Vector computed independently by the Python signer (call.py):
        // guarantees that the Python SDK and the Rust core produce the SAME signature.
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", b"");
        assert_eq!(
            sig,
            "35bd9fc5e57d92ee91952301516314311947ed66bf8e5bde5b83b816aab5dbe6"
        );
    }

    #[test]
    fn rotation_accepts_previous_secret() {
        // signed with the old secret; during the rotation the verifier
        // accepts [new, old] -> OK. With only the new one -> rejected.
        let sig = sign("old", "1000", "orders", "get_item", b"{}", b"");
        let both = ["new".to_string(), "old".to_string()];
        assert!(verify_any(&both, "1000", "orders", "get_item", b"{}", b"", &sig, 1010).is_ok());
        let only_new = ["new".to_string()];
        assert!(verify_any(&only_new, "1000", "orders", "get_item", b"{}", b"", &sig, 1010).is_err());
    }

    #[test]
    fn tampered_auth_data_rejected() {
        // the propagated identity is signed: it cannot be spoofed after the fact.
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", br#"{"user_id":"alice"}"#);
        assert!(verify(
            "secret", "1000", "orders", "get_item", b"{}",
            br#"{"user_id":"bob"}"#, &sig, 1010,
        )
        .is_err());
        // the same identity passes.
        assert!(verify(
            "secret", "1000", "orders", "get_item", b"{}",
            br#"{"user_id":"alice"}"#, &sig, 1010,
        )
        .is_ok());
    }
}
