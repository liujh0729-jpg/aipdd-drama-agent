package fal

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"strings"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

const contextKeyResponseFormat = "fal_response_format"

type falTTSRequest struct {
	AudioURL                  string         `json:"audio_url"`
	Prompt                    string         `json:"prompt"`
	EmotionalAudioURL         string         `json:"emotional_audio_url,omitempty"`
	Strength                  *float64       `json:"strength,omitempty"`
	EmotionalStrengths        map[string]any `json:"emotional_strengths,omitempty"`
	ShouldUsePromptForEmotion *bool          `json:"should_use_prompt_for_emotion,omitempty"`
	EmotionPrompt             string         `json:"emotion_prompt,omitempty"`
}

type falTTSMetadata struct {
	AudioURL                  string         `json:"audio_url"`
	EmotionalAudioURL         string         `json:"emotional_audio_url,omitempty"`
	Strength                  *float64       `json:"strength,omitempty"`
	EmotionalStrengths        map[string]any `json:"emotional_strengths,omitempty"`
	ShouldUsePromptForEmotion *bool          `json:"should_use_prompt_for_emotion,omitempty"`
	EmotionPrompt             string         `json:"emotion_prompt,omitempty"`
}

type falElevenLabsTTSRequest struct {
	Text                   string   `json:"text"`
	Voice                  string   `json:"voice,omitempty"`
	Stability              *float64 `json:"stability,omitempty"`
	Timestamps             *bool    `json:"timestamps,omitempty"`
	LanguageCode           string   `json:"language_code,omitempty"`
	ApplyTextNormalization string   `json:"apply_text_normalization,omitempty"`
}

type falElevenLabsTTSMetadata struct {
	Voice                  string   `json:"voice,omitempty"`
	Stability              *float64 `json:"stability,omitempty"`
	Timestamps             *bool    `json:"timestamps,omitempty"`
	LanguageCode           string   `json:"language_code,omitempty"`
	ApplyTextNormalization string   `json:"apply_text_normalization,omitempty"`
}

type falElevenLabsMusicRequest struct {
	Prompt                   string          `json:"prompt,omitempty"`
	CompositionPlan          json.RawMessage `json:"composition_plan,omitempty"`
	MusicLengthMS            *int            `json:"music_length_ms,omitempty"`
	ForceInstrumental        *bool           `json:"force_instrumental,omitempty"`
	RespectSectionsDurations *bool           `json:"respect_sections_durations,omitempty"`
	OutputFormat             string          `json:"output_format,omitempty"`
}

type falElevenLabsMusicMetadata struct {
	CompositionPlan          json.RawMessage `json:"composition_plan,omitempty"`
	MusicLengthMS            *int            `json:"music_length_ms,omitempty"`
	ForceInstrumental        *bool           `json:"force_instrumental,omitempty"`
	RespectSectionsDurations *bool           `json:"respect_sections_durations,omitempty"`
	OutputFormat             string          `json:"output_format,omitempty"`
}

type falTTSResponse struct {
	Audio any `json:"audio"`
}

type falAudioObject struct {
	URL string `json:"url"`
}

func buildFalAudioRequest(c *gin.Context, modelID string, expectedKind relaycommon.DCMediaAudioKind, request dto.AudioRequest) (any, error) {
	profile, err := relaycommon.NormalizeDCMediaAudioRequest(&request)
	if err != nil {
		return nil, err
	}
	if profile.Kind != expectedKind {
		return nil, fmt.Errorf("fal audio model %s requires %s request metadata, got %s", modelID, expectedKind, profile.Kind)
	}
	if request.ResponseFormat != "" {
		c.Set(contextKeyResponseFormat, request.ResponseFormat)
	}
	switch modelID {
	case ModelElevenLabsTTSElevenV3ID:
		c.Set(contextKeyResponseFormat, defaultResponseFormat)
		return buildFalElevenLabsTTSRequest(request)
	case ModelElevenLabsMusicID:
		falReq, err := buildFalElevenLabsMusicRequest(request)
		if err != nil {
			return nil, err
		}
		if falReq.OutputFormat != "" {
			c.Set(contextKeyResponseFormat, audioFormatFromFalOutputFormat(falReq.OutputFormat))
		}
		return falReq, nil
	case ModelIndexTTS2FalID:
		return buildFalTTSRequest(request)
	default:
		return nil, fmt.Errorf("unsupported fal audio model: %s", modelID)
	}
}

