package main

import (
	"fmt"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/buildinfo"
)

// main is intentionally a scaffold entry point. Wiring an inbound transport
// here before its invocation contract is accepted would create a second owner.
func main() {
	fmt.Printf("mote-gateway %s (%s, %s)\n", buildinfo.Version, buildinfo.Commit, buildinfo.Date)
}
