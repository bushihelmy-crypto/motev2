package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

func compileTokenLimits(operation api.Operation, records []recordRef) (modelcatalog.TokenLimits, bool, error) {
	var limits modelcatalog.TokenLimits
	for _, ref := range records {
		maxInput, err := nonNegativeInteger(ref.record.MaxInputTokens)
		if err != nil {
			return modelcatalog.TokenLimits{}, false, fmt.Errorf("max_input_tokens: %w", err)
		}
		if operation == api.OperationEmbedding && maxInput == 0 {
			maxInput, err = nonNegativeInteger(ref.record.MaxTokens)
			if err != nil {
				return modelcatalog.TokenLimits{}, false, fmt.Errorf("max_tokens: %w", err)
			}
		}
		if err := mergeLimitValue("max_input_tokens", &limits.MaxInputTokens, maxInput); err != nil {
			return modelcatalog.TokenLimits{}, false, err
		}
	}
	if operation != api.OperationGenerate && operation != api.OperationRealtime {
		return limits, false, nil
	}

	var maxOutput int64
	for _, ref := range records {
		explicit, err := nonNegativeInteger(ref.record.MaxOutputTokens)
		if err != nil {
			return modelcatalog.TokenLimits{}, false, fmt.Errorf("max_output_tokens: %w", err)
		}
		generic, err := nonNegativeInteger(ref.record.MaxTokens)
		if err != nil {
			return modelcatalog.TokenLimits{}, false, fmt.Errorf("max_tokens: %w", err)
		}
		if explicit > 0 && generic > 0 && explicit != generic {
			return modelcatalog.TokenLimits{}, false, errors.New("max_output_tokens conflicts with max_tokens")
		}
		candidate := explicit
		if candidate == 0 {
			candidate = generic
		}
		if err := mergeLimitValue("max_output_tokens", &maxOutput, candidate); err != nil {
			return modelcatalog.TokenLimits{}, false, err
		}
	}
	parameterPolicy, parameterFound, policyErr := outputTokenBounds(records)
	if policyErr != nil {
		return modelcatalog.TokenLimits{}, false, policyErr
	}
	if parameterFound {
		if maxOutput > 0 && parameterPolicy.Maximum != nil && maxOutput != *parameterPolicy.Maximum {
			return modelcatalog.TokenLimits{}, false, errors.New("model output maximum conflicts with parameter range maximum")
		}
		if maxOutput == 0 && parameterPolicy.Maximum != nil {
			maxOutput = *parameterPolicy.Maximum
		}
		if parameterPolicy.Minimum != nil {
			limits.MinOutputTokens = *parameterPolicy.Minimum
		}
	}
	limits.MaxOutputTokens = maxOutput
	if limits.MinOutputTokens > 0 && limits.MaxOutputTokens > 0 && limits.MinOutputTokens > limits.MaxOutputTokens {
		return modelcatalog.TokenLimits{}, false, errors.New("output minimum must not exceed maximum")
	}
	return limits, parameterFound || maxOutput > 0, nil
}

func mergeLimitValue(field string, target *int64, candidate int64) error {
	if candidate == 0 {
		return nil
	}
	if *target != 0 && *target != candidate {
		return fmt.Errorf("conflicting %s declarations", field)
	}
	*target = candidate
	return nil
}

