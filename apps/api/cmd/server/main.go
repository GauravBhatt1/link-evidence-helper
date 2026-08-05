package main

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/httpapi"
	jobservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/jobs"
	libraryservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/library"
	searchservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/search"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

func main() {
	if mode := envOrDefault("LINK_EVIDENCE_SEARCH_MODE", "fixture"); mode != "fixture" {
		log.Fatalf("unsupported LINK_EVIDENCE_SEARCH_MODE %q; development mode permits fixture only", mode)
	}

	address := envOrDefault("LINK_EVIDENCE_API_ADDR", "127.0.0.1:8780")
	if !isLoopbackAddress(address) && !envBool("LINK_EVIDENCE_ALLOW_PUBLIC_LISTEN") {
		log.Fatalf("refusing non-loopback API address %q without LINK_EVIDENCE_ALLOW_PUBLIC_LISTEN=true", address)
	}

	fixtureDir, err := resolveFixtureDir()
	if err != nil {
		log.Fatal(err)
	}
	catalog, err := searchservice.NewFixtureCatalog(fixtureDir)
	if err != nil {
		log.Fatalf("load sanitized development fixtures: %v", err)
	}

	libraryMode := envOrDefault("LINK_EVIDENCE_LIBRARY_MODE", "fixture")
	var libraryRepository libraryservice.Repository
	switch libraryMode {
	case "fixture":
		libraryRepository, err = libraryservice.NewFixtureRepository(fixtureDir)
		if err != nil {
			log.Fatalf("load sanitized development library: %v", err)
		}
	case "jellyfin":
		client, clientErr := libraryservice.NewJellyfinClient(libraryservice.JellyfinConfig{
			BaseURL:      os.Getenv("LINK_EVIDENCE_JELLYFIN_URL"),
			APIKey:       os.Getenv("LINK_EVIDENCE_JELLYFIN_API_KEY"),
			LibraryIDs:   splitCSV(os.Getenv("LINK_EVIDENCE_JELLYFIN_LIBRARY_IDS")),
			AllowPrivate: envBool("LINK_EVIDENCE_JELLYFIN_ALLOW_PRIVATE"),
			Timeout:      time.Duration(envInt("LINK_EVIDENCE_JELLYFIN_TIMEOUT_SECONDS", 10, 1, 60)) * time.Second,
			PageSize:     envInt("LINK_EVIDENCE_JELLYFIN_PAGE_SIZE", 200, 1, 500),
			MaxItems:     envInt("LINK_EVIDENCE_JELLYFIN_MAX_ITEMS", 5000, 1, 5000),
		})
		if clientErr != nil {
			log.Fatalf("invalid Jellyfin runtime configuration: %v", clientErr)
		}
		libraryRepository, err = libraryservice.NewJellyfinRepository(
			client,
			time.Duration(envInt("LINK_EVIDENCE_JELLYFIN_CACHE_SECONDS", 30, 0, 600))*time.Second,
		)
		if err != nil {
			log.Fatalf("enable Jellyfin library repository: %v", err)
		}
	default:
		log.Fatalf("unsupported LINK_EVIDENCE_LIBRARY_MODE %q", libraryMode)
	}

	var jobs *jobservice.Service
	if redisAddress := strings.TrimSpace(os.Getenv("LINK_EVIDENCE_REDIS_ADDR")); redisAddress != "" {
		if !isLoopbackAddress(redisAddress) && !envBool("LINK_EVIDENCE_ALLOW_REMOTE_REDIS") {
			log.Fatalf("refusing non-loopback Redis address %q without LINK_EVIDENCE_ALLOW_REMOTE_REDIS=true", redisAddress)
		}
		config := jobqueue.DefaultConfig()
		config.Prefix = envOrDefault("LINK_EVIDENCE_JOB_PREFIX", config.Prefix)
		config.MaxQueued = int64(envInt("LINK_EVIDENCE_JOB_MAX_QUEUED", int(config.MaxQueued), 1, 10000))
		jobs, err = jobservice.Open(
			redisAddress,
			os.Getenv("LINK_EVIDENCE_REDIS_PASSWORD"),
			envInt("LINK_EVIDENCE_REDIS_DB", 0, 0, 15),
			config,
		)
		if err != nil {
			log.Fatalf("enable development Redis jobs: %v", err)
		}
		defer jobs.Close()
	}

	var adminVerifier *adminauth.Verifier
	if token := os.Getenv("LINK_EVIDENCE_ADMIN_TOKEN"); token != "" {
		adminVerifier, err = adminauth.NewVerifier(token)
		if err != nil {
			log.Fatalf("invalid LINK_EVIDENCE_ADMIN_TOKEN: %v", err)
		}
	}

	handler := httpapi.HandlerWithServices(catalog, jobs, libraryRepository, adminVerifier)
	server := &http.Server{
		Addr:              address,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}

	stopped := make(chan os.Signal, 1)
	signal.Notify(stopped, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stopped
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			log.Printf("development API shutdown: %v", err)
		}
	}()

	log.Printf("development API listening on http://%s (search=fixture; library=%s; Redis jobs=%t; admin auth=%t)", address, libraryMode, jobs != nil, adminVerifier != nil)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}

func envOrDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func envBool(name string) bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv(name)), "true")
}

func envInt(name string, fallback, minimum, maximum int) int {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < minimum || parsed > maximum {
		log.Fatalf("%s must be an integer between %d and %d", name, minimum, maximum)
	}
	return parsed
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			result = append(result, trimmed)
		}
	}
	return result
}

func isLoopbackAddress(address string) bool {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return false
	}
	host = strings.Trim(host, "[]")
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func resolveFixtureDir() (string, error) {
	if configured := strings.TrimSpace(os.Getenv("LINK_EVIDENCE_FIXTURE_DIR")); configured != "" {
		return requireDirectory(configured)
	}
	for _, candidate := range []string{
		"packages/testing/fixtures",
		"../../packages/testing/fixtures",
	} {
		if directory, err := requireDirectory(candidate); err == nil {
			return directory, nil
		}
	}
	return "", errors.New("sanitized fixture directory not found; set LINK_EVIDENCE_FIXTURE_DIR")
}

func requireDirectory(path string) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(absolute)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		return "", errors.New("fixture path is not a directory")
	}
	return absolute, nil
}
