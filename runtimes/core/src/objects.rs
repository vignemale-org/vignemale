// Provider Object Storage — client S3 focalisé (Scaleway / MinIO / AWS).
//
// La construction du client (`LazyS3Client`) est adaptée de
// `encore/runtimes/core/src/objects/s3/mod.rs`. Les opérations sont volontairement
// minimales et SANS l'instrumentation `trace` ni le multi-provider (gcs/noop) du
// subsystem complet d'Encore — on rebranchera tout ça plus tard. Suffisant pour
// tester de bout en bout contre Scaleway / MinIO.

use std::sync::Arc;

use aws_sdk_s3 as s3;

use crate::secrets::{Manager, Secret};
use crate::vignemale::runtime::v1 as pb;

/// Une poignée vers un bucket S3 (un cluster + le nom cloud du bucket).
pub struct Bucket {
    client: Arc<LazyS3Client>,
    cloud_name: String,
}

/// Construit un `Bucket` depuis la config d'un cluster (provider switch S3) + le
/// bucket logique. Résout le secret `secret_access_key` via le module `secrets`.
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
        _ => anyhow::bail!("seul le provider S3 est supporté pour l'instant"),
    }
}

impl Bucket {
    /// Crée le bucket s'il n'existe pas (idempotent).
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

    /// Écrit un objet.
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

    /// Lit le contenu d'un objet.
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

    /// Indique si un objet existe.
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

    /// Liste les clés sous un préfixe.
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

    /// Supprime un objet.
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

/// Client S3 construit paresseusement (adapté de `s3/mod.rs` d'Encore).
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
                let mut builder =
                    aws_config::defaults(aws_config::BehaviorVersion::latest()).region(region);
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
                // force_path_style : indispensable pour MinIO et les endpoints custom.
                let s3_conf = s3::config::Builder::from(&aws_cfg)
                    .force_path_style(true)
                    .build();
                Arc::new(s3::Client::from_conf(s3_conf))
            })
            .await
    }
}
