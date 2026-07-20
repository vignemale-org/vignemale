// Query execution (outside a transaction) + tracing — mirror of Encore's
// client.rs (their QueryTracer pushes to their trace protocol; here we
// push to the structured logs, OTel export will come in phase 5).

use deadpool_postgres::Pool;

use super::manager::get_conn;
use super::val::{params_refs, rows_to_json, SqlParam};

/// Traces every query: duration + query (truncated); errors at ERROR level.
pub(crate) fn trace_query<T>(
    sql: &str,
    started: std::time::Instant,
    result: &anyhow::Result<T>,
) {
    let q: String = sql.chars().take(200).collect();
    let ms = started.elapsed().as_millis() as u64;
    match result {
        Ok(_) => {
            tracing::debug!(target: "vignemale::sqldb", query = %q, duration_ms = ms, "sql query")
        }
        Err(e) => {
            tracing::error!(target: "vignemale::sqldb", query = %q, duration_ms = ms, error = %e, "sql query failed")
        }
    }
}

/// Executes a query and returns the rows (JSON array of objects).
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

/// Validates a query via PREPARE — without executing it (the mechanism of
/// `sqlx::query!`, moved to `vignemale check` time). Postgres checks
/// syntax, tables, columns, and infers the types: we return them.
pub async fn prepare(pool: &Pool, sql: &str) -> anyhow::Result<serde_json::Value> {
    let started = std::time::Instant::now();
    let result = async {
        let client = get_conn(pool).await?;
        let stmt = client.prepare(sql).await?;
        let params: Vec<String> = stmt.params().iter().map(|t| t.to_string()).collect();
        let columns: Vec<serde_json::Value> = stmt
            .columns()
            .iter()
            .map(|c| serde_json::json!({"name": c.name(), "type": c.type_().to_string()}))
            .collect();
        Ok(serde_json::json!({"params": params, "columns": columns}))
    }
    .await;
    trace_query(sql, started, &result);
    result
}

/// Executes a **multi-statement** SQL script (simple query protocol) —
/// for migration files. No parameters.
pub async fn batch(pool: &Pool, sql: &str) -> anyhow::Result<()> {
    let started = std::time::Instant::now();
    let result = async {
        let client = get_conn(pool).await?;
        client.batch_execute(sql).await?;
        Ok(())
    }
    .await;
    trace_query(sql, started, &result);
    result
}

/// Executes a command (INSERT/UPDATE/DELETE/DDL) and returns the number of
/// affected rows.
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
