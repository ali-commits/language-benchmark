package main

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/argon2"
)

var hashSlots = make(chan struct{}, 2)
var dummyHash string

func hashPassword(password string) (string, error) {
	return hashPasswordContext(context.Background(), password)
}
func acquireHash(ctx context.Context) error {
	if e := ctx.Err(); e != nil {
		return e
	}
	select {
	case hashSlots <- struct{}{}:
		if e := ctx.Err(); e != nil {
			<-hashSlots
			return e
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func hashPasswordContext(ctx context.Context, password string) (string, error) {
	if e := acquireHash(ctx); e != nil {
		return "", e
	}
	defer func() { <-hashSlots }()
	salt := make([]byte, 16)
	if _, e := rand.Read(salt); e != nil {
		return "", e
	}
	key := argon2.IDKey([]byte(password), salt, 3, 65536, 1, 32)
	b := base64.RawStdEncoding
	return fmt.Sprintf("$argon2id$v=19$m=65536,t=3,p=1$%s$%s", b.EncodeToString(salt), b.EncodeToString(key)), nil
}
func verifyPassword(password, encoded string) bool {
	valid, _ := verifyPasswordContext(context.Background(), password, encoded)
	return valid
}
func verifyPasswordContext(ctx context.Context, password, encoded string) (bool, error) {
	if e := acquireHash(ctx); e != nil {
		return false, e
	}
	defer func() { <-hashSlots }()
	parts := strings.Split(encoded, "$")
	if len(parts) != 6 || parts[1] != "argon2id" || parts[2] != "v=19" || parts[3] != "m=65536,t=3,p=1" {
		return false, nil
	}
	salt, e := base64.RawStdEncoding.DecodeString(parts[4])
	if e != nil || len(salt) != 16 {
		return false, nil
	}
	expected, e := base64.RawStdEncoding.DecodeString(parts[5])
	if e != nil || len(expected) != 32 {
		return false, nil
	}
	actual := argon2.IDKey([]byte(password), salt, 3, 65536, 1, 32)
	return subtle.ConstantTimeCompare(actual, expected) == 1, nil
}
func (a *app) issueToken(id int64) (string, error) {
	now := time.Now().Unix()
	return jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{"sub": strconv.FormatInt(id, 10), "iat": now, "exp": now + a.ttl, "iss": "language-benchmark", "aud": "social-api"}).SignedString(a.secret)
}
func (a *app) decodeToken(value string) (int64, error) {
	token, e := jwt.Parse(value, func(t *jwt.Token) (any, error) { return a.secret, nil }, jwt.WithValidMethods([]string{"HS256"}), jwt.WithIssuer("language-benchmark"), jwt.WithAudience("social-api"), jwt.WithExpirationRequired(), jwt.WithJSONNumber())
	if e != nil || !token.Valid {
		return 0, errors.New("invalid token")
	}
	c := token.Claims.(jwt.MapClaims)
	sub, ok := c["sub"].(string)
	if !ok {
		return 0, errors.New("invalid subject")
	}
	iat, ok := c["iat"].(json.Number)
	if !ok {
		return 0, errors.New("invalid issued time")
	}
	i, e := iat.Int64()
	if e != nil || i > time.Now().Unix()+30 {
		return 0, errors.New("invalid issued time")
	}
	exp, ok := c["exp"].(json.Number)
	if !ok {
		return 0, errors.New("invalid expiry")
	}
	if _, e = exp.Int64(); e != nil {
		return 0, e
	}
	id, e := strconv.ParseInt(sub, 10, 64)
	if e != nil || id <= 0 {
		return 0, errors.New("invalid subject")
	}
	return id, nil
}
func encodeCursor(stamp time.Time, id int64) string {
	b, _ := json.Marshal([]any{stamp.UTC().Format(time.RFC3339Nano), id})
	return base64.RawURLEncoding.EncodeToString(b)
}
func decodeCursor(value string) (time.Time, int64, error) {
	invalid := errors.New("invalid cursor")
	if len(value) < 1 || len(value) > 256 {
		return time.Time{}, 0, invalid
	}
	data, e := base64.URLEncoding.Strict().DecodeString(value + strings.Repeat("=", (4-len(value)%4)%4))
	if e != nil {
		return time.Time{}, 0, invalid
	}
	var pair []json.RawMessage
	if json.Unmarshal(data, &pair) != nil || len(pair) != 2 {
		return time.Time{}, 0, invalid
	}
	var s string
	var id int64
	if json.Unmarshal(pair[0], &s) != nil || json.Unmarshal(pair[1], &id) != nil || id <= 0 {
		return time.Time{}, 0, invalid
	}
	stamp, e := time.Parse(time.RFC3339Nano, s)
	if e != nil || stamp.Year() < 1 || stamp.Year() > 9999 {
		return time.Time{}, 0, invalid
	}
	return stamp.UTC(), id, nil
}
