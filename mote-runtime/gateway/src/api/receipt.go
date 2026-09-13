package api

// ReceiptReference points to the Gateway-owned durable operation receipt.
// Persistence mechanics remain outside Gateway behind a narrow port.
type ReceiptReference struct {
	ReceiptID   string `json:"receipt_id"`
	OperationID string `json:"operation_id"`
	Revision    int64  `json:"revision"`
}