func compileGenerationPolicy(records []recordRef, outputTokensSupported bool) (*modelcatalog.GenerationPolicy, error) {
	policy := &modelcatalog.GenerationPolicy{}
	samplingAllowed, err := compileBooleanField(booleanField{
		name:  "supports_sampling_params",
		value: func(record sourceRecord) *bool { return record.SupportsSamplingParams },
	}, records)
	if err != nil {
		return nil, err
	}
	allowsSampling := samplingAllowed == nil || *samplingAllowed
	if allowsSampling {
		if parameter, ok, err := findParameter(records, "temperature"); err != nil {
			return nil, err
		} else if ok {
			policy.Temperature, err = floatPolicy(parameter)
			if err != nil {
				return nil, fmt.Errorf("temperature: %w", err)
			}
		}
		if parameter, ok, err := findParameter(records, "top_p", "topp"); err != nil {
			return nil, err
		} else if ok {
			policy.TopP, err = floatPolicy(parameter)
			if err != nil {
				return nil, fmt.Errorf("top_p: %w", err)
			}
		}
		if parameter, ok, err := findParameter(records, "seed"); err != nil {
			return nil, err
		} else if ok {
			policy.Seed, err = integerPolicy(parameter)
			if err != nil {
				return nil, fmt.Errorf("seed: %w", err)
			}
		}
	}
	if outputTokensSupported {
		// The default is a Gateway policy, not a model fact. Runtime resolves
		// it centrally so the catalog never repeats a derived 4096 value.
		policy.MaxOutputTokens = &modelcatalog.OutputTokenParameter{}
	}
	if parameter, ok, err := findParameter(records, "stop", "stop_sequences", "stopsequences"); err != nil {
		return nil, err
	} else if ok {
		stops, parseErr := rawStrings(parameter.Default)
		if parseErr != nil {
			return nil, fmt.Errorf("stop: %w", parseErr)
		}
		policy.Stop = &modelcatalog.StopParameter{Default: stops}
	}
	if policy.Temperature == nil && policy.TopP == nil && policy.MaxOutputTokens == nil && policy.Stop == nil && policy.Seed == nil {
		return nil, nil
	}
	return policy, nil
}

func compileEmbeddingPolicy(records []recordRef) (*modelcatalog.EmbeddingPolicy, error) {
	policy := &modelcatalog.EmbeddingPolicy{}
	if parameter, ok, err := findParameter(records, "dimensions", "output_dimensionality"); err != nil {
		return nil, err
	} else if ok {
		var policyErr error
		policy.Dimensions, policyErr = positiveIntegerPolicy(parameter)
		if policyErr != nil {
			return nil, fmt.Errorf("dimensions: %w", policyErr)
		}
		vectorSize, vectorFound, vectorErr := consistentVectorSize(records)
		if vectorErr != nil {
			return nil, vectorErr
		}
		if policy.Dimensions.Default == nil && vectorFound {
			policy.Dimensions.Default = &vectorSize
		} else if policy.Dimensions.Default != nil && vectorFound && *policy.Dimensions.Default != vectorSize {
			return nil, errors.New("embedding dimensions conflict with output_vector_size")
		}
		return policy, nil
	}
	vectorSize, vectorFound, err := consistentVectorSize(records)
	if err != nil {
		return nil, err
	}
	if vectorFound {
		policy.FixedDimensions = &vectorSize
	}
	return policy, nil
}

func consistentVectorSize(records []recordRef) (int64, bool, error) {
	var value int64
	found := false
	for _, ref := range records {
		candidate, err := positiveIntegerPointer(ref.record.OutputVectorSize)
		if err != nil {
			return 0, false, fmt.Errorf("output_vector_size: %w", err)
		}
		if candidate == nil {
			continue
		}
		if found && value != *candidate {
			return 0, false, errors.New("conflicting output_vector_size declarations")
		}
		value, found = *candidate, true
	}
	return value, found, nil
}

func floatPolicy(parameter sourceParameter) (*modelcatalog.NumericParameter[float64], error) {
	defaultValue, err := rawFloat(parameter.Default)
	if err != nil {
		return nil, err
	}
	policy := &modelcatalog.NumericParameter[float64]{Default: defaultValue}
	if parameter.Range != nil {
		policy.Minimum, err = numberFloat(parameter.Range.Minimum)
		if err != nil {
			return nil, fmt.Errorf("minimum: %w", err)
		}
		policy.Maximum, err = numberFloat(parameter.Range.Maximum)
		if err != nil {
			return nil, fmt.Errorf("maximum: %w", err)
		}
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		return nil, errors.New("minimum must not exceed maximum")
	}
	if policy.Default != nil && ((policy.Minimum != nil && *policy.Default < *policy.Minimum) ||
		(policy.Maximum != nil && *policy.Default > *policy.Maximum)) {
		return nil, errors.New("default is outside the declared bounds")
	}
	return policy, nil
}

