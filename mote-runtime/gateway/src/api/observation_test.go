package api

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestServiceReportedCostIsOptionalAndExplicit(t *testing.T) {
	observation := LLMObservation{}
	encoded, err := json.Marshal(observation)
	if err != nil {
		t.Fatalf("marshal observation without service-reported cost: %v", err)
	}
	if strings.Contains(string(encoded), "service_reported_cost") {
		t.Fatalf("missing service-reported cost must be omitted: %s", encoded)
	}

	observation.ServiceReportedCost = &ServiceReportedCost{
		Currency: "USD",
		Amount:   "0.000001",
	}
	encoded, err = json.Marshal(observation)
	if err != nil {
		t.Fatalf("marshal observation with service-reported cost: %v", err)
	}
	if !strings.Contains(string(encoded), `"service_reported_cost":{"currency":"USD","amount":"0.000001"}`) {
		t.Fatalf("service-reported cost has an unexpected wire shape: %s", encoded)
	}
	if strings.Contains(string(encoded), `"cost":`) {
		t.Fatalf("legacy cost field leaked into the wire shape: %s", encoded)
	}
}
