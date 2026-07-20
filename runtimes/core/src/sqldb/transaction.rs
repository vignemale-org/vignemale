// Transactions without a lifetime — mirror of Encore's transaction.rs ("where the
// transaction doesn't have a lifetime, so it can be shared via napi-rs"):
// in our case, to cross the PyO3 binding. A registry id -> pool connection;
// BEGIN on open, a terminal COMMIT/ROLLBACK returns the connection.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};

use deadpool_postgres::Pool;

use super::client::trace_query;
use super::manager::get_conn;
use super::val::{params_refs, rows_to_json, SqlParam};

static TXS: OnceLock<Mutex<HashMap<u64, deadpool_postgres::Object>>> = OnceLock::new();
static TX_SEQ: AtomicU64 = AtomicU64::new(0);

fn txs() -> &'static Mutex<HashMap<u64, deadpool_postgres::Object>> {
    TXS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn take_tx(id: u64) -> anyhow::Result<deadpool_postgres::Object> {
    txs()
        .lock()
        .expect("txs lock")
        .remove(&id)
        .ok_or_else(|| anyhow::anyhow!("transaction {id} unknown or already finished"))
}

/// Opens a transaction (BEGIN) and returns its identifier.
pub async fn tx_begin(pool: &Pool) -> anyhow::Result<u64> {
    let started = std::time::Instant::now();
    let result = async {
        let conn = get_conn(pool).await?;
        conn.batch_execute("BEGIN").await?;
        Ok(conn)
    }
    .await;
    trace_query("BEGIN", started, &result);
    let conn = result?;
    let id = TX_SEQ.fetch_add(1, Ordering::SeqCst) + 1;
    txs().lock().expect("txs lock").insert(id, conn);
    Ok(id)
}

async fn with_tx<T, F, Fut>(id: u64, f: F) -> anyhow::Result<T>
where
    F: FnOnce(deadpool_postgres::Object) -> Fut,
    Fut: std::future::Future<Output = (deadpool_postgres::Object, anyhow::Result<T>)>,
{
    let conn = take_tx(id)?;
    let (conn, result) = f(conn).await;
    // the connection goes back to the registry, even after a SQL error: it is
    // the rollback (from the SDK) that finishes the transaction cleanly
    txs().lock().expect("txs lock").insert(id, conn);
    result
}

pub async fn tx_query(
    id: u64,
    sql: &str,
    params: Vec<SqlParam>,
) -> anyhow::Result<serde_json::Value> {
    let started = std::time::Instant::now();
    let result = with_tx(id, |conn| async move {
        let r = async {
            let rows = conn.query(sql, &params_refs(&params)).await?;
            rows_to_json(&rows)
        }
        .await;
        (conn, r)
    })
    .await;
    trace_query(sql, started, &result);
    result
}

pub async fn tx_execute(id: u64, sql: &str, params: Vec<SqlParam>) -> anyhow::Result<u64> {
    let started = std::time::Instant::now();
    let result = with_tx(id, |conn| async move {
        let r = async { Ok(conn.execute(sql, &params_refs(&params)).await?) }.await;
        (conn, r)
    })
    .await;
    trace_query(sql, started, &result);
    result
}

async fn tx_finish(id: u64, terminal: &str) -> anyhow::Result<()> {
    let conn = take_tx(id)?;
    let started = std::time::Instant::now();
    let result = conn
        .batch_execute(terminal)
        .await
        .map_err(|e| anyhow::anyhow!("{e:#}"));
    trace_query(terminal, started, &result);
    // conn returns to the pool here (drop) — the transaction is finished
    result
}

pub async fn tx_commit(id: u64) -> anyhow::Result<()> {
    tx_finish(id, "COMMIT").await
}

pub async fn tx_rollback(id: u64) -> anyhow::Result<()> {
    tx_finish(id, "ROLLBACK").await
}
