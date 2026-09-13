//go:build integration

package integration

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestLocalFixtureHarnessIsDeterministic(t *testing.T) {
	handler := http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/fixture" {
			http.NotFound(writer, request)
			return
		}
		_, _ = writer.Write([]byte("fixture-ok"))
	})
	request := httptest.NewRequest(http.MethodGet, "http://fixture.invalid/fixture", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	response := recorder.Result()
	t.Cleanup(func() { _ = response.Body.Close() })
	if response.StatusCode != http.StatusOK {
		t.Fatalf("unexpected fixture status: %d", response.StatusCode)
	}
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatalf("read fixture response: %v", err)
	}
	if string(body) != "fixture-ok" {
		t.Fatalf("unexpected fixture body: %q", body)
	}
}
