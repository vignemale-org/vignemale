// Génère les types Rust depuis les .proto (prost), comme `runtimes/core/build.rs`
// d'Encore. Les imports (schema, infra, secretdata) sont compilés transitivement.

fn main() -> std::io::Result<()> {
    // protoc hermétique : on utilise le binaire vendoré pour l'arch hôte (sauf si
    // PROTOC est déjà défini dans l'environnement, qui prime). Les types
    // bien-connus (google/protobuf/*) viennent de l'include vendoré.
    let vendored_include = protoc_bin_vendored::include_path()
        .expect("include protoc vendoré")
        .to_string_lossy()
        .into_owned();
    if std::env::var_os("PROTOC").is_none() {
        std::env::set_var(
            "PROTOC",
            protoc_bin_vendored::protoc_bin_path().expect("protoc vendoré"),
        );
    }
    prost_build::compile_protos(
        &[
            "../../proto/vignemale/runtime/v1/runtime.proto",
            "../../proto/vignemale/parser/meta/v1/meta.proto",
        ],
        &["../../proto/", &vendored_include],
    )?;
    Ok(())
}
