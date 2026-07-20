// Generates the Rust types from the .proto files (prost), like Encore's
// `runtimes/core/build.rs`. The imports (schema, infra, secretdata) are compiled
// transitively.

fn main() -> std::io::Result<()> {
    // Hermetic protoc: we use the vendored binary for the host arch (unless
    // PROTOC is already set in the environment, which takes precedence). The
    // well-known types (google/protobuf/*) come from the vendored include.
    let vendored_include = protoc_bin_vendored::include_path()
        .expect("vendored protoc include")
        .to_string_lossy()
        .into_owned();
    if std::env::var_os("PROTOC").is_none() {
        std::env::set_var(
            "PROTOC",
            protoc_bin_vendored::protoc_bin_path().expect("vendored protoc"),
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
