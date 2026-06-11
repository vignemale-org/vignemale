//! `sqldb` — Postgres : pool, requêtes, **transactions**, **TLS**, types riches.
//!
//! Porté du module `sqldb` d'Encore (même découpage : `manager` / `client` /
//! `transaction` / `val`, MPL — cf. proto/ATTRIBUTION.md), adapté à notre
//! pont JSON : le binding passe des paramètres JSON, on renvoie les lignes
//! en JSON.
//!
//! - `manager` — pools par DSN, TLS (CA custom via `VIGNEMALE_SQLDB_CA_CERT`,
//!   le cas Scaleway Managed Database), config du pool ;
//! - `client`  — query/execute tracées (durée, requête tronquée, erreurs) ;
//! - `transaction` — begin/commit/rollback **sans lifetime** (façon Encore,
//!   pour traverser le binding) ;
//! - `val`     — conversion des valeurs : JSON → bind Postgres adapté au type
//!   de la colonne, et lignes → JSON typé (numeric en string, bytea en
//!   base64, arrays, time/date/uuid…).

mod client;
mod manager;
mod transaction;
mod val;

pub use client::{execute, query};
pub use manager::pool_for_dsn;
pub use transaction::{tx_begin, tx_commit, tx_execute, tx_query, tx_rollback};
pub use val::SqlParam;
