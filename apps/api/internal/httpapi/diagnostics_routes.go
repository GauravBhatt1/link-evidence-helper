package httpapi

import (
	"context"
	"net/http"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/diagnostics"
)

// DiagnosticsProvider returns a bounded, secret-safe diagnostics snapshot.
// Implementations must not include URLs, credentials, headers, request bodies,
// hostnames, or arbitrary metadata in the returned model.
type DiagnosticsProvider interface {
	Snapshot(context.Context) (diagnostics.Snapshot, error)
}

func (api *apiHandler) diagnostics(verifier *adminauth.Verifier, provider DiagnosticsProvider) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		requestID := api.requestID()
		writer.Header().Set("X-Request-ID", requestID)
		if request.Method != http.MethodGet {
			methodNotAllowed(writer, http.MethodGet)
			return
		}
		if len(request.URL.Query()) != 0 {
			writeError(writer, http.StatusBadRequest, "invalid_request", "Diagnostics does not accept query parameters.", requestID)
			return
		}
		if verifier == nil || provider == nil {
			writeError(writer, http.StatusServiceUnavailable, "diagnostics_unavailable", "Diagnostics is not enabled.", requestID)
			return
		}
		if err := verifier.VerifyRequest(request); err != nil {
			writeError(writer, http.StatusUnauthorized, "unauthorized", "Administrator authorization is required.", requestID)
			return
		}

		snapshot, err := provider.Snapshot(request.Context())
		if err != nil {
			writeError(writer, http.StatusServiceUnavailable, "diagnostics_unavailable", "Diagnostics could not be generated.", requestID)
			return
		}
		writeJSON(writer, http.StatusOK, snapshot)
	}
}
