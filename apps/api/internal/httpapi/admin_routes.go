package httpapi

import (
	"net/http"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
)

func (api *apiHandler) adminSession(verifier *adminauth.Verifier) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		requestID := api.requestID()
		writer.Header().Set("X-Request-ID", requestID)
		if request.Method != http.MethodGet {
			methodNotAllowed(writer, http.MethodGet)
			return
		}
		if len(request.URL.Query()) != 0 {
			writeError(writer, http.StatusBadRequest, "invalid_request", "Admin session does not accept query parameters.", requestID)
			return
		}
		if verifier == nil {
			writeError(writer, http.StatusServiceUnavailable, "admin_auth_unavailable", "Administrator access is not configured.", requestID)
			return
		}
		if !verifier.VerifyAuthorization(request.Header.Get("Authorization")) {
			writer.Header().Set("WWW-Authenticate", `Bearer realm="admin"`)
			writeError(writer, http.StatusUnauthorized, "unauthorized", "Administrator authentication is required.", requestID)
			return
		}
		writeJSON(writer, http.StatusOK, struct {
			OK      bool   `json:"ok"`
			Success bool   `json:"success"`
			Role    string `json:"role"`
		}{OK: true, Success: true, Role: "admin"})
	}
}
