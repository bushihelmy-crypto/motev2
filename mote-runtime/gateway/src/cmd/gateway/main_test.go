package main

import (
	"fmt"
	"io"
	"os"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/buildinfo"
)

func TestMainPrintsBuildIdentity(t *testing.T) {
	original := os.Stdout
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = writer
	t.Cleanup(func() { os.Stdout = original })

	main()
	os.Stdout = original
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	output, err := io.ReadAll(reader)
	if err != nil {
		t.Fatal(err)
	}
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
	want := fmt.Sprintf("mote-gateway %s (%s, %s)\n", buildinfo.Version, buildinfo.Commit, buildinfo.Date)
	if string(output) != want {
		t.Fatalf("command identity = %q, want %q", output, want)
	}
}
