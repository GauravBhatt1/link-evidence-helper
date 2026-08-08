package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

const maxSourceAdminBodyBytes = 8 << 10

type sourceDraftRequest struct {
	ID                 string   `json:"id"`
	DisplayName        string   `json:"displayName"`
	Kind               string   `json:"kind"`
	Endpoint           string   `json:"endpoint"`
	QueryParameter     string   `json:"queryParameter,omitempty"`
	ResultRoot         string   `json:"resultRoot,omitempty"`
	TitleField         string   `json:"titleField,omitempty"`
	URLField           string   `json:"urlField,omitempty"`
	AllowedResultHosts []string `json:"allowedResultHosts,omitempty"`
	Enabled            bool     `json:"enabled"`
	ExpectedRevision   uint64   `json:"expectedRevision,omitempty"`
}

func (body sourceDraftRequest) draft() sourceadmin.Draft {
	return sourceadmin.Draft{
		ID: body.ID, DisplayName: body.DisplayName, Kind: body.Kind, Endpoint: body.Endpoint,
		QueryParameter: body.QueryParameter, ResultRoot: body.ResultRoot, TitleField: body.TitleField,
		URLField: body.URLField, AllowedResultHosts: append([]string(nil), body.AllowedResultHosts...),
		Enabled: body.Enabled,
	}
}

func SourceAdminHandler(verifier *adminauth.Verifier, registry sourceadmin.Registry) http.Handler {
	return SourceAdminHandlerWithAudit(verifier, registry, nil)
}

func SourceAdminHandlerWithAudit(verifier *adminauth.Verifier, registry sourceadmin.Registry, recorder audit.Recorder) http.Handler {
	api := &apiHandler{}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/admin/sources", api.sourceCollection(verifier, registry, recorder))
	mux.HandleFunc("/api/v1/admin/sources/", api.sourceResource(verifier, registry, recorder))
	return api.securityHeaders(mux)
}

func (api *apiHandler) sourceCollection(verifier *adminauth.Verifier, registry sourceadmin.Registry, recorder audit.Recorder) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		requestID := api.requestID()
		writer.Header().Set("X-Request-ID", requestID)
		if !authorizeAdmin(writer, request, requestID, verifier) {
			return
		}
		if registry == nil {
			writeError(writer, http.StatusServiceUnavailable, "source_registry_unavailable", "Source management is not configured.", requestID)
			return
		}
		if len(request.URL.Query()) != 0 {
			writeError(writer, http.StatusBadRequest, "invalid_request", "Source routes do not accept query parameters.", requestID)
			return
		}
		switch request.Method {
		case http.MethodGet:
			writeJSON(writer, http.StatusOK, registry.List())
		case http.MethodPost:
			var body sourceDraftRequest
			if !decodeBoundedJSON(writer, request, requestID, &body) {
				return
			}
			if body.ExpectedRevision != 0 {
				writeError(writer, http.StatusBadRequest, "invalid_request", "expectedRevision is not valid when creating a source.", requestID)
				return
			}
			source, err := registry.Create(body.draft(), time.Now().UTC())
			if !recordSourceAudit(writer, requestID, recorder, "source.create", body.ID, err) {
				return
			}
			writeSourceResult(writer, requestID, source, err, http.StatusCreated)
		default:
			writer.Header().Set("Allow", "GET, POST")
			writeError(writer, http.StatusMethodNotAllowed, "method_not_allowed", "Method not allowed.", requestID)
		}
	}
}

