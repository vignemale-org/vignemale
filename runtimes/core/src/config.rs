// Chargement de la config depuis l'environnement.
// Porté de `encore/runtimes/core/src/lib.rs` (fonctions *_from_env), rebrandé.
//
// Pour l'instant : chemin **protobuf binaire** uniquement (var d'env, base64,
// gzip optionnel) + fichier. Le chemin JSON lisible (`infracfg.rs`, le gros
// mapping infra → runtime) sera porté ensuite. `enable_test_mode` (qui shell-out
// vers le CLI) est volontairement laissé de côté.

use std::fmt::Display;
use std::io::Read;
use std::path::Path;

use base64::Engine;
use prost::Message;

use crate::proccfg;
use crate::vignemale::parser::meta::v1 as metapb;
use crate::vignemale::runtime::v1 as runtimepb;

#[derive(Debug)]
pub enum ParseError {
    EnvNotPresent,
    EnvVar(std::env::VarError),
    Base64(base64::DecodeError),
    Proto(prost::DecodeError),
    IO(std::io::Error),
}

impl Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::EnvNotPresent => write!(f, "environment variable not present"),
            ParseError::EnvVar(e) => write!(f, "failed to read environment variable: {e}"),
            ParseError::Base64(e) => write!(f, "failed to decode environment variable: {e}"),
            ParseError::Proto(e) => write!(f, "failed to parse environment variable: {e}"),
            ParseError::IO(e) => write!(f, "failed to read file: {e}"),
        }
    }
}

impl std::error::Error for ParseError {}

/// Charge la `RuntimeConfig` depuis `VIGNEMALE_RUNTIME_CONFIG` (base64, gzip
/// optionnel) ou depuis le fichier pointé par `VIGNEMALE_RUNTIME_CONFIG_PATH`.
pub fn runtime_config_from_env() -> Result<runtimepb::RuntimeConfig, ParseError> {
    let cfg = match std::env::var("VIGNEMALE_RUNTIME_CONFIG") {
        Ok(cfg) => cfg,
        Err(std::env::VarError::NotPresent) => {
            // Not present. Check the VIGNEMALE_RUNTIME_CONFIG_PATH environment variable.
            match std::env::var("VIGNEMALE_RUNTIME_CONFIG_PATH") {
                Ok(path) => {
                    let path = Path::new(&path);
                    return parse_runtime_config(path);
                }
                Err(std::env::VarError::NotPresent) => return Err(ParseError::EnvNotPresent),
                Err(e) => return Err(ParseError::EnvVar(e)),
            }
        }
        Err(e) => return Err(ParseError::EnvVar(e)),
    };

    if cfg.starts_with("gzip:") {
        // Parse the remainder as base64-encoded gzip data.
        let cfg = cfg.as_bytes();
        let cfg = &cfg["gzip:".len()..];
        let gzip_data = base64::engine::general_purpose::STANDARD
            .decode(cfg)
            .map_err(ParseError::Base64)?;

        let mut decoder = flate2::read::GzDecoder::new(&gzip_data[..]);
        let mut raw_data = Vec::new();
        decoder.read_to_end(&mut raw_data).map_err(ParseError::IO)?;
        runtimepb::RuntimeConfig::decode(&raw_data[..]).map_err(ParseError::Proto)
    } else {
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(cfg.as_bytes())
            .map_err(ParseError::Base64)?;
        runtimepb::RuntimeConfig::decode(&decoded[..]).map_err(ParseError::Proto)
    }
}

fn parse_runtime_config(path: &Path) -> Result<runtimepb::RuntimeConfig, ParseError> {
    let data = std::fs::read(path).map_err(ParseError::IO)?;
    runtimepb::RuntimeConfig::decode(&data[..]).map_err(ParseError::Proto)
}

/// Charge les métadonnées d'app (`Data`) depuis `VIGNEMALE_APP_META` (base64,
/// gzip optionnel) ou le fichier `VIGNEMALE_APP_META_PATH`.
pub fn meta_from_env() -> Result<metapb::Data, ParseError> {
    let cfg = match std::env::var("VIGNEMALE_APP_META") {
        Ok(cfg) => cfg,
        Err(std::env::VarError::NotPresent) => {
            // Not present. Check the VIGNEMALE_APP_META_PATH environment variable.
            match std::env::var("VIGNEMALE_APP_META_PATH") {
                Ok(path) => {
                    let path = Path::new(&path);
                    return parse_meta(path);
                }
                Err(std::env::VarError::NotPresent) => return Err(ParseError::EnvNotPresent),
                Err(e) => return Err(ParseError::EnvVar(e)),
            }
        }
        Err(e) => return Err(ParseError::EnvVar(e)),
    };

    if cfg.starts_with("gzip:") {
        // Parse the remainder as base64-encoded gzip data.
        let cfg = cfg.as_bytes();
        let cfg = &cfg["gzip:".len()..];
        let gzip_data = base64::engine::general_purpose::STANDARD
            .decode(cfg)
            .map_err(ParseError::Base64)?;

        let mut decoder = flate2::read::GzDecoder::new(&gzip_data[..]);
        let mut raw_data = Vec::new();
        decoder.read_to_end(&mut raw_data).map_err(ParseError::IO)?;
        metapb::Data::decode(&raw_data[..]).map_err(ParseError::Proto)
    } else {
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(cfg.as_bytes())
            .map_err(ParseError::Base64)?;
        metapb::Data::decode(&decoded[..]).map_err(ParseError::Proto)
    }
}

