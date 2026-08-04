module github.com/GauravBhatt1/link-evidence-helper/apps/api

go 1.23.0

toolchain go1.23.12

require (
	github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue v0.0.0
	github.com/santhosh-tekuri/jsonschema/v5 v5.3.1
)

replace github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue => ../../packages/jobqueue
