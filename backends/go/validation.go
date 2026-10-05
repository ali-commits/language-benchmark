package main

import (
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

var usernamePattern = regexp.MustCompile(`^[A-Za-z0-9_]+$`)

func respond(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if status != 204 {
		_ = json.NewEncoder(w).Encode(body)
	}
}
func failure(w http.ResponseWriter, status int, detail string) {
	respond(w, status, map[string]any{"detail": detail})
}
func validationDetail(location, field, kind, msg string, input any, ctx any) map[string]any {
	loc := []any{location}
	if field != "" {
		loc = append(loc, field)
	}
	v := map[string]any{"loc": loc, "type": kind, "msg": msg, "input": input}
	if ctx != nil {
		v["ctx"] = ctx
	}
	return v
}
func validation(w http.ResponseWriter, location, field, kind, msg string, input any, ctx any) {
	respond(w, 422, map[string]any{"detail": []any{validationDetail(location, field, kind, msg, input, ctx)}})
}
func readInput(w http.ResponseWriter, r *http.Request, kind string) (map[string]string, bool) {
	b, e := io.ReadAll(io.LimitReader(r.Body, 16385))
	if e != nil {
		failure(w, 400, "invalid request")
		return nil, false
	}
	if len(b) > 16384 {
		failure(w, 413, "request body too large")
		return nil, false
	}
	if !utf8.Valid(b) {
		failure(w, 400, "There was an error parsing the body")
		return nil, false
	}
	if len(b) == 0 {
		validation(w, "body", "", "missing", "Field required", nil, nil)
		return nil, false
	}
	var value any
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.UseNumber()
	if e = d.Decode(&value); e != nil {
		validation(w, "body", "", "json_invalid", "JSON decode error", nil, nil)
		return nil, false
	}
	var trailing any
	if d.Decode(&trailing) != io.EOF {
		validation(w, "body", "", "json_invalid", "JSON decode error", nil, nil)
		return nil, false
	}
	if value == nil {
		validation(w, "body", "", "missing", "Field required", nil, nil)
		return nil, false
	}
	object, ok := value.(map[string]any)
	if !ok {
		validation(w, "body", "", "model_attributes_type", "Input should be a valid dictionary or object to extract fields from", value, nil)
		return nil, false
	}
	fields := []string{"content"}
	if kind == "register" || kind == "login" {
		fields = []string{"username", "password"}
	}
	allowed := map[string]bool{}
	out := map[string]string{}
	details := []any{}
	add := func(field, typ, msg string, input, ctx any) {
		details = append(details, validationDetail("body", field, typ, msg, input, ctx))
	}
	for _, f := range fields {
		allowed[f] = true
		v, present := object[f]
		if !present {
			add(f, "missing", "Field required", object, nil)
			continue
		}
		s, ok := v.(string)
		if !ok {
			add(f, "string_type", "Input should be a valid string", v, nil)
			continue
		}
		min, max := 1, 280
		if f == "username" {
			min, max = 3, 32
		}
		if f == "password" {
			max = 128
			if kind == "register" {
				min = 12
			}
		}
		if kind == "comment" {
			max = 1000
		}
		n := utf8.RuneCountInString(s)
		if n < min {
			add(f, "string_too_short", "String should have at least "+itoa(min)+" "+characters(min), v, map[string]any{"min_length": min})
			continue
		}
		if n > max {
			add(f, "string_too_long", "String should have at most "+itoa(max)+" "+characters(max), v, map[string]any{"max_length": max})
			continue
		}
		if f == "username" && !usernamePattern.MatchString(s) {
			add(f, "string_pattern_mismatch", "String should match pattern '^[A-Za-z0-9_]+$'", v, map[string]any{"pattern": "^[A-Za-z0-9_]+$"})
			continue
		}
		if f == "content" && strings.TrimFunc(s, func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) }) == "" {
			add(f, "value_error", "Value error, content must not be blank", v, map[string]any{"error": map[string]any{}})
			continue
		}
		if f == "username" {
			s = strings.ToLower(s)
		}
		out[f] = s
	}
	for _, f := range objectKeys(b) {
		v := object[f]
		if !allowed[f] {
			add(f, "extra_forbidden", "Extra inputs are not permitted", v, nil)
		}
	}
	if len(details) > 0 {
		respond(w, 422, map[string]any{"detail": details})
		return nil, false
	}
	return out, true
}

func characters(n int) string {
	if n == 1 {
		return "character"
	}
	return "characters"
}

// Go maps do not retain JSON insertion order; errors for extra fields do.
func objectKeys(data []byte) []string {
	d := json.NewDecoder(strings.NewReader(string(data)))
	_, _ = d.Token()
	keys := []string{}
	seen := map[string]bool{}
	for d.More() {
		token, e := d.Token()
		if e != nil {
			break
		}
		key := token.(string)
		var value json.RawMessage
		if d.Decode(&value) != nil {
			break
		}
		if !seen[key] {
			keys = append(keys, key)
			seen[key] = true
		}
	}
	return keys
}
