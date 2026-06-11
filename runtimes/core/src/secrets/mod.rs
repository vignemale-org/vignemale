// Porté de `encore/runtimes/core/src/secrets/mod.rs`, rebrandé.
// Résout un `SecretData` (env / embedded, encodage none/base64/gzip, sous-clé JSON)
// en sa valeur réelle. Résolution paresseuse + mise en cache (OnceLock).
// Le bloc de tests inline d'Encore est retiré (on teste depuis Python).

use std::collections::HashMap;
use std::fmt::Display;
use std::io::Read as _;
use std::sync::{Arc, OnceLock};

use base64::{engine::general_purpose, Engine as _};
use flate2::read::GzDecoder;

use crate::names::VignemaleName;
use crate::vignemale::runtime::v1 as pb;
use pb::secret_data::{Encoding, Source, SubPath};
use pb::{AppSecret, SecretData};

pub struct Manager {
    app_secrets: HashMap<VignemaleName, Arc<Secret>>,
}

impl Manager {
    pub fn new(app_secrets: Vec<AppSecret>) -> Self {
        let app_secrets = app_secrets
            .into_iter()
            .filter_map(|s| {
                s.data
                    .map(|data| (s.vignemale_name.into(), Arc::new(Secret::new(data))))
            })
            .collect();
        Self { app_secrets }
    }

    pub fn load(&self, data: SecretData) -> Secret {
        Secret::new(data)
    }

    /// Retrieve the secret for the given vignemale name.
    /// If the secret is not found, returns None.
    pub fn app_secret(&self, name: VignemaleName) -> Option<Arc<Secret>> {
        self.app_secrets.get(&name).cloned()
    }
}

pub struct Secret {
    data: SecretData,
    resolved: OnceLock<ResolveResult<Vec<u8>>>,
}

impl Secret {
    fn new(data: SecretData) -> Self {
        Self {
            data,
            resolved: OnceLock::new(),
        }
    }

    pub fn new_for_test(plaintext: &'static str) -> Self {
        Self::new(SecretData {
            source: Some(Source::Embedded(plaintext.as_bytes().to_vec())),
            sub_path: None,
            encoding: Encoding::None as i32,
        })
    }

    pub fn get(&self) -> Result<&[u8], ResolveError> {
        let result = self.resolved.get_or_init(|| resolve(&self.data)).as_deref();
        match result {
            Ok(bytes) => Ok(bytes),
            Err(err) => Err(*err),
        }
    }
}

const BASE64: general_purpose::GeneralPurpose = general_purpose::STANDARD;

#[derive(Debug, Copy, Clone)]
pub enum ResolveError {
    EnvVarNotFound,
    JsonKeyNotFound,
    JsonValueNotString,
    InvalidBase64,
    InvalidJSON,
    InvalidJSONValue,
    InvalidGzip,
    InvalidSecretSource,
    UnknownEncoding,
}

impl std::error::Error for ResolveError {}

impl Display for ResolveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ResolveError::EnvVarNotFound => write!(f, "environment variable not found"),
            ResolveError::JsonKeyNotFound => write!(f, "JSON key not found"),
            ResolveError::JsonValueNotString => write!(f, "JSON value is not a string"),
            ResolveError::InvalidBase64 => write!(f, "invalid base64"),
            ResolveError::InvalidGzip => write!(f, "invalid gzip data"),
            ResolveError::InvalidJSON => write!(f, "invalid JSON"),
            ResolveError::InvalidJSONValue => write!(f, "invalid JSON value encoding"),
            ResolveError::InvalidSecretSource => write!(f, "invalid secret source"),
            ResolveError::UnknownEncoding => write!(f, "unknown encoding"),
        }
    }
}

type ResolveResult<T> = Result<T, ResolveError>;