func buildFalTTSRequest(request dto.AudioRequest) (*falTTSRequest, error) {
	input := strings.TrimSpace(request.Input)
	if input == "" {
		return nil, fmt.Errorf("input is required")
	}
	var metadata falTTSMetadata
	if len(request.Metadata) > 0 {
		if err := common.Unmarshal(request.Metadata, &metadata); err != nil {
			return nil, fmt.Errorf("invalid fal tts metadata: %w", err)
		}
	}
	audioURL := strings.TrimSpace(metadata.AudioURL)
	if audioURL == "" {
		return nil, fmt.Errorf("metadata.audio_url is required")
	}
	falReq := &falTTSRequest{
		AudioURL:                  audioURL,
		Prompt:                    input,
		EmotionalAudioURL:         strings.TrimSpace(metadata.EmotionalAudioURL),
		Strength:                  metadata.Strength,
		EmotionalStrengths:        metadata.EmotionalStrengths,
		ShouldUsePromptForEmotion: metadata.ShouldUsePromptForEmotion,
		EmotionPrompt:             strings.TrimSpace(metadata.EmotionPrompt),
	}
	if falReq.ShouldUsePromptForEmotion == nil && falReq.EmotionPrompt != "" {
		value := true
		falReq.ShouldUsePromptForEmotion = &value
	}
	if strings.TrimSpace(request.Instructions) != "" && falReq.EmotionPrompt == "" {
		falReq.EmotionPrompt = strings.TrimSpace(request.Instructions)
		if falReq.ShouldUsePromptForEmotion == nil {
			value := true
			falReq.ShouldUsePromptForEmotion = &value
		}
	}
	return falReq, nil
}

func buildFalElevenLabsTTSRequest(request dto.AudioRequest) (*falElevenLabsTTSRequest, error) {
	input := strings.TrimSpace(request.Input)
	if input == "" {
		return nil, fmt.Errorf("input is required")
	}
	var metadata falElevenLabsTTSMetadata
	if len(request.Metadata) > 0 {
		if err := common.Unmarshal(request.Metadata, &metadata); err != nil {
			return nil, fmt.Errorf("invalid fal elevenlabs tts metadata: %w", err)
		}
	}
	voice := strings.TrimSpace(request.Voice)
	if voice == "" {
		voice = strings.TrimSpace(metadata.Voice)
	}
	return &falElevenLabsTTSRequest{
		Text:                   input,
		Voice:                  voice,
		Stability:              metadata.Stability,
		Timestamps:             metadata.Timestamps,
		LanguageCode:           strings.TrimSpace(metadata.LanguageCode),
		ApplyTextNormalization: strings.TrimSpace(metadata.ApplyTextNormalization),
	}, nil
}

func buildFalElevenLabsMusicRequest(request dto.AudioRequest) (*falElevenLabsMusicRequest, error) {
	input := strings.TrimSpace(request.Input)
	var metadata falElevenLabsMusicMetadata
	if len(request.Metadata) > 0 {
		if err := common.Unmarshal(request.Metadata, &metadata); err != nil {
			return nil, fmt.Errorf("invalid fal elevenlabs music metadata: %w", err)
		}
	}
	if input == "" && len(metadata.CompositionPlan) == 0 {
		return nil, fmt.Errorf("input or metadata.composition_plan is required")
	}
	if metadata.MusicLengthMS != nil && (*metadata.MusicLengthMS < 3000 || *metadata.MusicLengthMS > 600000) {
		return nil, fmt.Errorf("metadata.music_length_ms must be between 3000 and 600000")
	}
	outputFormat, err := falMusicOutputFormat(request.ResponseFormat, metadata.OutputFormat)
	if err != nil {
		return nil, err
	}
	return &falElevenLabsMusicRequest{
		Prompt:                   input,
		CompositionPlan:          metadata.CompositionPlan,
		MusicLengthMS:            metadata.MusicLengthMS,
		ForceInstrumental:        metadata.ForceInstrumental,
		RespectSectionsDurations: metadata.RespectSectionsDurations,
		OutputFormat:             outputFormat,
	}, nil
}

func falMusicOutputFormat(responseFormat string, metadataOutputFormat string) (string, error) {
	outputFormat := strings.TrimSpace(metadataOutputFormat)
	if outputFormat != "" {
		return outputFormat, nil
	}
	format := strings.ToLower(strings.TrimSpace(responseFormat))
	switch format {
	case "":
		return "", nil
	case "mp3":
		return "mp3_44100_128", nil
	case "opus":
		return "opus_48000_128", nil
	case "pcm":
		return "pcm_44100", nil
	case "ulaw":
		return "ulaw_8000", nil
	case "alaw":
		return "alaw_8000", nil
	default:
		if strings.Contains(format, "_") {
			return format, nil
		}
		return "", fmt.Errorf("unsupported fal elevenlabs music response_format: %s", responseFormat)
	}
}

func handleFalTTSResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (usage any, err *types.NewAPIError) {
	defer service.CloseResponseBodyGracefully(resp)
	body, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		return nil, types.NewOpenAIError(readErr, types.ErrorCodeReadResponseBodyFailed, http.StatusInternalServerError)
	}
	var falResp falTTSResponse
	if unmarshalErr := common.Unmarshal(body, &falResp); unmarshalErr != nil {
		return nil, types.NewOpenAIError(
			fmt.Errorf("fal tts response decode failed: %w; body: %s", unmarshalErr, string(body)),
			types.ErrorCodeBadResponseBody,
			http.StatusBadGateway,
		)
	}
	audioSource := extractFalAudioSource(falResp.Audio)
	if audioSource == "" {
		return nil, types.NewOpenAIError(
			fmt.Errorf("fal tts response missing audio: %s", string(body)),
			types.ErrorCodeBadResponseBody,
			http.StatusBadGateway,
		)
	}
	audioBytes, contentType, downloadErr := resolveFalAudio(c, info, audioSource)
	if downloadErr != nil {
		return nil, types.NewOpenAIError(downloadErr, types.ErrorCodeBadResponseBody, http.StatusBadGateway)
	}
	if contentType == "" {
		contentType = contentTypeForFormat(responseFormat(c))
	}
	c.Data(http.StatusOK, contentType, audioBytes)
	return falTTSUsage(c, info, audioBytes, contentType), nil
}

func extractFalAudioSource(audio any) string {
	switch value := audio.(type) {
	case string:
		return strings.TrimSpace(value)
	case map[string]any:
		if url, ok := value["url"].(string); ok {
			return strings.TrimSpace(url)
		}
		if data, ok := value["data"].(string); ok {
			return strings.TrimSpace(data)
		}
	default:
		var obj falAudioObject
		if payload, err := common.Marshal(value); err == nil && common.Unmarshal(payload, &obj) == nil {
			return strings.TrimSpace(obj.URL)
		}
	}
	return ""
}

func resolveFalAudio(c *gin.Context, info *relaycommon.RelayInfo, audioSource string) ([]byte, string, error) {
	if strings.HasPrefix(audioSource, "data:") {
		return decodeDataURLAudio(audioSource)
	}
	if !strings.HasPrefix(audioSource, "http://") && !strings.HasPrefix(audioSource, "https://") {
		decoded, err := base64.StdEncoding.DecodeString(audioSource)
		if err != nil {
			return nil, "", fmt.Errorf("fal tts audio is neither URL nor base64 data")
		}
		return decoded, contentTypeForFormat(responseFormat(c)), nil
	}
	client, err := service.GetHttpClientWithProxy(info.ChannelSetting.Proxy)
	if err != nil {
		return nil, "", err
	}
	req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodGet, audioSource, nil)
	if err != nil {
		return nil, "", err
	}
	audioResp, err := client.Do(req)
	if err != nil {
		return nil, "", fmt.Errorf("download fal tts audio failed: %w", err)
	}
	defer service.CloseResponseBodyGracefully(audioResp)
	if audioResp.StatusCode < 200 || audioResp.StatusCode >= 300 {
		return nil, "", fmt.Errorf("download fal tts audio failed with status: %d", audioResp.StatusCode)
	}
	body, err := io.ReadAll(audioResp.Body)
	if err != nil {
		return nil, "", fmt.Errorf("read fal tts audio failed: %w", err)
	}
	return body, audioResp.Header.Get("Content-Type"), nil
}

func decodeDataURLAudio(dataURL string) ([]byte, string, error) {
	comma := strings.Index(dataURL, ",")
	if comma < 0 {
		return nil, "", fmt.Errorf("invalid data URL audio")
	}
	meta := dataURL[:comma]
	payload := dataURL[comma+1:]
	if !strings.Contains(strings.ToLower(meta), ";base64") {
		return nil, "", fmt.Errorf("fal tts data URL audio must be base64")
	}
	decoded, err := base64.StdEncoding.DecodeString(payload)
	if err != nil {
		return nil, "", fmt.Errorf("decode fal tts data URL audio failed: %w", err)
	}
	contentType := strings.TrimPrefix(strings.Split(meta, ";")[0], "data:")
	return decoded, contentType, nil
}

func responseFormat(c *gin.Context) string {
	format := strings.TrimSpace(c.GetString(contextKeyResponseFormat))
	if format == "" {
		format = defaultResponseFormat
	}
	return format
}

