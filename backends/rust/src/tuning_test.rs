use super::*;
#[test]
fn settings() {
    assert_eq!(parse_parallelism(None).unwrap(), 2);
    assert_eq!(parse_parallelism(Some("4")).unwrap(), 4);
    for s in ["", "bad", "0", "5", "-1", "+4", " 4", "999999999999999999"] {
        assert!(parse_parallelism(Some(s)).is_err(), "accepted {s}");
    }
}
#[test]
fn receipt_observes_runtime() {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .enable_all()
        .build()
        .unwrap();
    let dir = std::env::temp_dir().join(format!("lb-tuning-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    rt.block_on(async {
        write_receipt(Some(dir.to_str().unwrap()), "test-run", 4, 20, 0).unwrap();
        use std::os::unix::fs::PermissionsExt;
        let receipt = dir.join(format!("tuning-{}.json", std::process::id()));
        assert_eq!(
            std::fs::metadata(&receipt).unwrap().permissions().mode() & 0o777,
            0o644,
            "receipt must remain host readable under a restrictive umask"
        );
        let value: serde_json::Value = serde_json::from_slice(
            &std::fs::read(dir.join(format!("tuning-{}.json", std::process::id()))).unwrap(),
        )
        .unwrap();
        assert_eq!(value["cpu_parallelism"], 4);
        assert_eq!(value["pid"], std::process::id());
        assert_eq!(value["hash_concurrency"], 4);
        assert_eq!(value["pool_budget"], 20);
        assert_eq!(value["run_id"], "test-run");
        if std::env::var("BENCH_PROFILE").as_deref() == Ok("production4") {
            assert_eq!(value["schema_version"], 2);
            assert_eq!(value["pool_min_size"], 0);
        } else {
            assert_eq!(value["schema_version"], 1);
            assert!(value.get("pool_min_size").is_none());
        }
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 1);
        write_receipt(None, "unused", 4, 20, 0).unwrap();
    });
    std::fs::remove_dir_all(dir).unwrap();
}

#[tokio::test]
async fn tuned_hash_queue_cancellation_keeps_active_permits() {
    let hashes = Arc::new(Semaphore::new(4));
    let state = App {
        pool: PgPoolOptions::new()
            .connect_lazy("postgresql://postgres:test@localhost:9/language_benchmark_rust_test")
            .unwrap(),
        secret: Arc::new("s".repeat(32)),
        ttl: 3600,
        hashes: hashes.clone(),
        dummy: Arc::new(String::new()),
        acquires: Arc::new(AtomicU64::new(0)),
        acquire_ns: Arc::new(AtomicU64::new(0)),
    };
    let active = hashes.clone().acquire_many_owned(4).await.unwrap();
    assert!(hashes.try_acquire().is_err());
    let waiter = tokio::spawn(async move { state.password_hash("queued-password".into()).await });
    tokio::task::yield_now().await;
    assert!(!waiter.is_finished());
    waiter.abort();
    assert!(waiter.await.err().unwrap().is_cancelled());
    assert_eq!(hashes.available_permits(), 0);
    drop(active);
    assert_eq!(hashes.available_permits(), 4);
}

#[test]
fn blocking_hash_owns_permit_after_request_abort() {
    use std::future::Future;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .max_blocking_threads(1)
        .enable_all()
        .build()
        .unwrap();
    let entered = runtime.enter();
    let hashes = Arc::new(Semaphore::new(4));
    let state = App {
        pool: PgPoolOptions::new()
            .connect_lazy("postgresql://postgres:test@localhost:9/language_benchmark_rust_test")
            .unwrap(),
        secret: Arc::new("s".repeat(32)),
        ttl: 3600,
        hashes: hashes.clone(),
        dummy: Arc::new(String::new()),
        acquires: Arc::new(AtomicU64::new(0)),
        acquire_ns: Arc::new(AtomicU64::new(0)),
    };
    // Queue the actual password job behind an occupied blocking thread.
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let blocker = runtime.spawn_blocking(move || {
        started_tx.send(()).unwrap();
        release_rx.recv().unwrap();
    });
    started_rx.recv().unwrap();
    let mut job = Box::pin(state.password_hash("twelve-chars-password".into()));
    let mut context = std::task::Context::from_waker(std::task::Waker::noop());
    assert!(job.as_mut().poll(&mut context).is_pending());
    drop(job);
    assert_eq!(
        hashes.available_permits(),
        3,
        "queued blocking hash must retain its slot after request cancellation"
    );
    drop(entered);
    release_tx.send(()).unwrap();
    runtime.block_on(blocker).unwrap();
    runtime.block_on(async {
        let permits = hashes.acquire_many(4).await.unwrap();
        drop(permits);
    });
    assert_eq!(hashes.available_permits(), 4);
}

#[test]
fn production_pool_bounds() {
    assert_eq!(parse_pool_bounds(None, None).unwrap(), (20, 0));
    assert_eq!(parse_pool_bounds(Some("16"), Some("4")).unwrap(), (16, 4));
    for v in ["", "-1", "+16", " 16", "0", "257", "01"] {
        assert!(parse_pool_bounds(Some(v), None).is_err());
    }
    assert!(parse_pool_bounds(Some("16"), Some("17")).is_err());
}
