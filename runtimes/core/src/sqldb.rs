//! `sqldb` — Postgres : pool de connexions + requêtes, résultats en JSON.
//!
//! Version focalisée du module `sqldb` d'Encore : le binding passe des
//! paramètres JSON, on renvoie les lignes en JSON. Le mapping fin depuis
//! l'`infra.proto` (provider switch Managed Database / Docker local) viendra
//! avec le provisioning ; pour l'instant le DSN arrive résolu du SDK.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use deadpool_postgres::{Manager as PgManager, ManagerConfig, Pool, RecyclingMethod};
use tokio_postgres::types::{IsNull, ToSql, Type};
use tokio_postgres::{NoTls, Row};

// Un pool par DSN, partagé pour tout le process (créé paresseusement).
static POOLS: OnceLock<Mutex<HashMap<String, Pool>>> = OnceLock::new();

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
        NoTls,
        ManagerConfig {
            recycling_method: RecyclingMethod::Fast,
        },
    );
    let pool = Pool::builder(mgr).max_size(16).build()?;
    pools.insert(dsn.to_string(), pool.clone());
    Ok(pool)
}

/// Paramètre SQL venu du binding (valeur JSON) ; s'adapte au type attendu
/// par Postgres au moment du bind (INT4 vs INT8, TEXT vs UUID, etc.).
#[derive(Debug)]
pub enum SqlParam {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Json(serde_json::Value),
}

impl SqlParam {
    pub fn from_json(v: serde_json::Value) -> Self {
        use serde_json::Value as J;
        match v {
            J::Null => SqlParam::Null,
            J::Bool(b) => SqlParam::Bool(b),
            J::Number(n) => match n.as_i64() {
                Some(i) => SqlParam::Int(i),
                None => SqlParam::Float(n.as_f64().unwrap_or(f64::NAN)),
            },
            J::String(s) => SqlParam::Str(s),
            other => SqlParam::Json(other),
        }
    }
}

type ToSqlResult = Result<IsNull, Box<dyn std::error::Error + Sync + Send>>;

impl ToSql for SqlParam {
    fn to_sql(&self, ty: &Type, out: &mut bytes::BytesMut) -> ToSqlResult {
        match self {
            SqlParam::Null => Ok(IsNull::Yes),
            SqlParam::Bool(b) => b.to_sql(ty, out),
            SqlParam::Int(i) => match *ty {
                Type::INT2 => (*i as i16).to_sql(ty, out),
                Type::INT4 => (*i as i32).to_sql(ty, out),
                Type::FLOAT4 => (*i as f32).to_sql(ty, out),
                Type::FLOAT8 => (*i as f64).to_sql(ty, out),
                _ => i.to_sql(ty, out),
            },
            SqlParam::Float(f) => match *ty {
                Type::FLOAT4 => (*f as f32).to_sql(ty, out),
                _ => f.to_sql(ty, out),
            },
            SqlParam::Str(s) => match *ty {
                Type::UUID => uuid::Uuid::parse_str(s)?.to_sql(ty, out),
                Type::TIMESTAMPTZ => chrono::DateTime::parse_from_rfc3339(s)?
                    .with_timezone(&chrono::Utc)
                    .to_sql(ty, out),
                Type::JSON | Type::JSONB => {
                    serde_json::Value::String(s.clone()).to_sql(ty, out)
                }
                _ => s.as_str().to_sql(ty, out),
            },
            SqlParam::Json(v) => v.to_sql(ty, out),
        }
    }

    fn accepts(_ty: &Type) -> bool {
        true // on tente la conversion ; l'erreur de bind remonte sinon
    }

    tokio_postgres::types::to_sql_checked!();
}

fn params_refs(params: &[SqlParam]) -> Vec<&(dyn ToSql + Sync)> {
    params.iter().map(|p| p as &(dyn ToSql + Sync)).collect()
}

/// Exécute une requête et renvoie les lignes (tableau JSON d'objets).
pub async fn query(
    pool: &Pool,
    sql: &str,
    params: Vec<SqlParam>,
) -> anyhow::Result<serde_json::Value> {
    let client = pool.get().await?;
    let rows = client.query(sql, &params_refs(&params)).await?;
    rows_to_json(&rows)
}

