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

/// Connecteur TLS (comme le manager d'Encore) : CA custom optionnelle,
/// garde-fous dev. Le `sslmode` du DSN décide si TLS est utilisé.
fn tls_connector() -> anyhow::Result<postgres_native_tls::MakeTlsConnector> {
    let mut builder = native_tls::TlsConnector::builder();
    if let Ok(path) = std::env::var("VIGNEMALE_SQLDB_CA_CERT") {
        let pem = std::fs::read(&path)
            .map_err(|e| anyhow::anyhow!("VIGNEMALE_SQLDB_CA_CERT ({path}): {e}"))?;
        builder.add_root_certificate(
            native_tls::Certificate::from_pem(&pem)
                .map_err(|e| anyhow::anyhow!("certificat CA invalide: {e}"))?,
        );
    }
    if std::env::var("VIGNEMALE_SQLDB_TLS_INSECURE").is_ok_and(|v| v == "1") {
        builder.danger_accept_invalid_certs(true);
        builder.danger_accept_invalid_hostnames(true);
    }
    Ok(postgres_native_tls::MakeTlsConnector::new(builder.build()?))
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
