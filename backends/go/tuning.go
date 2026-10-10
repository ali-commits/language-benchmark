package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
)

func hashConcurrency() (int, error) {
	value, present := os.LookupEnv("HASH_CONCURRENCY")
	if !present {
		return 2, nil
	}
	if len(value) != 1 || value[0] < '1' || value[0] > '4' {
		return 0, fmt.Errorf("HASH_CONCURRENCY must be an integer between 1 and 4")
	}
	return int(value[0] - '0'), nil
}

func writeTuningReceipt(poolSize int32) error {
	dir := os.Getenv("RUN_DIR")
	if dir == "" {
		return nil
	}
	payload := map[string]any{"schema_version": 1, "run_id": os.Getenv("RUN_ID"), "pid": os.Getpid(), "language": "go", "workers_total": 1, "connections_per_worker": poolSize, "pool_budget": 20, "cpu_parallelism": runtime.GOMAXPROCS(0), "hash_concurrency": cap(hashSlots), "hash_scope": "process"}
	file, err := os.CreateTemp(dir, ".tuning-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err = json.NewEncoder(file).Encode(payload); err != nil {
		file.Close()
		return err
	}
	if err = file.Chmod(0644); err != nil {
		file.Close()
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), filepath.Join(dir, "tuning-"+strconv.Itoa(os.Getpid())+".json"))
}
