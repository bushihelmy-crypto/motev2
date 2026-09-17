package api

import (
	"fmt"
	"regexp"
)

const maxRuntimeIdentityLength = 128

var (
	baseModelPattern   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$`)
	serviceKindPattern = regexp.MustCompile(`^[a-z][a-z0-9_.-]*$`)
	protocolIDPattern  = regexp.MustCompile(`^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$`)
)

// ValidateBaseModel enforces the canonical bare model identity shared by the
// model catalog, service deployment allowlists, and invocation DTO schema.
// Provider namespaces and routing aliases are not part of this value.
func ValidateBaseModel(value string) error {
	if !baseModelPattern.MatchString(value) {
		return fmt.Errorf("base_model must be 1-256 bytes and use a bare model name with letters, digits, '_', '.', ':', or '-'")
	}
	return nil
}

// ValidateServiceKind enforces the canonical service identity emitted in
// Gateway observations. Descriptors reject invalid identities instead of
// rewriting them after admission.
func ValidateServiceKind(value string) error {
	return validateRuntimeIdentity("service kind", value, serviceKindPattern)
}

// ValidateProtocolID enforces the canonical dotted protocol identity emitted
// in Gateway observations.
func ValidateProtocolID(value string) error {
	return validateRuntimeIdentity("protocol id", value, protocolIDPattern)
}

func validateRuntimeIdentity(kind, value string, pattern *regexp.Regexp) error {
	if len(value) == 0 || len(value) > maxRuntimeIdentityLength || !pattern.MatchString(value) {
		return fmt.Errorf("%s must be 1-%d bytes in canonical lowercase form", kind, maxRuntimeIdentityLength)
	}
	return nil
}
