// Package adminauth provides the runtime-only authentication boundary for
// administrative API routes. Secrets are never serialized or logged.
package adminauth

import (
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"strings"
	"unicode"
)

var ErrInvalidTokenConfiguration = errors.New("invalid admin token configuration")

// Verifier stores only a SHA-256 digest of the configured runtime token.
type Verifier struct {
	digest [sha256.Size]byte
}

// NewVerifier validates and hashes a runtime token. Tokens must be long enough
// to resist guessing and must not contain whitespace or control characters.
func NewVerifier(token string) (*Verifier, error) {
	if len(token) < 32 || len(token) > 512 {
		return nil, ErrInvalidTokenConfiguration
	}
	for _, character := range token {
		if unicode.IsSpace(character) || unicode.IsControl(character) {
			return nil, ErrInvalidTokenConfiguration
		}
	}
	return &Verifier{digest: sha256.Sum256([]byte(token))}, nil
}

// VerifyAuthorization accepts exactly one RFC 6750-style Bearer credential.
// It performs a constant-time digest comparison and never returns the token.
func (verifier *Verifier) VerifyAuthorization(header string) bool {
	if verifier == nil || len(header) < len("Bearer ")+1 {
		return false
	}
	if !strings.EqualFold(header[:len("Bearer ")], "Bearer ") {
		return false
	}
	token := header[len("Bearer "):]
	if token == "" || strings.TrimSpace(token) != token || strings.ContainsAny(token, "\r\n\t ") {
		return false
	}
	digest := sha256.Sum256([]byte(token))
	return subtle.ConstantTimeCompare(digest[:], verifier.digest[:]) == 1
}
