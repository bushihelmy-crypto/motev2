package api

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
)

func TestGenerationParametersValidatePublicDomain(t *testing.T) {
	tests := []struct {
		name       string
		parameters GenerationParameters
		valid      bool
	}{
		{name: "empty", valid: true},
		{name: "empty stop at otherwise valid boundaries", parameters: GenerationParameters{
			Temperature: floatPointer(2), TopP: floatPointer(1), MaxOutputTokens: intPointer(1),
			Stop: []string{"", strings.Repeat("界", 256)},
		}, valid: false},
		{name: "valid", parameters: GenerationParameters{
			Temperature: floatPointer(0), TopP: floatPointer(0.1), MaxOutputTokens: intPointer(1),
			Stop: []string{"END", strings.Repeat("界", 256)},
		}, valid: true},
		{name: "temperature high", parameters: GenerationParameters{Temperature: floatPointer(3)}},
		{name: "temperature non-finite", parameters: GenerationParameters{Temperature: floatPointer(math.Inf(1))}},
		{name: "top-p zero", parameters: GenerationParameters{TopP: floatPointer(0)}},
		{name: "top-p non-finite", parameters: GenerationParameters{TopP: floatPointer(math.NaN())}},
		{name: "output tokens zero", parameters: GenerationParameters{MaxOutputTokens: intPointer(0)}},
		{name: "too many stop sequences", parameters: GenerationParameters{Stop: make([]string, 17)}},
		{name: "empty stop sequence", parameters: GenerationParameters{Stop: []string{""}}},
		{name: "long stop sequence", parameters: GenerationParameters{Stop: []string{strings.Repeat("界", 257)}}},
		{name: "invalid utf8 stop sequence", parameters: GenerationParameters{Stop: []string{string([]byte{0xff})}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := test.parameters.Validate()
			if test.valid && err != nil {
				t.Fatalf("valid parameters rejected: %v", err)
			}
			if !test.valid && err == nil {
				t.Fatal("invalid parameters accepted")
			}
		})
	}
}

func TestGenerationParametersAcceptAndRejectExactNumericBoundaries(t *testing.T) {
	valid := []GenerationParameters{
		{Temperature: floatPointer(0)},
		{Temperature: floatPointer(2)},
		{TopP: floatPointer(math.SmallestNonzeroFloat64)},
		{TopP: floatPointer(1)},
		{MaxOutputTokens: intPointer(1)},
		{MaxOutputTokens: intPointer(math.MaxInt64)},
	}
	for index, parameters := range valid {
		if err := parameters.Validate(); err != nil {
			t.Errorf("valid boundary %d rejected: %+v: %v", index, parameters, err)
		}
	}

	invalid := []GenerationParameters{
		{Temperature: floatPointer(math.Nextafter(0, math.Inf(-1)))},
		{Temperature: floatPointer(math.Nextafter(2, math.Inf(1)))},
		{Temperature: floatPointer(math.Inf(-1))},
		{Temperature: floatPointer(math.NaN())},
		{TopP: floatPointer(math.Copysign(0, -1))},
		{TopP: floatPointer(math.Nextafter(1, math.Inf(1)))},
		{TopP: floatPointer(math.Inf(1))},
		{TopP: floatPointer(math.Inf(-1))},
		{MaxOutputTokens: intPointer(-1)},
	}
	for index, parameters := range invalid {
		if err := parameters.Validate(); err == nil {
			t.Errorf("invalid boundary %d accepted: %+v", index, parameters)
		}
	}
}

func TestStopSequencesUseCharacterAndItemBoundaries(t *testing.T) {
	validSixteen := make([]string, 16)
	for index := range validSixteen {
		validSixteen[index] = "x"
	}
	for _, stop := range [][]string{
		nil,
		{},
		{"x"},
		{strings.Repeat("界", 256)},
		validSixteen,
	} {
		if err := (GenerationParameters{Stop: stop}).Validate(); err != nil {
			t.Errorf("valid stop boundary rejected (items=%d): %v", len(stop), err)
		}
	}

	invalidSeventeen := append(append([]string(nil), validSixteen...), "x")
	for _, stop := range [][]string{
		invalidSeventeen,
		{strings.Repeat("界", 257)},
		{string([]byte{'x', 0xff})},
	} {
		if err := (GenerationParameters{Stop: stop}).Validate(); err == nil {
			t.Errorf("invalid stop boundary accepted (items=%d)", len(stop))
		}
	}
}

func TestGenerationParameterPointersPreserveExplicitZeroOnJSONRoundTrip(t *testing.T) {
	parameters := GenerationParameters{
		Temperature: floatPointer(0),
		Seed:        intPointer(0),
	}
	encoded, err := json.Marshal(parameters)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(encoded), `"temperature":0`) || !strings.Contains(string(encoded), `"seed":0`) {
		t.Fatalf("explicit zero values became omission: %s", encoded)
	}
	var decoded GenerationParameters
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Temperature == nil || *decoded.Temperature != 0 || decoded.Seed == nil || *decoded.Seed != 0 {
		t.Fatalf("explicit zero values did not round-trip: %+v", decoded)
	}
}

func floatPointer(value float64) *float64 { return &value }
func intPointer(value int64) *int64       { return &value }
