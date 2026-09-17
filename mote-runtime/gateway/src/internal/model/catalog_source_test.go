package model

import (
	"context"
	"errors"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
)

type catalogSource struct {
	records []ports.ModelRecord
	err     error
}

func (source *catalogSource) LoadModelCatalog(context.Context) ([]ports.ModelRecord, error) {
	if source == nil {
		return nil, errors.New("nil source")
	}
	if source.err != nil {
		return nil, source.err
	}
	return source.records, nil
}

func TestCatalogStoreLoadsAndAtomicallyRetainsPreviousCatalogOnFailure(t *testing.T) {
	source := &catalogSource{records: []ports.ModelRecord{sourceRecord("source-a")}}
	store, err := NewCatalogStore(context.Background(), source)
	if err != nil {
		t.Fatalf("construct catalog store: %v", err)
	}
	if _, err := store.Current().Lookup("source-a"); err != nil {
		t.Fatalf("initial source record missing: %v", err)
	}

	source.records = []ports.ModelRecord{{BaseModel: "invalid", Lifecycle: ports.ModelLifecycleActive}}
	if err := store.Refresh(context.Background()); err == nil {
		t.Fatal("invalid refresh was accepted")
	}
	if _, err := store.Current().Lookup("source-a"); err != nil {
		t.Fatalf("failed refresh replaced the previous catalog: %v", err)
	}

	want := errors.New("database unavailable")
	source.err = want
	if err := store.Refresh(context.Background()); !errors.Is(err, want) {
		t.Fatalf("source failure was not preserved: %v", err)
	}
	if _, err := store.Current().Lookup("source-a"); err != nil {
		t.Fatalf("source failure discarded the previous catalog: %v", err)
	}
}

func TestCatalogStoreRequiresInitialCompleteSource(t *testing.T) {
	if _, err := NewCatalogStore(context.Background(), (*catalogSource)(nil)); err == nil {
		t.Fatal("nil source was accepted")
	}
	if _, err := NewCatalogStore(context.Background(), &catalogSource{}); err == nil {
		t.Fatal("empty source was accepted")
	}
}

func TestCatalogFromRecordsRejectsGenerationValuesOutsidePublicContract(t *testing.T) {
	temperature := 3.0
	topP := 0.0
	tests := []struct {
		name       string
		generation ports.ModelGeneration
	}{
		{name: "temperature default", generation: ports.ModelGeneration{
			Temperature: &ports.ModelNumericFloat{Default: &temperature},
		}},
		{name: "temperature clamp", generation: ports.ModelGeneration{
			Temperature: &ports.ModelNumericFloat{Maximum: &temperature},
		}},
		{name: "top-p default", generation: ports.ModelGeneration{
			TopP: &ports.ModelNumericFloat{Default: &topP},
		}},
		{name: "top-p clamp", generation: ports.ModelGeneration{
			TopP: &ports.ModelNumericFloat{Minimum: &topP},
		}},
		{name: "empty stop default", generation: ports.ModelGeneration{
			Stop: &ports.ModelStop{Default: []string{""}},
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			record := sourceRecord("invalid-generation")
			record.Capability.Generation = &test.generation
			if _, err := NewCatalogFromRecords([]ports.ModelRecord{record}); err == nil {
				t.Fatal("invalid generation record was accepted")
			}
		})
	}
}

func TestCatalogStoreRejectsTypedNilAndZeroValueSources(t *testing.T) {
	store := &CatalogStore{}
	if err := store.Refresh(context.Background()); err == nil {
		t.Fatal("zero-value catalog store attempted to call a missing source")
	}
}

func TestNilCatalogStoreIsNotReadyOrRefreshable(t *testing.T) {
	var store *CatalogStore
	if got := store.Current(); got.Ready() {
		t.Fatal("nil catalog store returned a ready catalog")
	}
	if err := store.Refresh(context.Background()); err == nil {
		t.Fatal("nil catalog store refresh unexpectedly succeeded")
	}
}

func TestCatalogFromRecordsFreezesPersistenceSlices(t *testing.T) {
	records := []ports.ModelRecord{sourceRecord("frozen")}
	catalog, err := NewCatalogFromRecords(records)
	if err != nil {
		t.Fatalf("construct catalog from records: %v", err)
	}
	records[0].Capability.InputModalities[0] = api.ModalityImage
	definition, err := catalog.Lookup("frozen")
	if err != nil {
		t.Fatalf("lookup frozen record: %v", err)
	}
	capability, ok := definition.Capability(api.OperationGenerate)
	if !ok || !capability.SupportsInputModality(api.ModalityText) || capability.SupportsInputModality(api.ModalityImage) {
		t.Fatal("catalog retained mutable persistence record state")
	}
}

