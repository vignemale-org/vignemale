// Conversion des valeurs — miroir du val.rs d'Encore : les paramètres JSON
// s'adaptent au type Postgres au moment du bind (coercions string → date /
// uuid / numeric / bytea…), et les lignes reviennent en JSON typé.

use rust_decimal::prelude::FromPrimitive;
use rust_decimal::Decimal;
use tokio_postgres::types::{IsNull, Kind, ToSql, Type};
use tokio_postgres::Row;

/// Paramètre SQL venu du binding (valeur JSON).
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
            SqlParam::Bool(b) => match *ty {
                Type::TEXT | Type::VARCHAR => b.to_string().to_sql(ty, out),
                _ => b.to_sql(ty, out),
            },
            SqlParam::Int(i) => match *ty {
                Type::INT2 => (*i as i16).to_sql(ty, out),
                Type::INT4 => (*i as i32).to_sql(ty, out),
                Type::FLOAT4 => (*i as f32).to_sql(ty, out),
                Type::FLOAT8 => (*i as f64).to_sql(ty, out),
                Type::NUMERIC => Decimal::from(*i).to_sql(ty, out),
                _ => i.to_sql(ty, out),
            },
            SqlParam::Float(f) => match *ty {
                Type::FLOAT4 => (*f as f32).to_sql(ty, out),
                Type::NUMERIC => Decimal::from_f64(*f)
                    .ok_or("float non représentable en NUMERIC")?
                    .to_sql(ty, out),
                _ => f.to_sql(ty, out),
            },
            SqlParam::Str(s) => match *ty {
                Type::UUID => uuid::Uuid::parse_str(s)?.to_sql(ty, out),
                Type::TIMESTAMPTZ => chrono::DateTime::parse_from_rfc3339(s)?
                    .with_timezone(&chrono::Utc)
                    .to_sql(ty, out),
                Type::TIMESTAMP => s.parse::<chrono::NaiveDateTime>()?.to_sql(ty, out),
                Type::DATE => {
                    chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d")?.to_sql(ty, out)
                }
                Type::TIME => {
                    chrono::NaiveTime::parse_from_str(s, "%H:%M:%S")?.to_sql(ty, out)
                }
                Type::NUMERIC => s.parse::<Decimal>()?.to_sql(ty, out),
                Type::BYTEA => s.as_bytes().to_sql(ty, out),
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

pub(crate) fn params_refs(params: &[SqlParam]) -> Vec<&(dyn ToSql + Sync)> {
    params.iter().map(|p| p as &(dyn ToSql + Sync)).collect()
}

// --- lignes → JSON typé ---

pub(crate) fn rows_to_json(rows: &[Row]) -> anyhow::Result<serde_json::Value> {
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

fn opt<T, F: FnOnce(T) -> serde_json::Value>(v: Option<T>, f: F) -> serde_json::Value {
    v.map(f).unwrap_or(serde_json::Value::Null)
}

fn array_to_json(row: &Row, i: usize, inner: &Type) -> anyhow::Result<serde_json::Value> {
    use serde_json::Value as J;
    Ok(match *inner {
        Type::BOOL => opt(row.try_get::<_, Option<Vec<bool>>>(i)?, J::from),
        Type::INT2 => opt(row.try_get::<_, Option<Vec<i16>>>(i)?, J::from),
        Type::INT4 => opt(row.try_get::<_, Option<Vec<i32>>>(i)?, J::from),
        Type::INT8 => opt(row.try_get::<_, Option<Vec<i64>>>(i)?, J::from),
        Type::FLOAT4 => opt(row.try_get::<_, Option<Vec<f32>>>(i)?, J::from),
        Type::FLOAT8 => opt(row.try_get::<_, Option<Vec<f64>>>(i)?, J::from),
        Type::TEXT | Type::VARCHAR => {
            opt(row.try_get::<_, Option<Vec<String>>>(i)?, J::from)
        }
        Type::UUID => opt(row.try_get::<_, Option<Vec<uuid::Uuid>>>(i)?, |v| {
            J::Array(v.into_iter().map(|u| J::String(u.to_string())).collect())
        }),
        ref other => anyhow::bail!(
            "sqldb: tableau de type non supporté: {other} (colonne {})",
            row.columns()[i].name()
        ),
    })
}

fn col_to_json(row: &Row, i: usize, ty: &Type) -> anyhow::Result<serde_json::Value> {
    use serde_json::Value as J;

    if let Kind::Array(inner) = ty.kind() {
        return array_to_json(row, i, inner);
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
        Type::TIME => opt(row.try_get::<_, Option<chrono::NaiveTime>>(i)?, |v| {
            J::String(v.format("%H:%M:%S%.f").to_string())
        }),
        Type::UUID => opt(row.try_get::<_, Option<uuid::Uuid>>(i)?, |v| {
            J::String(v.to_string())
        }),
        // précision préservée : NUMERIC voyage en string (façon Decimal d'Encore)
        Type::NUMERIC => opt(row.try_get::<_, Option<Decimal>>(i)?, |v| {
            J::String(v.to_string())
        }),
        // binaire : encodé base64 pour traverser le JSON
        Type::BYTEA => opt(row.try_get::<_, Option<Vec<u8>>>(i)?, |v| {
            use base64::Engine as _;
            J::String(base64::engine::general_purpose::STANDARD.encode(v))
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
}