func (api *apiHandler) sourceResource(verifier *adminauth.Verifier, registry sourceadmin.Registry, recorder audit.Recorder) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		requestID := api.requestID()
		writer.Header().Set("X-Request-ID", requestID)
		if !authorizeAdmin(writer, request, requestID, verifier) {
			return
		}
		if registry == nil {
			writeError(writer, http.StatusServiceUnavailable, "source_registry_unavailable", "Source management is not configured.", requestID)
			return
		}
		if len(request.URL.Query()) != 0 {
			writeError(writer, http.StatusBadRequest, "invalid_request", "Source routes do not accept query parameters.", requestID)
			return
		}
		id := strings.TrimPrefix(request.URL.Path, "/api/v1/admin/sources/")
		if id == "" || strings.Contains(id, "/") {
			writeError(writer, http.StatusNotFound, "source_not_found", "Source not found.", requestID)
			return
		}
		var body sourceDraftRequest
		if !decodeBoundedJSON(writer, request, requestID, &body) {
			return
		}
		if body.ExpectedRevision == 0 {
			writeError(writer, http.StatusBadRequest, "invalid_request", "expectedRevision is required.", requestID)
			return
		}
		switch request.Method {
		case http.MethodPut:
			if body.ID != id {
				writeError(writer, http.StatusBadRequest, "invalid_request", "The source id must match the route.", requestID)
				return
			}
			source, err := registry.Update(id, body.ExpectedRevision, body.draft(), time.Now().UTC())
			if !recordSourceAudit(writer, requestID, recorder, "source.update", id, err) {
				return
			}
			writeSourceResult(writer, requestID, source, err, http.StatusOK)
		case http.MethodDelete:
			source, err := registry.Disable(id, body.ExpectedRevision, time.Now().UTC())
			if !recordSourceAudit(writer, requestID, recorder, "source.disable", id, err) {
				return
			}
			writeSourceResult(writer, requestID, source, err, http.StatusOK)
		default:
			writer.Header().Set("Allow", "PUT, DELETE")
			writeError(writer, http.StatusMethodNotAllowed, "method_not_allowed", "Method not allowed.", requestID)
		}
	}
}

func recordSourceAudit(writer http.ResponseWriter, requestID string, recorder audit.Recorder, action, sourceID string, operationErr error) bool {
	if recorder == nil {
		return true
	}
	outcome := "success"
	if operationErr != nil {
		outcome = "failure"
	}
	event, err := audit.NewEvent("evt:"+requestID+":"+action, "http:"+requestID, "admin", action, "source:"+sourceID, outcome, time.Now().UTC())
	if err == nil {
		err = recorder.Record(event)
	}
	if err != nil {
		writeError(writer, http.StatusInternalServerError, "audit_unavailable", "The source operation could not be audited.", requestID)
		return false
	}
	return true
}

func authorizeAdmin(writer http.ResponseWriter, request *http.Request, requestID string, verifier *adminauth.Verifier) bool {
	if verifier == nil {
		writeError(writer, http.StatusServiceUnavailable, "admin_auth_unavailable", "Administrator access is not configured.", requestID)
		return false
	}
	if !verifier.VerifyAuthorization(request.Header.Get("Authorization")) {
		writer.Header().Set("WWW-Authenticate", `Bearer realm="admin"`)
		writeError(writer, http.StatusUnauthorized, "unauthorized", "Administrator authentication is required.", requestID)
		return false
	}
	return true
}

func decodeBoundedJSON(writer http.ResponseWriter, request *http.Request, requestID string, target any) bool {
	if !isJSONContentType(request.Header.Get("Content-Type")) {
		writeError(writer, http.StatusUnsupportedMediaType, "unsupported_media_type", "Content-Type must be application/json.", requestID)
		return false
	}
	request.Body = http.MaxBytesReader(writer, request.Body, maxSourceAdminBodyBytes)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		writeError(writer, http.StatusBadRequest, "invalid_request", "The source request is invalid.", requestID)
		return false
	}
	if err := ensureJSONEOF(decoder); err != nil {
		writeError(writer, http.StatusBadRequest, "invalid_request", "The request body must contain one JSON object.", requestID)
		return false
	}
	return true
}

func writeSourceResult(writer http.ResponseWriter, requestID string, source sourceadmin.Source, err error, successStatus int) {
	if err == nil {
		writeJSON(writer, successStatus, source)
		return
	}
	switch {
	case errors.Is(err, sourceadmin.ErrInvalidSource):
		writeError(writer, http.StatusBadRequest, "invalid_source", "The source configuration is invalid.", requestID)
	case errors.Is(err, sourceadmin.ErrSourceExists):
		writeError(writer, http.StatusConflict, "source_exists", "A source with this id already exists.", requestID)
	case errors.Is(err, sourceadmin.ErrSourceNotFound):
		writeError(writer, http.StatusNotFound, "source_not_found", "Source not found.", requestID)
	case errors.Is(err, sourceadmin.ErrRevisionConflict):
		writeError(writer, http.StatusConflict, "revision_conflict", "The source changed; reload it and retry.", requestID)
	default:
		writeError(writer, http.StatusInternalServerError, "internal_error", "The source operation could not be completed.", requestID)
	}
}
