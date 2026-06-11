// ORM côté CORE : le SDK (Python, demain JS…) envoie un DESCRIPTEUR de table
// (JSON) et des opérations logiques ; tout le SQL est généré ICI, par
// **sea-query** (le builder sous SeaORM — dynamique à l'exécution, quoting et
// placeholders éprouvés). Les SDKs restent des façades déclaratives :
// ajouter un langage n'ajoute pas un deuxième ORM.

use deadpool_postgres::Pool;
use sea_query::{
    Alias, ColumnDef, Expr, Order, PostgresQueryBuilder, Query, Table,
};
use serde::Deserialize;

use super::client::{execute, query};
use super::val::SqlParam;

#[derive(Debug, Deserialize)]
pub struct ColumnSpec {
    pub name: String,
    /// Type logique : int | str | bool | float | datetime | date | json
    pub typ: String,
    #[serde(default)]
    pub nullable: bool,
    #[serde(default)]
    pub primary_key: bool,
}

#[derive(Debug, Deserialize)]
pub struct TableSchema {
    pub table: String,
    pub columns: Vec<ColumnSpec>,
}

impl TableSchema {
    /// Whitelist : seules les colonnes du schéma sont adressables.
    fn column(&self, name: &str) -> anyhow::Result<&ColumnSpec> {
        self.columns
            .iter()
            .find(|c| c.name == name)
            .ok_or_else(|| anyhow::anyhow!("colonne inconnue: {name} (table {})", self.table))
    }

    fn pk(&self) -> anyhow::Result<&ColumnSpec> {
        self.columns
            .iter()
            .find(|c| c.primary_key)
            .ok_or_else(|| anyhow::anyhow!("table {} sans clé primaire", self.table))
    }

    fn alias(&self) -> Alias {
        Alias::new(&self.table)
    }
}

/// JSON → valeur sea-query (qui génère le placeholder).
fn to_value(v: &serde_json::Value) -> sea_query::Value {
    use serde_json::Value as J;
    match v {
        J::Null => sea_query::Value::String(None),
        J::Bool(b) => (*b).into(),
        J::Number(n) => match n.as_i64() {
            Some(i) => i.into(),
            None => n.as_f64().unwrap_or(f64::NAN).into(),
        },
        J::String(s) => s.clone().into(),
        other => sea_query::Value::Json(Some(Box::new(other.clone()))),
    }
}

/// Valeurs sea-query → nos paramètres de bind (coercions val.rs au moment
/// de l'exécution : NUMERIC, dates, uuid…).
fn to_params(values: sea_query::Values) -> Vec<SqlParam> {
    use sea_query::Value as V;
    values
        .into_iter()
        .map(|v| match v {
            V::Bool(Some(b)) => SqlParam::Bool(b),
            V::TinyInt(Some(i)) => SqlParam::Int(i as i64),
            V::SmallInt(Some(i)) => SqlParam::Int(i as i64),
            V::Int(Some(i)) => SqlParam::Int(i as i64),
            V::BigInt(Some(i)) => SqlParam::Int(i),
            V::Float(Some(f)) => SqlParam::Float(f as f64),
            V::Double(Some(f)) => SqlParam::Float(f),
            V::String(Some(s)) => SqlParam::Str(*s),
            V::Json(Some(j)) => SqlParam::Json(*j),
            _ => SqlParam::Null,
        })
        .collect()
}

fn column_def(c: &ColumnSpec) -> anyhow::Result<ColumnDef> {
    let mut def = ColumnDef::new(Alias::new(&c.name));
    if c.primary_key && c.typ == "int" {
        def.big_integer().auto_increment().primary_key(); // → BIGSERIAL PK
        return Ok(def);
    }
    match c.typ.as_str() {
        "int" => def.big_integer(),
        "str" => def.text(),
        "bool" => def.boolean(),
        "float" => def.double(),
        "datetime" => def.timestamp_with_time_zone(),
        "date" => def.date(),
        "json" => def.json_binary(),
        other => anyhow::bail!("type logique inconnu: {other:?}"),
    };
    if !c.nullable {
        def.not_null();
    }
    Ok(def)
}

type JsonMap = serde_json::Map<String, serde_json::Value>;

