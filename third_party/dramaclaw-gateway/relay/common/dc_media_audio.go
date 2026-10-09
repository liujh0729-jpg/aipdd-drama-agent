package common

import (
	"fmt"
	"math"
	"net/url"
	"strings"

	rootcommon "github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
)

type DCMediaAudioKind string

const (
	DCMediaAudioKindSpeech          DCMediaAudioKind = "speech"
	DCMediaAudioKindReferenceSpeech DCMediaAudioKind = "reference_speech"
	DCMediaAudioKindMusic           DCMediaAudioKind = "music"
)

type DCMediaAudioMetadata struct {
	AudioURL                  string `json:"audio_url,omitempty"`
	ShouldUsePromptForEmotion *bool  `json:"should_use_prompt_for_emotion,omitempty"`
	EmotionPrompt             string `json:"emotion_prompt,omitempty"`
	MusicLengthMS             *int   `json:"music_length_ms,omitempty"`
	ForceInstrumental         *bool  `json:"force_instrumental,omitempty"`
	RespectSectionsDurations  *bool  `json:"respect_sections_durations,omitempty"`
	OutputFormat              string `json:"output_format,omitempty"`
}

type DCMediaAudioProfile struct {
	Kind     DCMediaAudioKind
	Metadata DCMediaAudioMetadata
}

// NormalizeDCMediaAudioRequest applies the DC-Media Audio Profile without
// changing the OpenAI-compatible AudioRequest wire format.
func NormalizeDCMediaAudioRequest(request *dto.AudioRequest) (*DCMediaAudioProfile, error) {
	if request == nil {
		return nil, fmt.Errorf("audio request is required")
	}

	request.Model = strings.TrimSpace(request.Model)
	request.Voice = strings.TrimSpace(request.Voice)
	request.ResponseFormat = strings.ToLower(strings.TrimSpace(request.ResponseFormat))
	if request.Model == "" {
		return nil, fmt.Errorf("model is required")
	}
	if strings.TrimSpace(request.Input) == "" {
		return nil, fmt.Errorf("input is required")
	}
	if request.ResponseFormat == "" {
		request.ResponseFormat = "mp3"
	}
	if !isDCMediaAudioResponseFormat(request.ResponseFormat) {
		return nil, fmt.Errorf("unsupported audio response_format: %s", request.ResponseFormat)
	}
	if request.Speed != nil && (math.IsNaN(*request.Speed) || math.IsInf(*request.Speed, 0) || *request.Speed <= 0) {
		return nil, fmt.Errorf("speed must be a positive finite number")
	}

	metadata := DCMediaAudioMetadata{}
	if len(request.Metadata) > 0 && string(request.Metadata) != "null" {
		if err := rootcommon.Unmarshal(request.Metadata, &metadata); err != nil {
			return nil, fmt.Errorf("invalid audio metadata: %w", err)
		}
	}
	metadata.AudioURL = strings.TrimSpace(metadata.AudioURL)
	metadata.EmotionPrompt = strings.TrimSpace(metadata.EmotionPrompt)
	metadata.OutputFormat = strings.TrimSpace(metadata.OutputFormat)

	hasReferenceFields := metadata.AudioURL != "" ||
		metadata.ShouldUsePromptForEmotion != nil || metadata.EmotionPrompt != ""
	hasMusicFields := metadata.MusicLengthMS != nil || metadata.ForceInstrumental != nil ||
		metadata.RespectSectionsDurations != nil || metadata.OutputFormat != ""
	if hasReferenceFields && hasMusicFields {
		return nil, fmt.Errorf("reference speech and music metadata cannot be combined")
	}

	profile := &DCMediaAudioProfile{Kind: DCMediaAudioKindSpeech, Metadata: metadata}
	if hasReferenceFields {
		if metadata.AudioURL == "" {
			return nil, fmt.Errorf("metadata.audio_url is required for reference speech")
		}
		if err := validateDCMediaAudioURL(metadata.AudioURL); err != nil {
			return nil, err
		}
		profile.Kind = DCMediaAudioKindReferenceSpeech
	}
	if hasMusicFields {
		if metadata.MusicLengthMS == nil {
			return nil, fmt.Errorf("metadata.music_length_ms is required for music generation")
		}
		if *metadata.MusicLengthMS < 3000 || *metadata.MusicLengthMS > 600000 {
			return nil, fmt.Errorf("metadata.music_length_ms must be between 3000 and 600000")
		}
		profile.Kind = DCMediaAudioKindMusic
	}
	return profile, nil
}

func isDCMediaAudioResponseFormat(format string) bool {
	switch format {
	case "mp3", "opus", "pcm", "ulaw", "alaw":
		return true
	default:
		return false
	}
}

func validateDCMediaAudioURL(rawURL string) error {
	if strings.HasPrefix(strings.ToLower(rawURL), "data:audio/") {
		comma := strings.Index(rawURL, ",")
		if comma < 0 || !strings.Contains(strings.ToLower(rawURL[:comma]), ";base64") || comma == len(rawURL)-1 {
			return fmt.Errorf("metadata.audio_url must be a base64 audio data URL")
		}
		return nil
	}
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return fmt.Errorf("metadata.audio_url must be an HTTP(S) URL or audio data URL")
	}
	return nil
}
