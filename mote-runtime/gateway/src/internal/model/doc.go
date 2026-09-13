// Package model owns exact model identity, capabilities, and the immutable
// model catalog. The checked-in catalog is a static seed until a future CRUD
// owner becomes the sole persistence and write path. Catalog defaults are
// patched once by validated Kernel invocation configuration. This package never
// owns endpoint, credential, service, protocol, routing, or pricing
// configuration.
package model
