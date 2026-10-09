package common

import (
	"testing"

	rootcommon "github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNormalizeDCMediaAudioRequestProfiles(t *testing.T) {
	falseValue := false
	tests := []struct {
		name     string
		request  dto.AudioRequest
		wantKind DCMediaAudioKind
	}{
		{
			name: "basic speech defaults response format",
			request: dto.AudioRequest{
				Model: " speech-model ",
				Input: "hello",
			},
			wantKind: DCMediaAudioKindSpeech,
		},
		{
			name: "reference speech preserves explicit false",
			request: dto.AudioRequest{
				Model: "reference-model",
				Input: "hello",
				Metadata: mustMarshalAudioMetadata(t, DCMediaAudioMetadata{
					AudioURL:                  "https://example.com/reference.wav",
					ShouldUsePromptForEmotion: &falseValue,
				}),
			},
			wantKind: DCMediaAudioKindReferenceSpeech,
		},
		{
			name: "music",
			request: dto.AudioRequest{
				Model: "music-model",
				Input: "soundtrack",
				Metadata: mustMarshalAudioMetadata(t, DCMediaAudioMetadata{
					MusicLengthMS: intPtr(30000),
				}),
			},
			wantKind: DCMediaAudioKindMusic,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			profile, err := NormalizeDCMediaAudioRequest(&tt.request)
			require.NoError(t, err)
			assert.Equal(t, tt.wantKind, profile.Kind)
			assert.Equal(t, "mp3", tt.request.ResponseFormat)
		})
	}
}

func TestNormalizeDCMediaAudioRequestRejectsInvalidProfile(t *testing.T) {
	tests := []struct {
		name    string
		request dto.AudioRequest
		wantErr string
	}{
		{
			name:    "missing input",
			request: dto.AudioRequest{Model: "speech-model"},
			wantErr: "input is required",
		},
		{
			name: "reference metadata without audio",
			request: dto.AudioRequest{
				Model:    "reference-model",
				Input:    "hello",
				Metadata: mustMarshalAudioMetadata(t, DCMediaAudioMetadata{EmotionPrompt: "calm"}),
			},
			wantErr: "metadata.audio_url is required",
		},
		{
			name: "reference and music conflict",
			request: dto.AudioRequest{
				Model: "mixed-model",
				Input: "hello",
				Metadata: mustMarshalAudioMetadata(t, DCMediaAudioMetadata{
					AudioURL:      "data:audio/wav;base64,YXVkaW8=",
					MusicLengthMS: intPtr(30000),
				}),
			},
			wantErr: "cannot be combined",
		},
		{
			name: "music length out of range",
			request: dto.AudioRequest{
				Model:    "music-model",
				Input:    "soundtrack",
				Metadata: mustMarshalAudioMetadata(t, DCMediaAudioMetadata{MusicLengthMS: intPtr(1000)}),
			},
			wantErr: "must be between 3000 and 600000",
		},
		{
			name: "unsupported response format",
			request: dto.AudioRequest{
				Model:          "speech-model",
				Input:          "hello",
				ResponseFormat: "json",
			},
			wantErr: "unsupported audio response_format",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := NormalizeDCMediaAudioRequest(&tt.request)
			require.ErrorContains(t, err, tt.wantErr)
		})
	}
}

func mustMarshalAudioMetadata(t *testing.T, metadata DCMediaAudioMetadata) []byte {
	t.Helper()
	payload, err := rootcommon.Marshal(metadata)
	require.NoError(t, err)
	return payload
}

func intPtr(value int) *int {
	return &value
}
