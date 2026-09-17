package testkit

import (
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/service"
)

func TestFixtureConstructorsPanicInsteadOfReturningInvalidFacts(t *testing.T) {
	tests := []struct {
		name string
		run  func()
	}{
		{name: "protocol", run: func() { mustProtocolDescriptor("", nil) }},
		{name: "service", run: func() { mustServiceDescriptor(service.DescriptorConfig{}) }},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			deferredPanic := false
			func() {
				defer func() {
					deferredPanic = recover() != nil
				}()
				testCase.run()
			}()
			if !deferredPanic {
				t.Fatal("invalid fixture did not panic")
			}
		})
	}
}
