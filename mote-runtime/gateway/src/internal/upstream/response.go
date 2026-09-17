package upstream

// Response is the opaque upstream result presented to a protocol decoder.
type Response interface {
	StatusCode() int
	Body() []byte
}
