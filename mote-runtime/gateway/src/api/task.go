package api

// TaskHandle is the durable identity returned by an asynchronous submission.
// Polling, cancellation, and reconciliation are separate operations.
type TaskHandle struct {
	TaskID         string `json:"task_id"`
	UpstreamTaskID string `json:"upstream_task_id,omitempty"`
	OperationID    string `json:"operation_id"`
}