fn parse_meta(path: &Path) -> Result<metapb::Data, ParseError> {
    let data = std::fs::read(path).map_err(ParseError::IO)?;
    metapb::Data::decode(&data[..]).map_err(ParseError::Proto)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // Les tests manipulent les mêmes variables d'environnement (état process) :
    // on les sérialise pour éviter les courses entre threads de test.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn demo_cfg() -> runtimepb::RuntimeConfig {
        runtimepb::RuntimeConfig {
            environment: Some(runtimepb::Environment {
                app_id: "myapp".to_string(),
                ..Default::default()
            }),
            ..Default::default()
        }
    }

    fn clear_env() {
        std::env::remove_var("VIGNEMALE_RUNTIME_CONFIG");
        std::env::remove_var("VIGNEMALE_RUNTIME_CONFIG_PATH");
    }

    fn app_id(cfg: &runtimepb::RuntimeConfig) -> &str {
        &cfg.environment.as_ref().unwrap().app_id
    }

    #[test]
    fn from_env_base64() {
        let _g = ENV_LOCK.lock().unwrap();
        clear_env();
        let b64 =
            base64::engine::general_purpose::STANDARD.encode(demo_cfg().encode_to_vec());
        std::env::set_var("VIGNEMALE_RUNTIME_CONFIG", b64);
        let cfg = runtime_config_from_env().unwrap();
        assert_eq!(app_id(&cfg), "myapp");
        clear_env();
    }

    #[test]
    fn from_env_gzip() {
        use std::io::Write as _;
        let _g = ENV_LOCK.lock().unwrap();
        clear_env();
        let mut enc =
            flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(&demo_cfg().encode_to_vec()).unwrap();
        let b64 = base64::engine::general_purpose::STANDARD.encode(enc.finish().unwrap());
        std::env::set_var("VIGNEMALE_RUNTIME_CONFIG", format!("gzip:{b64}"));
        let cfg = runtime_config_from_env().unwrap();
        assert_eq!(app_id(&cfg), "myapp");
        clear_env();
    }

    #[test]
    fn from_file_path() {
        let _g = ENV_LOCK.lock().unwrap();
        clear_env();
        let path =
            std::env::temp_dir().join(format!("vignemale-cfg-test-{}.pb", std::process::id()));
        std::fs::write(&path, demo_cfg().encode_to_vec()).unwrap();
        std::env::set_var("VIGNEMALE_RUNTIME_CONFIG_PATH", &path);
        let cfg = runtime_config_from_env().unwrap();
        assert_eq!(app_id(&cfg), "myapp");
        clear_env();
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn env_not_present() {
        let _g = ENV_LOCK.lock().unwrap();
        clear_env();
        assert!(matches!(
            runtime_config_from_env(),
            Err(ParseError::EnvNotPresent)
        ));
    }

    #[test]
    fn invalid_base64_rejected() {
        let _g = ENV_LOCK.lock().unwrap();
        clear_env();
        std::env::set_var("VIGNEMALE_RUNTIME_CONFIG", "%%% pas du base64 %%%");
        assert!(matches!(
            runtime_config_from_env(),
            Err(ParseError::Base64(_))
        ));
        clear_env();
    }
}

/// Charge l'éventuelle `ProcessConfig` depuis `VIGNEMALE_PROCESS_CONFIG`
/// (base64 d'un JSON).
pub fn proc_config_from_env() -> Result<Option<proccfg::ProcessConfig>, ParseError> {
    let encoded_config = match std::env::var("VIGNEMALE_PROCESS_CONFIG") {
        Ok(config) => config,
        Err(std::env::VarError::NotPresent) => return Ok(None),
        Err(e) => return Err(ParseError::EnvVar(e)),
    };

    let decoded = base64::engine::general_purpose::STANDARD
        .decode(encoded_config)
        .map_err(ParseError::Base64)?;

    let json_str = String::from_utf8(decoded)
        .map_err(|e| ParseError::IO(std::io::Error::new(std::io::ErrorKind::InvalidData, e)))?;

    let config = serde_json::from_str(&json_str)
        .map_err(|e| ParseError::IO(std::io::Error::new(std::io::ErrorKind::InvalidData, e)))?;

    Ok(Some(config))
}
