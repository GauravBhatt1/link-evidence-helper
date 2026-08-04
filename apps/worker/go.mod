module github.com/GauravBhatt1/link-evidence-helper/apps/worker

go 1.23.0

toolchain go1.23.12

require github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue v0.0.0

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	github.com/redis/go-redis/v9 v9.18.0 // indirect
	go.uber.org/atomic v1.11.0 // indirect
)

replace github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue => ../../packages/jobqueue
