package main

import (
	"context"
	"log"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	workerjobs "github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/jobs"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

func main() {
	redisAddress := envOrDefault("LINK_EVIDENCE_REDIS_ADDR", "127.0.0.1:6379")
	if !isLoopbackAddress(redisAddress) && os.Getenv("LINK_EVIDENCE_ALLOW_REMOTE_REDIS") != "true" {
		log.Fatalf("refusing non-loopback Redis address %q without LINK_EVIDENCE_ALLOW_REMOTE_REDIS=true", redisAddress)
	}
	config := jobqueue.DefaultConfig()
	config.Prefix = envOrDefault("LINK_EVIDENCE_JOB_PREFIX", config.Prefix)
	config.MaxQueued = int64(envInt("LINK_EVIDENCE_JOB_MAX_QUEUED", int(config.MaxQueued), 1, 10000))
	store, err := jobqueue.Open(
		redisAddress,
		os.Getenv("LINK_EVIDENCE_REDIS_PASSWORD"),
		envInt("LINK_EVIDENCE_REDIS_DB", 0, 0, 15),
		config,
	)
	if err != nil {
		log.Fatal(err)
	}
	defer store.Close()

	concurrency := envInt("LINK_EVIDENCE_WORKER_CONCURRENCY", 2, 1, jobqueue.MaxWorkerConcurrency)
	stepDelay := time.Duration(envInt("LINK_EVIDENCE_DEVELOPMENT_STEP_DELAY_MS", 100, 0, 10000)) * time.Millisecond
	runner, err := jobqueue.NewRunner(store, workerjobs.DevelopmentExecutor{StepDelay: stepDelay}, concurrency)
	if err != nil {
		log.Fatal(err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	log.Printf("development Redis worker started (concurrency=%d; live sources disabled)", concurrency)
	if err := runner.Run(ctx); err != nil {
		log.Fatal(err)
	}
}

func envOrDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
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