func integerPolicy(parameter sourceParameter) (*modelcatalog.NumericParameter[int64], error) {
	defaultValue, err := rawInteger(parameter.Default)
	if err != nil {
		return nil, err
	}
	policy := &modelcatalog.NumericParameter[int64]{Default: defaultValue}
	if parameter.Range != nil {
		policy.Minimum, err = numberInteger(parameter.Range.Minimum)
		if err != nil {
			return nil, fmt.Errorf("minimum: %w", err)
		}
		policy.Maximum, err = numberInteger(parameter.Range.Maximum)
		if err != nil {
			return nil, fmt.Errorf("maximum: %w", err)
		}
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		return nil, errors.New("minimum must not exceed maximum")
	}
	if policy.Default != nil && ((policy.Minimum != nil && *policy.Default < *policy.Minimum) ||
		(policy.Maximum != nil && *policy.Default > *policy.Maximum)) {
		return nil, errors.New("default is outside the declared bounds")
	}
	return policy, nil
}

func positiveIntegerPolicy(parameter sourceParameter) (*modelcatalog.NumericParameter[int64], error) {
	policy, err := integerPolicy(parameter)
	if err != nil {
		return nil, err
	}
	for _, value := range []*int64{policy.Minimum, policy.Maximum, policy.Default} {
		if value != nil && *value < 1 {
			return nil, errors.New("value must be positive")
		}
	}
	return policy, nil
}

func outputTokenBounds(records []recordRef) (*modelcatalog.NumericParameter[int64], bool, error) {
	policy := &modelcatalog.NumericParameter[int64]{}
	found := false
	disabledIn := ""
	for _, ref := range records {
		for _, parameter := range ref.record.ModelParameters {
			if !parameterNameMatch(parameter.ID, []string{"max_tokens", "max_output_tokens", "max_completion_tokens", "max_response_output_tokens"}) {
				continue
			}
			if parameter.Disabled {
				if found {
					return nil, false, fmt.Errorf("disabled output-token parameter from %s conflicts with an enabled declaration", ref.key)
				}
				disabledIn = ref.key
				continue
			}
			if disabledIn != "" {
				return nil, false, fmt.Errorf("enabled output-token parameter from %s conflicts with disabled declaration in %s", ref.key, disabledIn)
			}
			found = true
			if parameter.Range == nil {
				continue
			}
			for _, bound := range []struct {
				name      string
				source    *json.Number
				collected **int64
			}{
				{name: "minimum", source: parameter.Range.Minimum, collected: &policy.Minimum},
				{name: "maximum", source: parameter.Range.Maximum, collected: &policy.Maximum},
			} {
				value, err := numberInteger(bound.source)
				if err != nil {
					return nil, false, fmt.Errorf("%s %s: %w", parameter.ID, bound.name, err)
				}
				if value == nil {
					continue
				}
				if *value < 0 {
					return nil, false, fmt.Errorf("%s %s must not be negative", parameter.ID, bound.name)
				}
				if *bound.collected != nil && **bound.collected != *value {
					return nil, false, fmt.Errorf("conflicting max-output %s declarations in %s", bound.name, ref.key)
				}
				*bound.collected = value
			}
		}
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		return nil, false, errors.New("output minimum must not exceed maximum")
	}
	return policy, found, nil
}

