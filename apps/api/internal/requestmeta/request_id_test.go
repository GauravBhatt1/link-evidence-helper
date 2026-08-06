package requestmeta

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestMiddlewareGeneratesBoundedRequestID(t *testing.T) {
	const id = "0123456789abcdef0123456789abcdef"
	var observed string

	handler := Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		value, ok := FromContext(r.Context())
		if !ok {
			t.Fatal("request ID missing from context")
		}
		observed = value
		w.WriteHeader(http.StatusNoContent)
	}), func() (string, error) { return id, nil })

	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	req.Header.Set(Header, "attacker-controlled-value")
	res := httptest.NewRecorder()
	handler.ServeHTTP(res, req)

	if res.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusNoContent)
	}
	if got := res.Header().Get(Header); got != id {
		t.Fatalf("response request ID = %q, want %q", got, id)
	}
	if observed != id {
		t.Fatalf("context request ID = %q, want %q", observed, id)
	}
}

func TestMiddlewareFailsClosedOnGeneratorError(t *testing.T) {
	called := false
	handler := Middleware(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	}), func() (string, error) { return "", errors.New("entropy unavailable") })

	res := httptest.NewRecorder()
	handler.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/", nil))

	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusServiceUnavailable)
	}
	if called {
		t.Fatal("downstream handler was called")
	}
	if got := res.Header().Get(Header); got != "" {
		t.Fatalf("unexpected request ID header %q", got)
	}
}

func TestMiddlewareRejectsMalformedGeneratedIDs(t *testing.T) {
	for _, id := range []string{
		"short",
		"0123456789abcdef0123456789abcdeg",
		"0123456789ABCDEF0123456789ABCDEF",
		"0123456789abcdef0123456789abcdef0",
	} {
		t.Run(id, func(t *testing.T) {
			handler := Middleware(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				t.Fatal("downstream handler was called")
			}), func() (string, error) { return id, nil })

			res := httptest.NewRecorder()
			handler.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/", nil))
			if res.Code != http.StatusServiceUnavailable {
				t.Fatalf("status = %d, want %d", res.Code, http.StatusServiceUnavailable)
			}
		})
	}
}

func TestNewIDShape(t *testing.T) {
	first, err := NewID()
	if err != nil {
		t.Fatal(err)
	}
	second, err := NewID()
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 32 || len(second) != 32 {
		t.Fatalf("unexpected lengths: %d and %d", len(first), len(second))
	}
	if first == second {
		t.Fatal("consecutive request IDs unexpectedly matched")
	}
}
