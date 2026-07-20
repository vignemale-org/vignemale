//! `sqldb` — Postgres: pool, queries, **transactions**, **TLS**, rich types.
//!
//! Ported from Encore's `sqldb` module (same layout: `manager` / `client` /
//! `transaction` / `val`, MPL — cf. proto/ATTRIBUTION.md), adapted to our
//! JSON bridge: the binding passes JSON parameters, we return the rows
//! as JSON.
//!
//! - `manager` — pools per DSN, TLS (custom CA via `VIGNEMALE_SQLDB_CA_CERT`,
//!   the Scaleway Managed Database case), pool configuration;
//! - `client`  — traced query/execute (duration, truncated query, errors);
//! - `transaction` — begin/commit/rollback **without a lifetime** (Encore-style,
//!   to cross the binding);
//! - `val`     — value conversion: JSON -> Postgres bind adapted to the column's
//!   type, and rows -> typed JSON (numeric as string, bytea as
//!   base64, arrays, time/date/uuid…).

mod client;
mod manager;
pub mod orm;
mod transaction;
mod val;

pub use client::{batch, execute, prepare, query};
pub use manager::pool_for_dsn;
pub use transaction::{tx_begin, tx_commit, tx_execute, tx_query, tx_rollback};
pub use val::SqlParam;
