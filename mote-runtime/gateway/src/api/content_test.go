package api

import (
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

func floatPointer(value float64) *float64 { return &value }
func intPointer(value int64) *int64       { return &value }
