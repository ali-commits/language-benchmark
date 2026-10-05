package main

import (
	"errors"
	"github.com/jackc/pgx/v5/pgxpool"
	"net/url"
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
	cfg.MaxConns = 20
	cfg.MinConns = 0
	cfg.MaxConnLifetime = time.Hour
	cfg.MaxConnIdleTime = 5 * time.Minute
	cfg.ConnConfig.RuntimeParams["application_name"] = "lb-backend"
	cfg.ConnConfig.RuntimeParams["statement_timeout"] = "10000"
	return cfg, nil
}
