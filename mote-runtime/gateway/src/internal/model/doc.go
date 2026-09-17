// Package model owns exact model identity, model capabilities, parameter rules,
// and immutable catalogs. Production catalogs are loaded as complete records
// through ports.ModelCatalogSource and atomically refreshed; the checked-in
// gzip artifact is compiled only by package tests. This package never owns
// endpoint, credential, service, protocol, routing, or pricing configuration.
package model
