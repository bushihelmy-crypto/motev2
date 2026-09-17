// Package application owns the delivery-mode use cases. It admits a typed
// frame once and passes the resulting immutable admitted request to the
// downstream adapter; it does not decode inbound bytes or choose a model.
package application
