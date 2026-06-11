//! `vignemale-runtime-core` — le cœur runtime de Vignemale.
//!
//! Types proto générés depuis `vignemale/proto/` (prost) + modules de logique
//! portés depuis Encore (`runtimes/core`, MPL — cf. `proto/ATTRIBUTION.md`), rebrandés.
//!
//! Modules portés : config · proccfg · runtime_config · names · secrets · objects ·
//! api (serveur HTTP focalisé) · sqldb (Postgres) · observability (logs JSON).
//! À venir : queue.

pub mod api;
pub mod config;
pub mod names;
pub mod objects;
pub mod observability;
pub mod proccfg;
pub mod runtime_config;
pub mod secrets;
pub mod sqldb;

pub mod vignemale {
    pub mod runtime {
        pub mod v1 {
            include!(concat!(env!("OUT_DIR"), "/vignemale.runtime.v1.rs"));
        }
    }

    pub mod parser {
        pub mod meta {
            pub mod v1 {
                include!(concat!(env!("OUT_DIR"), "/vignemale.parser.meta.v1.rs"));
            }
        }

        pub mod schema {
            pub mod v1 {
                include!(concat!(env!("OUT_DIR"), "/vignemale.parser.schema.v1.rs"));
            }
        }
    }
}
