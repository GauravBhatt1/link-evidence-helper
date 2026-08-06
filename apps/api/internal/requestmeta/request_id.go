package requestmeta

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net/http"
)

const Header = "X-Request-ID"

type contextKey struct{}

type Generator func() (string, error)

func NewID() (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw[:]), nil
}

func FromContext(ctx context.Context) (string, bool) {
	value, ok := ctx.Value(contextKey{}).(string)
	return value, ok && value != ""
}

func Middleware(next http.Handler, generate Generator) http.Handler {
	if next == nil {
		next = http.NotFoundHandler()
	}
	if generate == nil {
		generate = NewID
	}

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id, err := generate()
		if err != nil || len(id) != 32 {
			http.Error(w, "service unavailable", http.StatusServiceUnavailable)
			return
		}
		for _, c := range id {
			if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
				http.Error(w, "service unavailable", http.StatusServiceUnavailable)
				return
			}
		}

		w.Header().Set(Header, id)
		ctx := context.WithValue(r.Context(), contextKey{}, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
