// Ported from `encore/runtimes/core/src/runtime_config/mod.rs`, rebranded Vignemale.
// Higher-level view of the metrics, derived from the RuntimeConfig proto.

use std::collections::HashMap;

use serde::Serialize;

use crate::vignemale::runtime::v1 as rt;

#[derive(Debug, Clone, Serialize)]
pub struct Metric {
    pub name: String,
    pub services: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RuntimeConfig {
    pub metrics: HashMap<String, Metric>,
}

impl RuntimeConfig {
    pub fn new(rt: &rt::RuntimeConfig) -> Self {
        let metrics = rt
            .deployment
            .as_ref()
            .map(|d| {
                d.metrics
                    .iter()
                    .map(|m| {
                        (
                            m.vignemale_name.clone(),
                            Metric {
                                name: m.vignemale_name.clone(),
                                services: m.services.clone(),
                            },
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        Self { metrics }
    }
}
