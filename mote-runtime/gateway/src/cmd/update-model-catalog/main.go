package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"

	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

type options struct {
	newAPIRepo      string
	newAPIRef       string
	bifrostRepo     string
	bifrostRef      string
	modelParameters string
	output          string
}

func main() {
	configured := parseOptions()
	if err := run(configured); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func parseOptions() options {
	var result options
	flag.StringVar(&result.newAPIRepo, "new-api-repo", "", "path to the new-api Git repository")
	flag.StringVar(&result.newAPIRef, "new-api-ref", defaultNewAPIRef, "immutable new-api Git commit to read")
	flag.StringVar(&result.bifrostRepo, "bifrost-repo", "", "path to the Bifrost Git repository")
	flag.StringVar(&result.bifrostRef, "bifrost-ref", defaultBifrostRef, "immutable Bifrost Git commit to read")
	flag.StringVar(&result.modelParameters, "model-parameters", "", "Bifrost model-parameters JSON snapshot")
	flag.StringVar(&result.output, "output", "src/internal/model/catalog_data.json.gz", "gzip-compressed catalog output path")
	flag.Parse()
	return result
}

func run(configured options) error {
	if configured.newAPIRepo == "" || configured.bifrostRepo == "" || configured.modelParameters == "" {
		return errors.New("-new-api-repo, -bifrost-repo, and -model-parameters are required")
	}
	newRevision, err := resolveCommit(configured.newAPIRepo, configured.newAPIRef)
	if err != nil {
		return fmt.Errorf("resolve new-api ref: %w", err)
	}
	bifrostRevision, err := resolveCommit(configured.bifrostRepo, configured.bifrostRef)
	if err != nil {
		return fmt.Errorf("resolve Bifrost ref: %w", err)
	}
	parameterData, err := os.ReadFile(configured.modelParameters)
	if err != nil {
		return fmt.Errorf("read Bifrost model parameters: %w", err)
	}
	records, err := decodeSourceRecords(parameterData)
	if err != nil {
		return err
	}
	models, rejected := compileModels(records)
	if len(models) == 0 {
		return errors.New("compile model catalog: sources produced no usable models")
	}
	digest := sha256.Sum256(parameterData)
	sources := []modelcatalog.CatalogSource{
		{Name: "new-api", Revision: strings.TrimSpace(newRevision)},
		{Name: "bifrost", Revision: strings.TrimSpace(bifrostRevision)},
		{Name: "bifrost-model-parameters", SHA256: hex.EncodeToString(digest[:])},
	}
	document := modelcatalog.CatalogDocument{
		SchemaVersion: modelcatalog.CatalogSchemaVersion,
		Sources:       sources,
		Models:        models,
	}
	catalogData, err := encodeCatalog(document)
	if err != nil {
		return err
	}
	if err := publishArtifact(configured.output, catalogData); err != nil {
		return err
	}
	for _, rejection := range rejected {
		fmt.Fprintf(os.Stderr, "rejected model: %v\n", rejection)
	}
	fmt.Printf("wrote %d models to %s (%d diagnostics)\n", len(models), configured.output, len(rejected))
	return nil
}