/// Applique les égalités du `where` (clés validées contre le schéma).
fn apply_where<S: sea_query::ConditionalStatement>(
    stmt: &mut S,
    schema: &TableSchema,
    where_: &JsonMap,
) -> anyhow::Result<()> {
    for (k, v) in where_ {
        let col = Expr::col(Alias::new(&schema.column(k)?.name));
        if v.is_null() {
            stmt.and_where(col.is_null());
        } else {
            stmt.and_where(col.eq(to_value(v)));
        }
    }
    Ok(())
}

async fn run_query(
    pool: &Pool,
    built: (String, sea_query::Values),
) -> anyhow::Result<serde_json::Value> {
    query(pool, &built.0, to_params(built.1)).await
}

async fn run_execute(
    pool: &Pool,
    built: (String, sea_query::Values),
) -> anyhow::Result<u64> {
    execute(pool, &built.0, to_params(built.1)).await
}

/// Crée la table si besoin + migration additive (colonnes manquantes).
pub async fn ensure(pool: &Pool, schema: &TableSchema) -> anyhow::Result<()> {
    let mut create = Table::create();
    create.table(schema.alias()).if_not_exists();
    for c in &schema.columns {
        create.col(&mut column_def(c)?);
    }
    execute(pool, &create.build(PostgresQueryBuilder), vec![]).await?;

    let existing = query(
        pool,
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
        vec![SqlParam::Str(schema.table.clone())],
    )
    .await?;
    let existing: Vec<&str> = existing
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|r| r.get("column_name").and_then(|v| v.as_str()))
                .collect()
        })
        .unwrap_or_default();
    for c in &schema.columns {
        if !existing.contains(&c.name.as_str()) {
            let mut def = column_def(c)?;
            def.null(); // additive : pas de NOT NULL sur les lignes existantes
            let mut alter = Table::alter();
            alter.table(schema.alias()).add_column(&mut def);
            execute(pool, &alter.build(PostgresQueryBuilder), vec![]).await?;
        }
    }
    Ok(())
}

fn first_row(rows: serde_json::Value) -> serde_json::Value {
    rows.as_array()
        .and_then(|a| a.first())
        .cloned()
        .unwrap_or(serde_json::Value::Null)
}

/// INSERT … RETURNING * → la ligne créée.
pub async fn insert(
    pool: &Pool,
    schema: &TableSchema,
    values: &JsonMap,
) -> anyhow::Result<serde_json::Value> {
    let mut stmt = Query::insert();
    stmt.into_table(schema.alias())
        .returning_all();
    if values.is_empty() {
        stmt.or_default_values();
    } else {
        let mut cols = Vec::new();
        let mut vals = Vec::new();
        for (k, v) in values {
            cols.push(Alias::new(&schema.column(k)?.name));
            vals.push(sea_query::SimpleExpr::Value(to_value(v)));
        }
        stmt.columns(cols).values(vals)?;
    }
    Ok(first_row(run_query(pool, stmt.build(PostgresQueryBuilder)).await?))
}

/// SELECT par clé primaire → la ligne, ou null.
pub async fn get(
    pool: &Pool,
    schema: &TableSchema,
    pk: serde_json::Value,
) -> anyhow::Result<serde_json::Value> {
    let pk_col = Alias::new(&schema.pk()?.name);
    let mut stmt = Query::select();
    stmt.column(sea_query::Asterisk)
        .from(schema.alias())
        .and_where(Expr::col(pk_col).eq(to_value(&pk)));
    Ok(first_row(run_query(pool, stmt.build(PostgresQueryBuilder)).await?))
}

/// SELECT par égalités → tableau de lignes (ordonné par clé primaire).
pub async fn find(
    pool: &Pool,
    schema: &TableSchema,
    where_: &JsonMap,
) -> anyhow::Result<serde_json::Value> {
    let mut stmt = Query::select();
    stmt.column(sea_query::Asterisk).from(schema.alias());
    apply_where(&mut stmt, schema, where_)?;
    if let Ok(pk) = schema.pk() {
        stmt.order_by(Alias::new(&pk.name), Order::Asc);
    }
    run_query(pool, stmt.build(PostgresQueryBuilder)).await
}

pub async fn count(pool: &Pool, schema: &TableSchema, where_: &JsonMap) -> anyhow::Result<u64> {
    let mut stmt = Query::select();
    stmt.expr_as(Expr::cust("count(*)"), Alias::new("n"))
        .from(schema.alias());
    apply_where(&mut stmt, schema, where_)?;
    let rows = run_query(pool, stmt.build(PostgresQueryBuilder)).await?;
    Ok(first_row(rows).get("n").and_then(|v| v.as_u64()).unwrap_or(0))
}

