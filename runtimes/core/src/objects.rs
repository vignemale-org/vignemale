// Object Storage provider — focused S3 client (Scaleway / MinIO / AWS).
//
// The client construction (`LazyS3Client`) is adapted from
// `encore/runtimes/core/src/objects/s3/mod.rs`. The operations are deliberately
// minimal and WITHOUT the `trace` instrumentation or the multi-provider (gcs/noop)
// of Encore's complete subsystem — we will wire all that back later. Enough to
// test end-to-end against Scaleway / MinIO.

use std::sync::Arc;

use aws_sdk_s3 as s3;

use crate::secrets::{Manager, Secret};
use crate::vignemale::runtime::v1 as pb;

/// A handle to an S3 bucket (a cluster + the bucket's cloud name).
pub struct Bucket {
    client: Arc<LazyS3Client>,
    cloud_name: String,
}

/// Builds a `Bucket` from a cluster's config (S3 provider switch) + the
/// logical bucket. Resolves the `secret_access_key` secret via the `secrets` module.
pub fn bucket_from_cluster(cluster: &pb::BucketCluster, bucket: &pb::Bucket) -> anyhow::Result<Bucket> {
    match &cluster.provider {
        Some(pb::bucket_cluster::Provider::S3(s3cfg)) => {
            let secret = s3cfg
                .secret_access_key
                .clone()
                .map(|data| Manager::new(vec![]).load(data));
            let client = Arc::new(LazyS3Client::new(s3cfg.clone(), secret));
            Ok(Bucket {
                client,
                cloud_name: bucket.cloud_name.clone(),
            })
        }
        _ => anyhow::bail!("only the S3 provider is supported for now"),
    }
}

impl Bucket {
    /// Creates the bucket if it does not exist (idempotent).
    pub async fn create_if_not_exists(&self) -> anyhow::Result<()> {
        let client = self.client.get().await;
        match client.create_bucket().bucket(&self.cloud_name).send().await {
            Ok(_) => Ok(()),
            Err(e) => {
                let svc = e.into_service_error();
                if svc.is_bucket_already_owned_by_you() || svc.is_bucket_already_exists() {
                    Ok(())
                } else {
                    Err(anyhow::Error::new(svc))
                }
            }
        }
    }

    /// Writes an object.
    pub async fn put(&self, key: &str, data: Vec<u8>) -> anyhow::Result<()> {
        let client = self.client.get().await;
        client
            .put_object()
            .bucket(&self.cloud_name)
            .key(key)
            .body(s3::primitives::ByteStream::from(data))
            .send()
            .await
            .map_err(|e| anyhow::anyhow!("put_object: {e}"))?;
        Ok(())
    }

    /// Reads the content of an object.
    pub async fn get(&self, key: &str) -> anyhow::Result<Vec<u8>> {
        let client = self.client.get().await;
        let out = client
            .get_object()
            .bucket(&self.cloud_name)
            .key(key)
            .send()
            .await
            .map_err(|e| anyhow::anyhow!("get_object: {e}"))?;
        let data = out
            .body
            .collect()
            .await
            .map_err(|e| anyhow::anyhow!("read body: {e}"))?;
        Ok(data.into_bytes().to_vec())
    }

    /// Indicates whether an object exists.
    pub async fn exists(&self, key: &str) -> anyhow::Result<bool> {
        let client = self.client.get().await;
        match client.head_object().bucket(&self.cloud_name).key(key).send().await {
            Ok(_) => Ok(true),
            Err(e) => {
                let svc = e.into_service_error();
                if svc.is_not_found() {
                    Ok(false)
                } else {
                    Err(anyhow::Error::new(svc))
                }
            }
        }
    }

    /// Lists the keys under a prefix.
    pub async fn list(&self, prefix: &str) -> anyhow::Result<Vec<String>> {
        let client = self.client.get().await;
        let out = client
            .list_objects_v2()
            .bucket(&self.cloud_name)
            .prefix(prefix)
            .send()
            .await
            .map_err(|e| anyhow::anyhow!("list_objects_v2: {e}"))?;
        Ok(out
            .contents()
            .iter()
            .filter_map(|o| o.key().map(|s| s.to_string()))
            .collect())
    }

    /// Deletes an object.
    pub async fn delete(&self, key: &str) -> anyhow::Result<()> {
        let client = self.client.get().await;
        client
            .delete_object()
            .bucket(&self.cloud_name)
            .key(key)
            .send()
            .await
            .map_err(|e| anyhow::anyhow!("delete_object: {e}"))?;
        Ok(())
    }
}

/// Lazily built S3 client (adapted from Encore's `s3/mod.rs`).
struct LazyS3Client {
    cfg: pb::bucket_cluster::S3,
    secret_access_key: Option<Secret>,
    cell: tokio::sync::OnceCell<Arc<s3::Client>>,
}

impl LazyS3Client {
    fn new(cfg: pb::bucket_cluster::S3, secret_access_key: Option<Secret>) -> Self {
        Self {
            cfg,
            secret_access_key,
            cell: tokio::sync::OnceCell::new(),
        }
    }

    async fn get(&self) -> &Arc<s3::Client> {
        self.cell
            .get_or_init(|| async {
                let region = aws_config::Region::new(self.cfg.region.clone());
                // HTTP client in rustls-ring (not aws-lc-rs): pure Rust TLS,
                // portable wheels (aarch64 cross-compilation included).
                let http_client = aws_smithy_http_client::Builder::new()
                    .tls_provider(aws_smithy_http_client::tls::Provider::Rustls(
                        aws_smithy_http_client::tls::rustls_provider::CryptoMode::Ring,
                    ))
                    .build_https();
                let mut builder = aws_config::defaults(aws_config::BehaviorVersion::latest())
                    .http_client(http_client)
                    .region(region);
                if let Some(endpoint) = self.cfg.endpoint.as_ref() {
                    builder = builder.endpoint_url(endpoint.clone());
                }

                if let (Some(access_key_id), Some(secret_access_key)) = (
                    self.cfg.access_key_id.as_ref(),
                    self.secret_access_key.as_ref(),
                ) {
                    use aws_credential_types::Credentials;
                    let secret_access_key = secret_access_key
                        .get()
                        .expect("unable to resolve s3 secret access key");
                    let secret_access_key = std::str::from_utf8(secret_access_key)
                        .expect("unable to parse s3 secret access key as utf-8");

                    builder = builder.credentials_provider(Credentials::new(
                        access_key_id,
                        secret_access_key,
                        None,
                        None,
                        "vignemale-runtime",
                    ));
                }

                let aws_cfg = builder.load().await;
                // force_path_style: essential for MinIO and custom endpoints.
                let s3_conf = s3::config::Builder::from(&aws_cfg)
                    .force_path_style(true)
                    .build();
                Arc::new(s3::Client::from_conf(s3_conf))
            })
            .await
    }
}
