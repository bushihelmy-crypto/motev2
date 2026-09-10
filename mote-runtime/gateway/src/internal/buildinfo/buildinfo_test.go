package buildinfo

import "testing"

func TestDevelopmentMetadataHasSafeDefaults(t *testing.T) {
	for name, value := range map[string]string{
		"version": Version,
		"commit":  Commit,
		"date":    Date,
	} {
		if value == "" {
			t.Errorf("%s metadata must not be empty", name)
		}
	}
}
