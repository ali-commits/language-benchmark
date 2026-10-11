package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"testing"
	"time"
)

func TestHashConcurrencySetting(t *testing.T) {
	t.Setenv("HASH_CONCURRENCY", "2")
	os.Unsetenv("HASH_CONCURRENCY")
	if n, e := hashConcurrency(); e != nil || n != 2 {
		t.Fatalf("default: %d %v", n, e)
	}
	for _, v := range []string{"1", "2", "4"} {
		t.Setenv("HASH_CONCURRENCY", v)
		n, e := hashConcurrency()
		want, _ := strconv.Atoi(v)
		if e != nil || n != want {
			t.Fatalf("%s: %d %v", v, n, e)
		}
	}
	for _, v := range []string{"", "bad", "0", "5", "-1", "+4", " 4", "99999999999999999999999"} {
		t.Setenv("HASH_CONCURRENCY", v)
		if _, e := hashConcurrency(); e == nil {
			t.Fatalf("accepted %q", v)
		}
	}
}
func TestTunedHashAdmissionAndCancellation(t *testing.T) {
	old := hashSlots
	defer func() { hashSlots = old }()
	hashSlots = make(chan struct{}, 4)
	for range 4 {
		if e := acquireHash(context.Background()); e != nil {
			t.Fatal(e)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- acquireHash(ctx) }()
	select {
	case <-done:
		t.Fatal("fifth hash admitted")
	case <-time.After(20 * time.Millisecond):
	}
	cancel()
	select {
	case e := <-done:
		if e != context.Canceled {
			t.Fatal(e)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled waiter stuck")
	}
	if len(hashSlots) != 4 {
		t.Fatal("cancellation released active hash permit")
	}
	for range 4 {
		<-hashSlots
	}
	if e := acquireHash(context.Background()); e != nil {
		t.Fatal(e)
	}
	<-hashSlots
}
func TestRuntimeReceipt(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("RUN_DIR", dir)
	t.Setenv("RUN_ID", "test-run")
	old := hashSlots
	defer func() { hashSlots = old }()
	hashSlots = make(chan struct{}, 4)
	if e := writeTuningReceipt(20); e != nil {
		t.Fatal(e)
	}
	files, e := os.ReadDir(dir)
	if e != nil || len(files) != 1 {
		t.Fatalf("files %v %v", files, e)
	}
	if files[0].Name() != "tuning-"+strconv.Itoa(os.Getpid())+".json" {
		t.Fatal(files[0].Name())
	}
	info, err := files[0].Info()
	if err != nil || info.Mode().Perm() != 0644 {
		t.Fatalf("receipt must be host readable: %v %v", info, err)
	}
	bytes, _ := os.ReadFile(filepath.Join(dir, files[0].Name()))
	var v map[string]any
	if e := json.Unmarshal(bytes, &v); e != nil {
		t.Fatal(e)
	}
	for k, want := range map[string]any{"schema_version": float64(1), "run_id": "test-run", "pid": float64(os.Getpid()), "language": "go", "workers_total": float64(1), "connections_per_worker": float64(20), "pool_budget": float64(20), "cpu_parallelism": float64(runtime.GOMAXPROCS(0)), "hash_concurrency": float64(4), "hash_scope": "process"} {
		if v[k] != want {
			t.Fatalf("%s: %v want %v", k, v[k], want)
		}
	}
	t.Setenv("RUN_DIR", "")
	if e := writeTuningReceipt(20); e != nil {
		t.Fatal(e)
	}
}

func TestPoolBounds(t *testing.T) {
	for _, c := range []struct {
		max, min         string
		wantMax, wantMin int32
	}{{"", "", 20, 0}, {"16", "4", 16, 4}, {"16", "16", 16, 16}} {
		var max, min *string
		if c.max != "" {
			max = &c.max
		}
		if c.min != "" {
			min = &c.min
		}
		a, b, e := poolBounds(max, min)
		if e != nil || a != c.wantMax || b != c.wantMin {
			t.Fatalf("bounds %v %v %v", a, b, e)
		}
	}
	for _, v := range []string{"", "-1", "+16", " 16", "0", "257", "01"} {
		if _, _, e := poolBounds(&v, nil); e == nil {
			t.Fatalf("accepted %q", v)
		}
	}
	a, b := "16", "17"
	if _, _, e := poolBounds(&a, &b); e == nil {
		t.Fatal("min exceeded max")
	}
	t.Setenv("DB_POOL_MAX_SIZE", "16")
	t.Setenv("DB_POOL_MIN_SIZE", "4")
	cfg, e := databaseConfig("postgresql://u:p@localhost/db", "local")
	if e != nil || cfg.MaxConns != 16 || cfg.MinConns != 4 {
		t.Fatalf("actual config: %v %v", cfg, e)
	}
}

func TestProductionReceiptUsesActualPool(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("RUN_DIR", dir)
	t.Setenv("BENCH_PROFILE", "production4")
	if e := writeTuningReceipt(16, 4); e != nil {
		t.Fatal(e)
	}
	bytes, e := os.ReadFile(filepath.Join(dir, "tuning-"+strconv.Itoa(os.Getpid())+".json"))
	if e != nil {
		t.Fatal(e)
	}
	var v map[string]any
	if e = json.Unmarshal(bytes, &v); e != nil {
		t.Fatal(e)
	}
	if v["schema_version"] != float64(2) || v["pool_budget"] != float64(16) || v["pool_min_size"] != float64(4) {
		t.Fatalf("untruthful receipt: %v", v)
	}
	if e := writeTuningReceipt(16); e == nil {
		t.Fatal("missing actual pool minimum accepted")
	}
}