func contentTypeForFormat(format string) string {
	switch strings.ToLower(strings.TrimSpace(format)) {
	case "wav":
		return "audio/wav"
	case "flac":
		return "audio/flac"
	case "aac":
		return "audio/aac"
	case "opus":
		return "audio/opus"
	case "pcm":
		return "audio/pcm"
	case "ulaw", "alaw":
		return "audio/basic"
	default:
		return "audio/mpeg"
	}
}

func falTTSUsage(c *gin.Context, info *relaycommon.RelayInfo, audioBytes []byte, contentType string) *dto.Usage {
	usage := &dto.Usage{}
	var audioReq dto.AudioRequest
	if info != nil {
		if req, ok := info.Request.(*dto.AudioRequest); ok {
			audioReq = *req
		}
	}
	modelID, _, _ := resolveFalAudioModel(falAudioModelName(info, audioReq))
	if modelID == ModelElevenLabsTTSElevenV3ID {
		inputChars := info.GetEstimatePromptTokens()
		if audioReq.Input != "" {
			inputChars = utf8.RuneCountInString(audioReq.Input)
		}
		usage.PromptTokens = inputChars
		usage.PromptTokensDetails.TextTokens = inputChars
		usage.TotalTokens = inputChars
		return usage
	}
	usage.PromptTokens = info.GetEstimatePromptTokens()
	usage.PromptTokensDetails.TextTokens = usage.PromptTokens
	reader := bytes.NewReader(audioBytes)
	durationExt := falAudioDurationExt(audioBytes, contentType, responseFormat(c))
	duration, durationErr := common.GetAudioDuration(c.Request.Context(), reader, durationExt)
	if durationErr != nil || duration <= 0 {
		estimatedTokens := int(math.Ceil(float64(len(audioBytes)) / 1000.0))
		usage.CompletionTokens = estimatedTokens
		usage.CompletionTokenDetails.AudioTokens = estimatedTokens
	} else if modelID == ModelElevenLabsMusicID {
		completionTokens := int(math.Ceil(duration/60.0) * 1000)
		usage.CompletionTokens = completionTokens
		usage.CompletionTokenDetails.AudioTokens = completionTokens
	} else {
		completionTokens := int(math.Round(math.Ceil(duration) / 60.0 * 1000))
		usage.CompletionTokens = completionTokens
		usage.CompletionTokenDetails.AudioTokens = completionTokens
	}
	usage.TotalTokens = usage.PromptTokens + usage.CompletionTokens
	return usage
}

func falAudioDurationExt(audioBytes []byte, contentType string, responseFormat string) string {
	if ext := audioExtFromMagic(audioBytes); ext != "" {
		return ext
	}
	if ext := audioExtFromContentType(contentType); ext != "" {
		return ext
	}
	format := strings.ToLower(strings.TrimSpace(responseFormat))
	if format == "" {
		format = defaultResponseFormat
	}
	if idx := strings.Index(format, "_"); idx > 0 {
		format = format[:idx]
	}
	return "." + format
}

func audioExtFromMagic(audioBytes []byte) string {
	if len(audioBytes) >= 12 && string(audioBytes[0:4]) == "RIFF" && string(audioBytes[8:12]) == "WAVE" {
		return ".wav"
	}
	if len(audioBytes) >= 3 && string(audioBytes[0:3]) == "ID3" {
		return ".mp3"
	}
	if len(audioBytes) >= 2 && audioBytes[0] == 0xff && (audioBytes[1]&0xe0) == 0xe0 {
		return ".mp3"
	}
	if len(audioBytes) >= 4 {
		switch string(audioBytes[0:4]) {
		case "OggS":
			return ".ogg"
		case "fLaC":
			return ".flac"
		}
	}
	return ""
}

func audioExtFromContentType(contentType string) string {
	contentType = strings.ToLower(strings.TrimSpace(strings.Split(contentType, ";")[0]))
	switch contentType {
	case "audio/wav", "audio/wave", "audio/x-wav":
		return ".wav"
	case "audio/mpeg", "audio/mp3":
		return ".mp3"
	case "audio/ogg", "audio/opus":
		return ".ogg"
	case "audio/flac", "audio/x-flac":
		return ".flac"
	case "audio/aac":
		return ".aac"
	case "audio/mp4", "audio/m4a":
		return ".m4a"
	default:
		return ""
	}
}

func audioFormatFromFalOutputFormat(outputFormat string) string {
	format := strings.ToLower(strings.TrimSpace(outputFormat))
	if format == "" {
		return defaultResponseFormat
	}
	if idx := strings.Index(format, "_"); idx > 0 {
		return format[:idx]
	}
	return format
}