/// UPDATE par clé primaire.
pub async fn update(
    pool: &Pool,
    schema: &TableSchema,
    pk: serde_json::Value,
    values: &JsonMap,
) -> anyhow::Result<u64> {
    if values.is_empty() {
        return Ok(0);
    }
    let pk_col = Alias::new(&schema.pk()?.name);
    let mut stmt = Query::update();
    stmt.table(schema.alias());
    for (k, v) in values {
        stmt.value(Alias::new(&schema.column(k)?.name), to_value(v));
    }
    stmt.and_where(Expr::col(pk_col).eq(to_value(&pk)));
    run_execute(pool, stmt.build(PostgresQueryBuilder)).await
}

/// UPDATE … WHERE égalités (utilisé par l'anonymisation RGPD).
pub async fn update_where(
    pool: &Pool,
    schema: &TableSchema,
    values: &JsonMap,
    where_: &JsonMap,
) -> anyhow::Result<u64> {
    if values.is_empty() {
        return Ok(0);
    }
    if where_.is_empty() {
        anyhow::bail!("update_where exige au moins un critère");
    }
    let mut stmt = Query::update();
    stmt.table(schema.alias());
    for (k, v) in values {
        stmt.value(Alias::new(&schema.column(k)?.name), to_value(v));
    }
    apply_where(&mut stmt, schema, where_)?;
    run_execute(pool, stmt.build(PostgresQueryBuilder)).await
}

/// DELETE par clé primaire.
pub async fn delete(
    pool: &Pool,
    schema: &TableSchema,
    pk: serde_json::Value,
) -> anyhow::Result<u64> {
    let pk_col = Alias::new(&schema.pk()?.name);
    let mut stmt = Query::delete();
    stmt.from_table(schema.alias())
        .and_where(Expr::col(pk_col).eq(to_value(&pk)));
    run_execute(pool, stmt.build(PostgresQueryBuilder)).await
}

/// DELETE … WHERE égalités (critère obligatoire).
pub async fn delete_where(
    pool: &Pool,
    schema: &TableSchema,
    where_: &JsonMap,
) -> anyhow::Result<u64> {
    if where_.is_empty() {
        anyhow::bail!("delete_where exige au moins un critère");
    }
    let mut stmt = Query::delete();
    stmt.from_table(schema.alias());
    apply_where(&mut stmt, schema, where_)?;
    run_execute(pool, stmt.build(PostgresQueryBuilder)).await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> TableSchema {
        serde_json::from_value(serde_json::json!({
            "table": "users",
            "columns": [
                {"name": "id", "typ": "int", "primary_key": true},
                {"name": "email", "typ": "str"},
                {"name": "meta", "typ": "json", "nullable": true}
            ]
        }))
        .unwrap()
    }

    #[test]
    fn schema_rejects_unknown_column() {
        let s = schema();
        assert!(s.column("email").is_ok());
        assert!(s.column("nexiste_pas").is_err());
    }

    #[test]
    fn select_sql_is_generated_by_sea_query() {
        let s = schema();
        let mut stmt = Query::select();
        stmt.column(sea_query::Asterisk).from(s.alias());
        let mut w = serde_json::Map::new();
        w.insert("email".into(), serde_json::json!("a@b.c"));
        apply_where(&mut stmt, &s, &w).unwrap();
        let (sql, values) = stmt.build(PostgresQueryBuilder);
        assert_eq!(sql, r#"SELECT * FROM "users" WHERE "email" = $1"#);
        assert_eq!(values.0.len(), 1);
    }

    #[test]
    fn create_table_maps_logical_types() {
        let s = schema();
        let mut create = Table::create();
        create.table(s.alias()).if_not_exists();
        for c in &s.columns {
            create.col(&mut column_def(c).unwrap());
        }
        let sql = create.build(PostgresQueryBuilder);
        assert!(sql.contains("bigserial"), "{sql}");
        assert!(sql.contains(r#""email" text NOT NULL"#), "{sql}");
        assert!(sql.contains(r#""meta" jsonb"#), "{sql}");
    }

    #[test]
    fn unknown_logical_type_rejected() {
        let c = ColumnSpec {
            name: "x".into(),
            typ: "blob".into(),
            nullable: false,
            primary_key: false,
        };
        assert!(column_def(&c).is_err());
    }
}