func findParameter(records []recordRef, names ...string) (sourceParameter, bool, error) {
	var found sourceParameter
	foundIn := ""
	disabledIn := ""
	for _, ref := range records {
		for _, parameter := range ref.record.ModelParameters {
			if !parameterNameMatch(parameter.ID, names) {
				continue
			}
			if parameter.Disabled {
				if foundIn != "" {
					return sourceParameter{}, false, fmt.Errorf("disabled parameter %q from %s conflicts with enabled declaration in %s", parameter.ID, ref.key, foundIn)
				}
				disabledIn = ref.key
				continue
			}
			if disabledIn != "" {
				return sourceParameter{}, false, fmt.Errorf("enabled parameter %q from %s conflicts with disabled declaration in %s", parameter.ID, ref.key, disabledIn)
			}
			if foundIn == "" {
				found = parameter
				foundIn = ref.key
				continue
			}
			if !equivalentParameter(found, parameter) {
				return sourceParameter{}, false, fmt.Errorf("parameter %q conflicts with another declaration (%s and %s)", parameter.ID, foundIn, ref.key)
			}
		}
	}
	if foundIn != "" {
		return found, true, nil
	}
	return sourceParameter{}, false, nil
}

func parameterNameMatch(value string, names []string) bool {
	for _, name := range names {
		if strings.EqualFold(value, name) {
			return true
		}
	}
	return false
}

func equivalentParameter(left, right sourceParameter) bool {
	if !bytes.Equal(bytes.TrimSpace(left.Default), bytes.TrimSpace(right.Default)) {
		return false
	}
	if left.Range == nil || right.Range == nil {
		return left.Range == nil && right.Range == nil
	}
	return equivalentNumber(left.Range.Minimum, right.Range.Minimum) && equivalentNumber(left.Range.Maximum, right.Range.Maximum)
}

func equivalentNumber(left, right *json.Number) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return left.String() == right.String()
}

func rawFloat(raw json.RawMessage) (*float64, error) {
	number, err := rawNumber(raw)
	if err != nil || number == nil {
		return nil, err
	}
	return numberFloat(number)
}

func rawInteger(raw json.RawMessage) (*int64, error) {
	number, err := rawNumber(raw)
	if err != nil || number == nil {
		return nil, err
	}
	return numberInteger(number)
}

func rawStrings(raw json.RawMessage) ([]string, error) {
	if len(bytes.TrimSpace(raw)) == 0 || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	var values []string
	if err := decoder.Decode(&values); err != nil {
		return nil, fmt.Errorf("must be an array of strings: %w", err)
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, errors.New("must contain exactly one JSON value")
	}
	return values, nil
}

func rawNumber(raw json.RawMessage) (*json.Number, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(trimmed))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, fmt.Errorf("must be numeric: %w", err)
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, errors.New("must contain exactly one JSON value")
	}
	number, ok := value.(json.Number)
	if !ok {
		return nil, errors.New("must be numeric")
	}
	return &number, nil
}

func numberFloat(number *json.Number) (*float64, error) {
	if number == nil {
		return nil, nil
	}
	value, err := number.Float64()
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil, errors.New("must be a finite number")
	}
	return &value, nil
}

func numberInteger(number *json.Number) (*int64, error) {
	if number == nil {
		return nil, nil
	}
	if value, err := number.Int64(); err == nil {
		return &value, nil
	}
	value, err := number.Float64()
	if err != nil || math.Trunc(value) != value || value < math.MinInt64 || value > math.MaxInt64 || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil, errors.New("must be a finite integer")
	}
	converted := int64(value)
	return &converted, nil
}

func nonNegativeInteger(number *json.Number) (int64, error) {
	value, err := numberInteger(number)
	if err != nil {
		return 0, err
	}
	if value == nil {
		return 0, nil
	}
	if *value < 0 {
		return 0, errors.New("must not be negative")
	}
	return *value, nil
}

func positiveIntegerPointer(number *json.Number) (*int64, error) {
	value, err := numberInteger(number)
	if err != nil {
		return nil, err
	}
	if value == nil || *value == 0 {
		return nil, nil
	}
	if *value < 0 {
		return nil, errors.New("must be positive")
	}
	return value, nil
}
