// svcauth — authentification des appels service-à-service (façon EncoreAuth
// d'Encore, simplifié) : HMAC-SHA256 sur (date, caller, endpoint, hash du
// body) avec contrôle d'horloge anti-rejeu. Le secret partagé vient de
// `VIGNEMALE_SERVICE_SECRET` (posé par le deploy ; jamais exposé aux clients).

use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};

/// Tolérance d'horloge entre services (secondes).
pub const MAX_SKEW_SECS: i64 = 120;

/// Signe un appel interne. `date` = epoch (secondes) en texte.
pub fn sign(secret: &str, date: &str, caller: &str, endpoint: &str, body: &[u8]) -> String {
    let body_hash = hex::encode(Sha256::digest(body));
    let msg = format!("{date}\n{caller}\n{endpoint}\n{body_hash}");
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("clé HMAC");
    mac.update(msg.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Vérifie la signature d'un appel interne (comparaison à temps constant).
pub fn verify(
    secret: &str,
    date: &str,
    caller: &str,
    endpoint: &str,
    body: &[u8],
    signature: &str,
    now_epoch: i64,
) -> Result<(), &'static str> {
    let ts: i64 = date.parse().map_err(|_| "date invalide")?;
    if (now_epoch - ts).abs() > MAX_SKEW_SECS {
        return Err("date hors tolérance (rejeu ?)");
    }
    let expected = sign(secret, date, caller, endpoint, body);
    let same = expected.len() == signature.len()
        && expected
            .bytes()
            .zip(signature.bytes())
            .fold(0u8, |acc, (a, b)| acc | (a ^ b))
            == 0;
    if same {
        Ok(())
    } else {
        Err("signature invalide")
    }
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
        let sig = sign("secret", "1000", "orders", "get_item", b"{}");
        assert!(verify("secret", "1000", "orders", "get_item", b"{}", &sig, 1010).is_ok());
    }

    #[test]
    fn tampered_body_rejected() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}");
        assert!(verify("secret", "1000", "orders", "get_item", b"{\"x\":1}", &sig, 1010).is_err());
    }

    #[test]
    fn wrong_secret_rejected() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}");
        assert!(verify("autre", "1000", "orders", "get_item", b"{}", &sig, 1010).is_err());
    }

    #[test]
    fn stale_date_rejected() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}");
        assert!(verify("secret", "1000", "orders", "get_item", b"{}", &sig, 5000).is_err());
    }
}
