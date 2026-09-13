package buildinfo

// These variables are intentionally the only version values in the Go source.
// Release tooling injects them from the tag; development builds use defaults.
var (
	Version = "dev"
	Commit  = "unknown"
	Date    = "unknown"
)
