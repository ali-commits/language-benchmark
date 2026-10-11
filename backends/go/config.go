package main

import (
	"errors"
	"github.com/jackc/pgx/v5/pgxpool"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

func validTTL(ttl int64) bool { return ttl >= 600 && ttl <= 86400 }
func databaseConfig(dsn, mode string) (*pgxpool.Config, error) {
	dsn = strings.Replace(dsn, "postgresql+psycopg://", "postgresql://", 1)
	u, e := url.Parse(dsn)
	if e != nil || u.Scheme != "postgresql" {
		return nil, errors.New("PostgreSQL required")
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return nil, e
	}
	if mode == "aws" {
		ssl, root := q["sslmode"], q["sslrootcert"]
		if len(ssl) != 1 || ssl[0] != "verify-full" || len(root) != 1 || strings.TrimSpace(root[0]) == "" {
			return nil, errors.New("AWS requires sslmode=verify-full and sslrootcert")
		}
	}
	cfg, e := pgxpool.ParseConfig(dsn)
	if e != nil {
		return nil, e
	}
	maxValue, maxPresent := os.LookupEnv("DB_POOL_MAX_SIZE")
	minValue, minPresent := os.LookupEnv("DB_POOL_MIN_SIZE")
	var maxSetting, minSetting *string
	if maxPresent {
		maxSetting = &maxValue
	}
	if minPresent {
		minSetting = &minValue
	}
	cfg.MaxConns, cfg.MinConns, e = poolBounds(maxSetting, minSetting)
	if e != nil {
		return nil, e
	}
	cfg.MaxConnLifetime = time.Hour
	cfg.MaxConnIdleTime = 5 * time.Minute
	cfg.ConnConfig.RuntimeParams["application_name"] = "lb-backend"
	cfg.ConnConfig.RuntimeParams["statement_timeout"] = "10000"
	return cfg, nil
}

func poolBounds(maxValue, minValue *string) (int32, int32, error) {
	parse := func(value *string, fallback int32, minimum int32) (int32, error) {
		if value == nil {
			return fallback, nil
		}
		n, e := strconv.Atoi(*value)
		if e != nil || n < int(minimum) || n > 256 || strconv.Itoa(n) != *value {
			return 0, errors.New("invalid DB pool size")
		}
		return int32(n), nil
	}
	max, e := parse(maxValue, 20, 1)
	if e != nil {
		return 0, 0, e
	}
	min, e := parse(minValue, 0, 0)
	if e != nil {
		return 0, 0, e
	}
	if min > max {
		return 0, 0, errors.New("DB pool minimum exceeds maximum")
	}
	return max, min, nil
}