/// Exécute une commande (INSERT/UPDATE/DELETE/DDL) et renvoie le nombre de
/// lignes affectées.
pub async fn execute(pool: &Pool, sql: &str, params: Vec<SqlParam>) -> anyhow::Result<u64> {
    let client = pool.get().await?;
    Ok(client.execute(sql, &params_refs(&params)).await?)
}

fn rows_to_json(rows: &[Row]) -> anyhow::Result<serde_json::Value> {
    let mut out = Vec::with_capacity(rows.len());
    for row in rows {
        let mut obj = serde_json::Map::new();
        for (i, col) in row.columns().iter().enumerate() {
            obj.insert(col.name().to_string(), col_to_json(row, i, col.type_())?);
        }
        out.push(serde_json::Value::Object(obj));
    }
    Ok(serde_json::Value::Array(out))
}

fn col_to_json(row: &Row, i: usize, ty: &Type) -> anyhow::Result<serde_json::Value> {
    use serde_json::Value as J;

    fn opt<T, F: FnOnce(T) -> J>(v: Option<T>, f: F) -> J {
        v.map(f).unwrap_or(J::Null)
    }

    Ok(match *ty {
        Type::BOOL => opt(row.try_get::<_, Option<bool>>(i)?, J::Bool),
        Type::INT2 => opt(row.try_get::<_, Option<i16>>(i)?, |v| J::from(v)),
        Type::INT4 => opt(row.try_get::<_, Option<i32>>(i)?, |v| J::from(v)),
        Type::INT8 => opt(row.try_get::<_, Option<i64>>(i)?, |v| J::from(v)),
        Type::FLOAT4 => opt(row.try_get::<_, Option<f32>>(i)?, |v| J::from(v)),
        Type::FLOAT8 => opt(row.try_get::<_, Option<f64>>(i)?, |v| J::from(v)),
        Type::TEXT | Type::VARCHAR | Type::BPCHAR | Type::NAME => {
            opt(row.try_get::<_, Option<String>>(i)?, J::String)
        }
        Type::JSON | Type::JSONB => row
            .try_get::<_, Option<serde_json::Value>>(i)?
            .unwrap_or(J::Null),
        Type::TIMESTAMPTZ => opt(
            row.try_get::<_, Option<chrono::DateTime<chrono::Utc>>>(i)?,
            |v| J::String(v.to_rfc3339()),
        ),
        Type::TIMESTAMP => opt(row.try_get::<_, Option<chrono::NaiveDateTime>>(i)?, |v| {
            J::String(v.format("%Y-%m-%dT%H:%M:%S%.f").to_string())
        }),
        Type::DATE => opt(row.try_get::<_, Option<chrono::NaiveDate>>(i)?, |v| {
            J::String(v.to_string())
        }),
        Type::UUID => opt(row.try_get::<_, Option<uuid::Uuid>>(i)?, |v| {
            J::String(v.to_string())
        }),
        ref other => anyhow::bail!(
            "sqldb: type de colonne non supporté: {other} (colonne {})",
            row.columns()[i].name()
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn from_json_maps_values() {
        assert!(matches!(
            SqlParam::from_json(serde_json::json!(null)),
            SqlParam::Null
        ));
        assert!(matches!(
            SqlParam::from_json(serde_json::json!(true)),
            SqlParam::Bool(true)
        ));
        assert!(matches!(
            SqlParam::from_json(serde_json::json!(42)),
            SqlParam::Int(42)
        ));
        assert!(matches!(
            SqlParam::from_json(serde_json::json!(1.5)),
            SqlParam::Float(_)
        ));
        assert!(matches!(
            SqlParam::from_json(serde_json::json!("x")),
            SqlParam::Str(_)
        ));
        assert!(matches!(
            SqlParam::from_json(serde_json::json!({"a": 1})),
            SqlParam::Json(_)
        ));
    }

    #[test]
    fn invalid_dsn_rejected() {
        assert!(pool_for_dsn("pas un dsn").is_err());
    }
}