func TestCatalogFromRecordsConvertsCompleteCapabilities(t *testing.T) {
	temperatureMinimum, temperatureMaximum, temperatureDefault := 0.2, 1.2, 0.7
	topPDefault := 0.9
	seedMinimum, seedMaximum, seedDefault := int64(1), int64(10), int64(3)
	defaultThinking := api.ThinkingAdaptive
	defaultEffort := api.ReasoningEffortHigh
	dimensionsMinimum, dimensionsMaximum, dimensionsDefault := int64(128), int64(1024), int64(512)
	records := []ports.ModelRecord{
		{
			BaseModel: "source-generate",
			Lifecycle: ports.ModelLifecycleDeprecated,
			TokenLimits: ports.ModelTokenLimits{
				ContextWindowTokens: 8192,
				MaxInputTokens:      4096,
				MinOutputTokens:     128,
				MaxOutputTokens:     2048,
			},
			Capability: ports.ModelCapability{
				Operation:        api.OperationGenerate,
				InputModalities:  []api.Modality{api.ModalityText},
				OutputModalities: []api.Modality{api.ModalityText},
				Features:         []api.Feature{api.FeatureToolCalls},
				Generation: &ports.ModelGeneration{
					Temperature:     &ports.ModelNumericFloat{Minimum: &temperatureMinimum, Maximum: &temperatureMaximum, Default: &temperatureDefault},
					TopP:            &ports.ModelNumericFloat{Default: &topPDefault},
					MaxOutputTokens: &ports.ModelOutputTokens{},
					Stop:            &ports.ModelStop{Default: []string{"END"}},
					Seed:            &ports.ModelNumericInt{Minimum: &seedMinimum, Maximum: &seedMaximum, Default: &seedDefault},
				},
				Reasoning: &ports.ModelReasoning{
					ThinkingModes: []ports.ModelThinkingMode{
						{Thinking: api.ThinkingDisabled},
						{Thinking: api.ThinkingAdaptive, Efforts: []api.ReasoningEffort{api.ReasoningEffortHigh}, DefaultEffort: &defaultEffort},
					},
					DefaultThinking: &defaultThinking,
				},
			},
		},
		{
			BaseModel: "source-embedding",
			Lifecycle: ports.ModelLifecycleActive,
			Capability: ports.ModelCapability{
				Operation:        api.OperationEmbedding,
				InputModalities:  []api.Modality{api.ModalityText},
				OutputModalities: []api.Modality{api.ModalityEmbedding},
				Embedding: &ports.ModelEmbedding{Dimensions: &ports.ModelNumericInt{
					Minimum: &dimensionsMinimum,
					Maximum: &dimensionsMaximum,
					Default: &dimensionsDefault,
				}},
			},
		},
	}

	catalog, err := NewCatalogFromRecords(records)
	if err != nil {
		t.Fatalf("construct complete source catalog: %v", err)
	}
	definition, err := catalog.Lookup("source-generate")
	if err != nil {
		t.Fatalf("lookup generated source model: %v", err)
	}
	if definition.Lifecycle() != LifecycleDeprecated || definition.TokenLimits().MaxOutputTokens != 2048 {
		t.Fatalf("model envelope was not converted: lifecycle=%q limits=%+v", definition.Lifecycle(), definition.TokenLimits())
	}
	capability, ok := definition.Capability(api.OperationGenerate)
	if !ok || !capability.SupportsFeature(api.FeatureToolCalls) {
		t.Fatal("generate capability was not converted")
	}
	requestedTemperature := 2.0
	parameters := capability.ResolveGenerationParameters(api.GenerationParameters{Temperature: &requestedTemperature})
	if parameters.Temperature == nil || *parameters.Temperature != temperatureMaximum ||
		parameters.TopP == nil || *parameters.TopP != topPDefault ||
		parameters.MaxOutputTokens == nil || *parameters.MaxOutputTokens != 2048 ||
		len(parameters.Stop) != 1 || parameters.Stop[0] != "END" ||
		parameters.Seed == nil || *parameters.Seed != seedDefault {
		t.Fatalf("generation policy was not converted: %+v", parameters)
	}
	reasoning, err := capability.ResolveReasoning(nil)
	if err != nil || reasoning == nil || reasoning.Thinking != defaultThinking || reasoning.Effort != defaultEffort {
		t.Fatalf("reasoning policy was not converted: %+v %v", reasoning, err)
	}

	embeddingDefinition, err := catalog.Lookup("source-embedding")
	if err != nil {
		t.Fatalf("lookup embedding source model: %v", err)
	}
	embedding, ok := embeddingDefinition.Capability(api.OperationEmbedding)
	if !ok {
		t.Fatal("embedding capability was not converted")
	}
	requestedDimensions := int64(4096)
	resolvedDimensions := embedding.ResolveEmbeddingDimensions(&requestedDimensions)
	if resolvedDimensions == nil || *resolvedDimensions != dimensionsMaximum {
		t.Fatalf("embedding bounds were not converted: %v", resolvedDimensions)
	}
	if value, known := embedding.DefaultEmbeddingDimensions(); !known || value != dimensionsDefault {
		t.Fatalf("embedding default was not converted: value=%d known=%v", value, known)
	}

	wantTemperatureMaximum := temperatureMaximum
	*records[0].Capability.Generation.Temperature.Maximum = 9
	records[0].Capability.Generation.Stop.Default[0] = "MUTATED"
	records[0].Capability.Reasoning.ThinkingModes[1].Efforts[0] = api.ReasoningEffortLow
	parameters = capability.ResolveGenerationParameters(api.GenerationParameters{Temperature: &requestedTemperature})
	reasoning, err = capability.ResolveReasoning(nil)
	if err != nil || *parameters.Temperature != wantTemperatureMaximum || parameters.Stop[0] != "END" || reasoning.Effort != defaultEffort {
		t.Fatalf("catalog retained mutable nested persistence state: parameters=%+v reasoning=%+v err=%v", parameters, reasoning, err)
	}
}

func sourceRecord(baseModel string) ports.ModelRecord {
	return ports.ModelRecord{
		BaseModel: baseModel,
		Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation:        api.OperationGenerate,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityText},
		},
	}
}
