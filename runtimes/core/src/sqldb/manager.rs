// Pools de connexions par DSN + TLS — miroir du manager.rs d'Encore (réduit :
// la config vient du DSN et de l'environnement ; le mapping infra.proto
// arrivera avec le provisioning).

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use deadpool_postgres::{Manager as PgManager, ManagerConfig, Pool, RecyclingMethod};

// Un pool par DSN, partagé pour tout le process (créé paresseusement).
static POOLS: OnceLock<Mutex<HashMap<String, Pool>>> = OnceLock::new();

fn env_u32(name: &str, default: u32) -> u32 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

/// Connecteur TLS en rustls : roots Mozilla (webpki-roots) par défaut, CA custom
/// optionnelle (VIGNEMALE_SQLDB_CA_CERT), mode dev insecure
/// (VIGNEMALE_SQLDB_TLS_INSECURE=1). Le `sslmode` du DSN décide si TLS est utilisé.
fn tls_connector() -> anyhow::Result<tokio_postgres_rustls::MakeRustlsConnect> {
    use std::sync::Arc;
    let provider = Arc::new(rustls::crypto::ring::default_provider());

    let config = if std::env::var("VIGNEMALE_SQLDB_TLS_INSECURE").is_ok_and(|v| v == "1") {
        rustls::ClientConfig::builder_with_provider(provider.clone())
            .with_safe_default_protocol_versions()?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(InsecureVerifier(provider)))
            .with_no_client_auth()
    } else {
        let mut roots = rustls::RootCertStore::empty();
        roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
        if let Ok(path) = std::env::var("VIGNEMALE_SQLDB_CA_CERT") {
            let pem = std::fs::read(&path)
                .map_err(|e| anyhow::anyhow!("VIGNEMALE_SQLDB_CA_CERT ({path}): {e}"))?;
            for cert in rustls_pemfile::certs(&mut pem.as_slice()) {
                roots
                    .add(cert.map_err(|e| anyhow::anyhow!("CA PEM invalide: {e}"))?)
                    .map_err(|e| anyhow::anyhow!("ajout CA: {e}"))?;
            }
        }
        rustls::ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()?
            .with_root_certificates(roots)
            .with_no_client_auth()
    };
    Ok(tokio_postgres_rustls::MakeRustlsConnect::new(config))
}

/// Vérificateur permissif pour le mode dev (VIGNEMALE_SQLDB_TLS_INSECURE).
#[derive(Debug)]
struct InsecureVerifier(std::sync::Arc<rustls::crypto::CryptoProvider>);

impl rustls::client::danger::ServerCertVerifier for InsecureVerifier {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &rustls::pki_types::CertificateDer<'_>,
        dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(message, cert, dss, &self.0.signature_verification_algorithms)
    }
    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &rustls::pki_types::CertificateDer<'_>,
        dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(message, cert, dss, &self.0.signature_verification_algorithms)
    }
    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        self.0.signature_verification_algorithms.supported_schemes()
    }
}

/// Renvoie (en le créant au besoin) le pool de connexions pour ce DSN.
pub fn pool_for_dsn(dsn: &str) -> anyhow::Result<Pool> {
    let pools = POOLS.get_or_init(|| Mutex::new(HashMap::new()));
    let mut pools = pools.lock().expect("pools lock");
    if let Some(p) = pools.get(dsn) {
        return Ok(p.clone());
    }
    let cfg: tokio_postgres::Config = dsn
        .parse()
        .map_err(|e| anyhow::anyhow!("DSN invalide: {e}"))?;
    let mgr = PgManager::from_config(
        cfg,
        tls_connector()?,
        ManagerConfig {
            recycling_method: RecyclingMethod::Fast,
        },
    );
    let pool = Pool::builder(mgr)
        .max_size(env_u32("VIGNEMALE_SQLDB_MAX_CONNS", 30) as usize)
        .wait_timeout(Some(std::time::Duration::from_secs(10)))
        .create_timeout(Some(std::time::Duration::from_secs(10)))
        .runtime(deadpool_postgres::Runtime::Tokio1)
        .build()?;
    pools.insert(dsn.to_string(), pool.clone());
    Ok(pool)
}

pub(crate) async fn get_conn(pool: &Pool) -> anyhow::Result<deadpool_postgres::Object> {
    pool.get().await.map_err(|e| match e {
        deadpool_postgres::PoolError::Timeout(_) => {
            anyhow::anyhow!("timeout d'obtention d'une connexion (pool saturé ou base injoignable)")
        }
        other => anyhow::anyhow!("{other:#}"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_dsn_rejected() {
        assert!(pool_for_dsn("pas un dsn").is_err());
    }
}
