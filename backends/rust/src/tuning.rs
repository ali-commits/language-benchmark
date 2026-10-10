use serde_json::json;
use std::path::Path;

pub fn parse_parallelism(value: Option<&str>) -> Result<usize, &'static str> {
    match value {
        None => Ok(2),
        Some("1") => Ok(1),
        Some("2") => Ok(2),
        Some("3") => Ok(3),
        Some("4") => Ok(4),
        _ => Err("parallelism must be an integer between 1 and 4"),
    }
}
pub fn setting(name: &str) -> Result<usize, Box<dyn std::error::Error>> {
    match std::env::var(name) {
        Ok(value) => parse_parallelism(Some(&value))
            .map_err(|_| format!("{name} must be an integer between 1 and 4").into()),
        Err(std::env::VarError::NotPresent) => Ok(2),
        Err(error) => Err(error.into()),
    }
}
pub fn write_receipt(
    directory: Option<&str>,
    run_id: &str,
    hashes: usize,
    pool_size: u32,
) -> Result<(), Box<dyn std::error::Error>> {
    let Some(directory) = directory.filter(|d| !d.is_empty()) else {
        return Ok(());
    };
    let pid = std::process::id();
    let payload = json!({"schema_version":1,"run_id":run_id,"pid":pid,"language":"rust","workers_total":1,"connections_per_worker":pool_size,"pool_budget":20,"cpu_parallelism":tokio::runtime::Handle::current().metrics().num_workers(),"hash_concurrency":hashes,"hash_scope":"process"});
    let directory = Path::new(directory);
    let temporary = directory.join(format!(".tuning-{pid}.tmp"));
    let result = (|| {
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(&serde_json::to_vec(&payload)?)?;
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(std::fs::Permissions::from_mode(0o644))?;
        drop(file);
        std::fs::rename(&temporary, directory.join(format!("tuning-{pid}.json")))?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })();
    let _ = std::fs::remove_file(temporary);
    result
}
