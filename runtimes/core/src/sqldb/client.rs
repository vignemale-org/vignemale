// Exécution des requêtes (hors transaction) + tracing — miroir du client.rs
// d'Encore (le QueryTracer y pousse vers leur protocole de trace ; ici on
// pousse vers les logs structurés, l'export OTel viendra en phase 5).

use deadpool_postgres::Pool;

use super::manager::get_conn;
use super::val::{params_refs, rows_to_json, SqlParam};

/// Trace chaque requête : durée + requête (tronquée) ; erreurs en ERROR.
pub(crate) fn trace_query<T>(
    sql: &str,
    started: std::time::Instant,
    result: &anyhow::Result<T>,
) {
    let q: String = sql.chars().take(200).collect();
    let ms = started.elapsed().as_millis() as u64;
    match result {
        Ok(_) => {
            tracing::debug!(target: "vignemale::sqldb", query = %q, duration_ms = ms, "requête sql")
        }
        Err(e) => {
            tracing::error!(target: "vignemale::sqldb", query = %q, duration_ms = ms, error = %e, "requête sql en échec")
        }
    }
}

/// Exécute une requête et renvoie les lignes (tableau JSON d'objets).
pub async fn query(
    pool: &Pool,
    sql: &str,
    params: Vec<SqlParam>,
) -> anyhow::Result<serde_json::Value> {
    let started = std::time::Instant::now();
    let result = async {
        let client = get_conn(pool).await?;
        let rows = client.query(sql, &params_refs(&params)).await?;
        rows_to_json(&rows)
    }
    .await;
    trace_query(sql, started, &result);
    result
}

/// Exécute une commande (INSERT/UPDATE/DELETE/DDL) et renvoie le nombre de
/// lignes affectées.
pub async fn execute(pool: &Pool, sql: &str, params: Vec<SqlParam>) -> anyhow::Result<u64> {
    let started = std::time::Instant::now();
    let result = async {
        let client = get_conn(pool).await?;
        Ok(client.execute(sql, &params_refs(&params)).await?)
    }
    .await;
    trace_query(sql, started, &result);
    result
}
