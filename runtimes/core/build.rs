// Génère les types Rust depuis les .proto (prost), comme `runtimes/core/build.rs`
// d'Encore. Les imports (schema, infra, secretdata) sont compilés transitivement.

fn main() -> std::io::Result<()> {
    prost_build::compile_protos(
        &[
            "../../proto/vignemale/runtime/v1/runtime.proto",
            "../../proto/vignemale/parser/meta/v1/meta.proto",
        ],
        &["../../proto/"],
    )?;
    Ok(())
}
