package auth

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"net/http"
	"strings"
)

var (
	ErrMissingCredential = errors.New("missing admin credential")
	ErrInvalidCredential = errors.New("invalid admin credential")
)

type Role string

const (
	RoleAdmin Role = "admin"
)

type Principal struct {
	Subject string
	Role    Role
}

type contextKey struct{}

type Verifier struct {
	expected [sha256.Size]byte
	subject  string
}

func NewVerifier(token, subject string) (*Verifier, error) {
	token = strings.TrimSpace(token)
	subject = strings.TrimSpace(subject)
	if token == "" {
		return nil, ErrMissingCredential
	}
	if subject == "" {
		subject = "configured-admin"
	}
	return &Verifier{expected: sha256.Sum256([]byte(token)), subject: subject}, nil
}

func (v *Verifier) AuthenticateRequest(request *http.Request) (Principal, error) {
	if request == nil {
		return Principal{}, ErrMissingCredential
	}
	header := strings.TrimSpace(request.Header.Get("Authorization"))
	const prefix = "Bearer "
	if len(header) <= len(prefix) || !strings.EqualFold(header[:len(prefix)], prefix) {
		return Principal{}, ErrMissingCredential
	}
	candidate := strings.TrimSpace(header[len(prefix):])
	if candidate == "" {
		return Principal{}, ErrMissingCredential
	}
	digest := sha256.Sum256([]byte(candidate))
	if subtle.ConstantTimeCompare(digest[:], v.expected[:]) != 1 {
		return Principal{}, ErrInvalidCredential
	}
	return Principal{Subject: v.subject, Role: RoleAdmin}, nil
}

func (v *Verifier) RequireAdmin(next http.Handler) http.Handler {
	if next == nil {
		panic("auth: nil handler")
	}
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		principal, err := v.AuthenticateRequest(request)
		if err != nil {
			writer.Header().Set("Cache-Control", "no-store")
			writer.Header().Set("WWW-Authenticate", `Bearer realm="admin"`)
			http.Error(writer, "unauthorized", http.StatusUnauthorized)
			return
		}
		ctx := context.WithValue(request.Context(), contextKey{}, principal)
		next.ServeHTTP(writer, request.WithContext(ctx))
	})
}

func PrincipalFromContext(ctx context.Context) (Principal, bool) {
	if ctx == nil {
		return Principal{}, false
	}
	principal, ok := ctx.Value(contextKey{}).(Principal)
	return principal, ok && principal.Subject != "" && principal.Role == RoleAdmin
}
