//! `vignemale-runtime-core` — the Vignemale runtime core.
//!
//! Proto types generated from `vignemale/proto/` (prost) + logic modules
//! ported from Encore (`runtimes/core`, MPL — cf. `proto/ATTRIBUTION.md`), rebranded.
//!
//! Ported modules: config · proccfg · runtime_config · names · secrets · objects ·
//! api (focused HTTP server) · sqldb (Postgres) · observability (JSON logs).
//! Coming next: queue.

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
