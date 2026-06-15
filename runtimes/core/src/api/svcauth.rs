// svcauth — authentification des appels service-à-service (façon EncoreAuth
// d'Encore, simplifié) : HMAC-SHA256 sur (date, caller, endpoint, hash du
// body, hash de l'identité propagée) avec contrôle d'horloge anti-rejeu. Le
// secret partagé vient de `VIGNEMALE_SERVICE_SECRET` (posé par le deploy ;
// jamais exposé aux clients).
//
// `auth_data` (= l'en-tête `x-vignemale-auth-data`, identité de l'appelant
// propagée) est INCLUS dans la signature, comme Encore lie UserId/UserData
// dans son OperationHash : sans ça, un service détenteur du secret pourrait
// usurper l'identité d'un autre utilisateur sur un appel interne.

use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};

/// Tolérance d'horloge entre services (secondes).
pub const MAX_SKEW_SECS: i64 = 120;

/// Signe un appel interne. `date` = epoch (secondes) en texte. `auth_data` =
/// l'identité propagée (octets de `x-vignemale-auth-data`, vide si absente).
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
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("clé HMAC");
    mac.update(msg.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Secrets acceptés en vérification, depuis l'environnement : le secret courant
/// (`VIGNEMALE_SERVICE_SECRET`) + d'éventuels précédents
/// (`VIGNEMALE_SERVICE_SECRET_PREVIOUS`, séparés par des virgules). Permet la
/// **rotation sans coupure** : on déploie le nouveau comme courant en gardant
/// l'ancien en « précédent », puis on retire l'ancien une fois tous les
/// services à jour. (Encore utilise des clés à `key_id` rotatif ; on simplifie
/// en acceptant un jeu de secrets — le signataire utilise toujours le courant.)
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

/// Compare deux signatures hex à temps constant.
fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Vérifie la signature contre UN secret (comparaison à temps constant).
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

/// Vérifie contre un JEU de secrets (courant + précédents) — pour la rotation.
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
    let ts: i64 = date.parse().map_err(|_| "date invalide")?;
    if (now_epoch - ts).abs() > MAX_SKEW_SECS {
        return Err("date hors tolérance (rejeu ?)");
    }
    for secret in secrets {
        let expected = sign(secret.as_ref(), date, caller, endpoint, body, auth_data);
        if ct_eq(&expected, signature) {
            return Ok(());
        }
    }
    Err("signature invalide")
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
        assert!(verify("autre", "1000", "orders", "get_item", b"{}", b"", &sig, 1010).is_err());
    }

    #[test]
    fn stale_date_rejected() {
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", b"");
        assert!(verify("secret", "1000", "orders", "get_item", b"{}", b"", &sig, 5000).is_err());
    }

    #[test]
    fn cross_language_vector() {
        // Vecteur calculé indépendamment par le signataire Python (call.py) :
        // garantit que SDK Python et cœur Rust produisent la MÊME signature.
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", b"");
        assert_eq!(
            sig,
            "35bd9fc5e57d92ee91952301516314311947ed66bf8e5bde5b83b816aab5dbe6"
        );
    }

    #[test]
    fn rotation_accepts_previous_secret() {
        // signé avec l'ancien secret ; pendant la rotation le vérificateur
        // accepte [nouveau, ancien] → OK. Avec le seul nouveau → rejeté.
        let sig = sign("ancien", "1000", "orders", "get_item", b"{}", b"");
        let both = ["nouveau".to_string(), "ancien".to_string()];
        assert!(verify_any(&both, "1000", "orders", "get_item", b"{}", b"", &sig, 1010).is_ok());
        let only_new = ["nouveau".to_string()];
        assert!(verify_any(&only_new, "1000", "orders", "get_item", b"{}", b"", &sig, 1010).is_err());
    }

    #[test]
    fn tampered_auth_data_rejected() {
        // identité propagée signée : on ne peut pas l'usurper a posteriori.
        let sig = sign("secret", "1000", "orders", "get_item", b"{}", br#"{"user_id":"alice"}"#);
        assert!(verify(
            "secret", "1000", "orders", "get_item", b"{}",
            br#"{"user_id":"bob"}"#, &sig, 1010,
        )
        .is_err());
        // la même identité passe.
        assert!(verify(
            "secret", "1000", "orders", "get_item", b"{}",
            br#"{"user_id":"alice"}"#, &sig, 1010,
        )
        .is_ok());
    }
}