fn resolve(data: &SecretData) -> ResolveResult<Vec<u8>> {
    let value = match &data.source {
        Some(Source::Embedded(data)) => data.clone(),
        Some(Source::Env(name)) => {
            let value = std::env::var(name).map_err(|_| ResolveError::EnvVarNotFound)?;
            value.into_bytes()
        }
        None => Err(ResolveError::InvalidSecretSource)?,
    };

    // Shall we decode this?
    let encoding = Encoding::try_from(data.encoding).map_err(|_| ResolveError::UnknownEncoding)?;
    let value = match encoding {
        Encoding::None => value,
        Encoding::Gzip => {
            let compressed = BASE64
                .decode(value)
                .map_err(|_| ResolveError::InvalidBase64)?;
            let mut decoder = GzDecoder::new(&compressed[..]);
            let mut decompressed = Vec::new();
            decoder
                .read_to_end(&mut decompressed)
                .map_err(|_| ResolveError::InvalidGzip)?;
            decompressed
        }
        Encoding::Base64 => BASE64
            .decode(&value)
            .map_err(|_| ResolveError::InvalidBase64)?,
    };

    // Is there a subpath?
    match &data.sub_path {
        None => Ok(value),

        Some(SubPath::JsonKey(json_key)) => {
            // Escape the JSON key since we use gjson.
            let json_key = escape_gjson_key(json_key);

            let str_value = std::str::from_utf8(&value).map_err(|_| ResolveError::InvalidJSON)?;
            let value = gjson::get(str_value, &json_key);
            match value.kind() {
                gjson::Kind::String => {
                    // Use the string as-is.
                    Ok(value.str().as_bytes().to_vec())
                }

                gjson::Kind::Object => {
                    // Iterate over the keys to find the first "bytes" or "string" key.
                    let mut result: Option<ResolveResult<Vec<u8>>> = None;
                    let iter = |key: gjson::Value, value: gjson::Value| {
                        match key.str() {
                            "bytes" => {
                                // Decode the bytes from base64.
                                let res = BASE64
                                    .decode(value.str())
                                    .map_err(|_| ResolveError::InvalidBase64);
                                result = Some(res);
                            }
                            "string" => {
                                // Use the string as-is.
                                result = Some(Ok(value.str().as_bytes().to_vec()));
                            }
                            _ => {}
                        }
                        result.is_some()
                    };
                    value.each(iter);
                    result.unwrap_or(Err(ResolveError::InvalidJSONValue))
                }

                gjson::Kind::Null => Err(ResolveError::JsonKeyNotFound),
                _ => Err(ResolveError::JsonValueNotString),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn embedded(value: &[u8], encoding: Encoding, sub_path: Option<SubPath>) -> Secret {
        Secret::new(SecretData {
            source: Some(Source::Embedded(value.to_vec())),
            sub_path,
            encoding: encoding as i32,
        })
    }

    #[test]
    fn embedded_plain() {
        assert_eq!(embedded(b"hello", Encoding::None, None).get().unwrap(), b"hello");
    }

    #[test]
    fn embedded_base64() {
        let b64 = BASE64.encode(b"hello");
        assert_eq!(
            embedded(b64.as_bytes(), Encoding::Base64, None).get().unwrap(),
            b"hello"
        );
    }

    #[test]
    fn embedded_gzip() {
        use std::io::Write as _;
        let mut enc =
            flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(b"hello gzip").unwrap();
        let b64 = BASE64.encode(enc.finish().unwrap());
        assert_eq!(
            embedded(b64.as_bytes(), Encoding::Gzip, None).get().unwrap(),
            b"hello gzip"
        );
    }

    #[test]
    fn env_source() {
        std::env::set_var("VIGNEMALE_TEST_SECRET_ENV", "from-env");
        let s = Secret::new(SecretData {
            source: Some(Source::Env("VIGNEMALE_TEST_SECRET_ENV".into())),
            sub_path: None,
            encoding: Encoding::None as i32,
        });
        assert_eq!(s.get().unwrap(), b"from-env");
    }

    #[test]
    fn env_source_missing() {
        let s = Secret::new(SecretData {
            source: Some(Source::Env("VIGNEMALE_TEST_SECRET_ABSENT".into())),
            sub_path: None,
            encoding: Encoding::None as i32,
        });
        assert!(matches!(s.get(), Err(ResolveError::EnvVarNotFound)));
    }

    #[test]
    fn json_key() {
        let s = embedded(
            br#"{"foo": "bar"}"#,
            Encoding::None,
            Some(SubPath::JsonKey("foo".into())),
        );
        assert_eq!(s.get().unwrap(), b"bar");
    }

    #[test]
    fn json_key_missing() {
        let s = embedded(
            br#"{"foo": "bar"}"#,
            Encoding::None,
            Some(SubPath::JsonKey("nope".into())),
        );
        assert!(matches!(s.get(), Err(ResolveError::JsonKeyNotFound)));
    }

    #[test]
    fn json_key_not_a_string() {
        let s = embedded(
            br#"{"foo": 42}"#,
            Encoding::None,
            Some(SubPath::JsonKey("foo".into())),
        );
        assert!(matches!(s.get(), Err(ResolveError::JsonValueNotString)));
    }

    #[test]
    fn missing_source() {
        let s = Secret::new(SecretData {
            source: None,
            sub_path: None,
            encoding: Encoding::None as i32,
        });
        assert!(matches!(s.get(), Err(ResolveError::InvalidSecretSource)));
    }

    #[test]
    fn invalid_base64() {
        let s = embedded(b"%%% pas du base64 %%%", Encoding::Base64, None);
        assert!(matches!(s.get(), Err(ResolveError::InvalidBase64)));
    }

    #[test]
    fn manager_app_secret() {
        let mgr = Manager::new(vec![AppSecret {
            rid: "sec-1".to_string(),
            vignemale_name: "api_key".to_string(),
            data: Some(SecretData {
                source: Some(Source::Embedded(b"sk-123".to_vec())),
                sub_path: None,
                encoding: Encoding::None as i32,
            }),
        }]);
        let s = mgr.app_secret("api_key".into()).expect("secret déclaré");
        assert_eq!(s.get().unwrap(), b"sk-123");
        assert!(mgr.app_secret("absent".into()).is_none());
    }
}

fn escape_gjson_key(key: &str) -> String {
    fn is_safe_path_key_char(c: char) -> bool {
        c.is_ascii_lowercase()
            || c.is_ascii_uppercase()
            || c.is_ascii_digit()
            || c <= ' '
            || c > '~'
            || c == '_'
            || c == '-'
            || c == ':'
    }

    let mut escaped = String::with_capacity(key.len());
    for c in key.chars() {
        if is_safe_path_key_char(c) {
            escaped.push(c);
        } else {
            escaped.push('\\');
            escaped.push(c);
        }
    }
    escaped
}
