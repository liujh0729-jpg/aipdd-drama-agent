package doubao_audio

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"strings"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	channelconstant "github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/relay/channel"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

const (
	ChannelName              = "doubao_audio"
	audioGenerationPath      = "/api/v3/tts/create"
	contextKeyResponseFormat = "doubao_audio_response_format"
	maxTextPromptCharacters  = 3000
	maxOutputDurationMS      = 120_000
	maxReferenceAudioBytes   = 10 * 1024 * 1024
)

type Adaptor struct{}

type audioGenerationRequest struct {
	Model       string                `json:"model"`
	TextPrompt  string                `json:"text_prompt"`
	References  []audioReference      `json:"references,omitempty"`
	AudioConfig audioGenerationConfig `json:"audio_config"`
}

type audioReference struct {
	Speaker   string `json:"speaker,omitempty"`
	AudioData string `json:"audio_data,omitempty"`
	AudioURL  string `json:"audio_url,omitempty"`
}

type audioGenerationConfig struct {
	Format     string `json:"format"`
	SpeechRate *int   `json:"speech_rate,omitempty"`
}

type audioGenerationResponse struct {
	Code             int     `json:"code"`
	Message          string  `json:"message"`
	Audio            string  `json:"audio"`
	Duration         float64 `json:"duration"`
	OriginalDuration float64 `json:"original_duration"`
	URL              string  `json:"url"`
}

func (a *Adaptor) Init(info *relaycommon.RelayInfo) {}

func (a *Adaptor) GetRequestURL(info *relaycommon.RelayInfo) (string, error) {
	if info == nil {
		return "", errors.New("doubao audio relay info is required")
	}
	baseURL := ""
	if info.ChannelMeta != nil {
		baseURL = strings.TrimRight(strings.TrimSpace(info.ChannelBaseUrl), "/")
	}
	if baseURL == "" {
		baseURL = channelconstant.ChannelBaseURLs[channelconstant.ChannelTypeDoubaoAudio]
	}
	if strings.HasSuffix(baseURL, audioGenerationPath) {
		return baseURL, nil
	}
	return baseURL + audioGenerationPath, nil
}

func (a *Adaptor) SetupRequestHeader(c *gin.Context, header *http.Header, info *relaycommon.RelayInfo) error {
	if info == nil || info.ChannelMeta == nil || strings.TrimSpace(info.ApiKey) == "" {
		return errors.New("doubao audio api key is required")
	}
	channel.SetupApiRequestHeader(info, c, header)
	header.Set("Content-Type", "application/json")
	header.Set("Accept", "application/json")
	header.Set("X-Api-Key", strings.TrimSpace(info.ApiKey))
	if requestID := strings.TrimSpace(info.RequestId); requestID != "" {
		header.Set("X-Api-Request-Id", requestID)
	}
	return nil
}

func (a *Adaptor) ConvertAudioRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.AudioRequest) (io.Reader, error) {
	if info == nil {
		return nil, errors.New("doubao audio relay info is required")
	}
	if info.RelayMode != relayconstant.RelayModeAudioSpeech {
		return nil, fmt.Errorf("unsupported doubao audio relay mode: %d", info.RelayMode)
	}
	if request.IsStream(c.Request) {
		return nil, fmt.Errorf("doubao audio does not support stream_format=%q", request.StreamFormat)
	}

	profile, err := relaycommon.NormalizeDCMediaAudioRequest(&request)
	if err != nil {
		return nil, err
	}
	if profile.Kind == relaycommon.DCMediaAudioKindMusic &&
		profile.Metadata.MusicLengthMS != nil &&
		*profile.Metadata.MusicLengthMS > maxOutputDurationMS {
		return nil, fmt.Errorf("doubao audio metadata.music_length_ms must not exceed %d", maxOutputDurationMS)
	}
	modelName := ""
	if info.ChannelMeta != nil {
		modelName = strings.TrimSpace(info.UpstreamModelName)
	}
	if modelName == "" {
		modelName = strings.TrimSpace(request.Model)
	}
	if modelName == "" {
		return nil, errors.New("doubao audio upstream model is required")
	}

	format, err := doubaoAudioFormat(request.ResponseFormat, profile.Metadata.OutputFormat)
	if err != nil {
		return nil, err
	}
	prompt := strings.TrimSpace(request.Input)
	references := make([]audioReference, 0, 1)

	if profile.Kind == relaycommon.DCMediaAudioKindReferenceSpeech {
		if strings.TrimSpace(request.Voice) != "" {
			return nil, errors.New("doubao audio voice and metadata.audio_url cannot be combined")
		}
		reference, err := doubaoAudioReference(profile.Metadata.AudioURL)
		if err != nil {
			return nil, err
		}
		references = append(references, reference)
		if !strings.Contains(prompt, "@音频1") {
			prompt += "\n请参考@音频1的音色与表达方式。"
		}
		if profile.Metadata.ShouldUsePromptForEmotion == nil || *profile.Metadata.ShouldUsePromptForEmotion {
			if emotion := strings.TrimSpace(profile.Metadata.EmotionPrompt); emotion != "" {
				prompt += "\n情绪与表演要求：" + emotion
			}
		}
	} else if voice := strings.TrimSpace(request.Voice); voice != "" {
		references = append(references, audioReference{Speaker: voice})
	}

	if instructions := strings.TrimSpace(request.Instructions); instructions != "" {
		prompt += "\n生成要求：" + instructions
	}
	if profile.Kind == relaycommon.DCMediaAudioKindMusic {
		prompt = appendMusicRequirements(prompt, profile.Metadata)
	}
	if utf8.RuneCountInString(prompt) > maxTextPromptCharacters {
		return nil, fmt.Errorf("doubao audio text_prompt exceeds %d characters", maxTextPromptCharacters)
	}

	speechRate, err := doubaoSpeechRate(request.Speed)
	if err != nil {
		return nil, err
	}
	upstreamRequest := audioGenerationRequest{
		Model:      modelName,
		TextPrompt: prompt,
		References: references,
		AudioConfig: audioGenerationConfig{
			Format:     format,
			SpeechRate: speechRate,
		},
	}
	c.Set(contextKeyResponseFormat, request.ResponseFormat)
	body, err := common.Marshal(upstreamRequest)
	if err != nil {
		return nil, fmt.Errorf("marshal doubao audio request: %w", err)
	}
	return bytes.NewReader(body), nil
}

func (a *Adaptor) ConvertOpenAIRequest(*gin.Context, *relaycommon.RelayInfo, *dto.GeneralOpenAIRequest) (any, error) {
	return nil, errors.New("doubao audio supports only /v1/audio/speech")
}

func (a *Adaptor) ConvertRerankRequest(*gin.Context, int, dto.RerankRequest) (any, error) {
	return nil, errors.New("doubao audio does not support rerank")
}

func (a *Adaptor) ConvertEmbeddingRequest(*gin.Context, *relaycommon.RelayInfo, dto.EmbeddingRequest) (any, error) {
	return nil, errors.New("doubao audio does not support embeddings")
}

func (a *Adaptor) ConvertImageRequest(*gin.Context, *relaycommon.RelayInfo, dto.ImageRequest) (any, error) {
	return nil, errors.New("doubao audio does not support image generation")
}

func (a *Adaptor) ConvertOpenAIResponsesRequest(*gin.Context, *relaycommon.RelayInfo, dto.OpenAIResponsesRequest) (any, error) {
	return nil, errors.New("doubao audio does not support responses")
}

func (a *Adaptor) ConvertClaudeRequest(*gin.Context, *relaycommon.RelayInfo, *dto.ClaudeRequest) (any, error) {
	return nil, errors.New("doubao audio does not support Claude requests")
}

func (a *Adaptor) ConvertGeminiRequest(*gin.Context, *relaycommon.RelayInfo, *dto.GeminiChatRequest) (any, error) {
	return nil, errors.New("doubao audio does not support Gemini requests")
}

func (a *Adaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (any, error) {
	return channel.DoApiRequest(a, c, info, requestBody)
}

func (a *Adaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (usage any, relayErr *types.NewAPIError) {
	if resp == nil {
		return nil, types.NewOpenAIError(errors.New("doubao audio returned an empty response"), types.ErrorCodeBadResponse, http.StatusBadGateway)
	}
	defer service.CloseResponseBodyGracefully(resp)
	if logID := strings.TrimSpace(resp.Header.Get("X-Tt-Logid")); logID != "" {
		c.Set(common.UpstreamRequestIdKey, logID)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeReadResponseBodyFailed, http.StatusBadGateway)
	}
	var upstream audioGenerationResponse
	if err := common.Unmarshal(body, &upstream); err != nil {
		return nil, types.NewOpenAIError(
			fmt.Errorf("decode doubao audio response: %w", err),
			types.ErrorCodeBadResponseBody,
			http.StatusBadGateway,
		)
	}
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices || upstream.Code != 0 {
		message := strings.TrimSpace(upstream.Message)
		if message == "" {
			message = fmt.Sprintf("doubao audio upstream returned HTTP %d with code %d", resp.StatusCode, upstream.Code)
		} else if upstream.Code != 0 {
			message = fmt.Sprintf("doubao audio upstream code %d: %s", upstream.Code, message)
		}
		status := resp.StatusCode
		if status < http.StatusBadRequest {
			status = http.StatusBadGateway
		}
		return nil, types.NewOpenAIError(errors.New(message), types.ErrorCodeBadResponse, status)
	}

	usage, err = doubaoAudioUsage(info, upstream.OriginalDuration, upstream.Duration)
	if err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeBadResponseBody, http.StatusBadGateway)
	}
	audio := strings.TrimSpace(upstream.Audio)
	if audio != "" {
		if comma := strings.Index(audio, ","); strings.HasPrefix(strings.ToLower(audio), "data:") && comma >= 0 {
			audio = audio[comma+1:]
		}
		audioBytes, err := base64.StdEncoding.DecodeString(audio)
		if err != nil {
			return nil, types.NewOpenAIError(
				fmt.Errorf("decode doubao audio data: %w", err),
				types.ErrorCodeBadResponseBody,
				http.StatusBadGateway,
			)
		}
		contentType := doubaoAudioContentType(c.GetString(contextKeyResponseFormat))
		c.Data(http.StatusOK, contentType, audioBytes)
		return usage, nil
	}
	if resultURL := strings.TrimSpace(upstream.URL); resultURL != "" {
		c.JSON(http.StatusOK, gin.H{"audio": gin.H{"url": resultURL}})
		return usage, nil
	}
	return nil, types.NewOpenAIError(
		errors.New("doubao audio response contains neither audio data nor URL"),
		types.ErrorCodeBadResponseBody,
		http.StatusBadGateway,
	)
}

func (a *Adaptor) DoErrorResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) *types.NewAPIError {
	_, relayErr := a.DoResponse(c, resp, info)
	return relayErr
}

func (a *Adaptor) GetModelList() []string { return nil }

func (a *Adaptor) GetChannelName() string { return ChannelName }

func (a *Adaptor) GetCapabilities() []string { return []string{"audio"} }

func (a *Adaptor) GetBaseURLPolicy() (bool, bool) { return false, true }

func doubaoAudioReference(rawURL string) (audioReference, error) {
	rawURL = strings.TrimSpace(rawURL)
	if strings.HasPrefix(strings.ToLower(rawURL), "data:audio/") {
		comma := strings.Index(rawURL, ",")
		if comma < 0 {
			return audioReference{}, errors.New("invalid doubao audio data URL")
		}
		encoded := rawURL[comma+1:]
		decodedLen := base64.StdEncoding.DecodedLen(len(encoded))
		if strings.HasSuffix(encoded, "=") {
			decodedLen--
		}
		if strings.HasSuffix(encoded, "==") {
			decodedLen--
		}
		if decodedLen > maxReferenceAudioBytes {
			return audioReference{}, fmt.Errorf(
				"doubao audio reference exceeds maximum allowed size of %d bytes",
				maxReferenceAudioBytes,
			)
		}
		if _, err := io.Copy(io.Discard, base64.NewDecoder(base64.StdEncoding, strings.NewReader(encoded))); err != nil {
			return audioReference{}, fmt.Errorf("invalid doubao audio base64 data: %w", err)
		}
		return audioReference{AudioData: encoded}, nil
	}
	return audioReference{AudioURL: rawURL}, nil
}

func doubaoAudioFormat(responseFormat, outputFormat string) (string, error) {
	requested := strings.ToLower(strings.TrimSpace(responseFormat))
	providerFormat := strings.ToLower(strings.TrimSpace(outputFormat))
	if providerFormat != "" {
		switch {
		case providerFormat == "mp3", providerFormat == "ogg_opus", providerFormat == "pcm":
		case strings.HasPrefix(providerFormat, "mp3_"):
			providerFormat = "mp3"
		case strings.HasPrefix(providerFormat, "opus_"):
			providerFormat = "ogg_opus"
		case strings.HasPrefix(providerFormat, "pcm_"):
			providerFormat = "pcm"
		default:
			return "", fmt.Errorf("doubao audio does not support metadata.output_format=%q", outputFormat)
		}
	}

	var mapped string
	switch requested {
	case "mp3":
		mapped = "mp3"
	case "opus":
		mapped = "ogg_opus"
	case "pcm":
		mapped = "pcm"
	default:
		return "", fmt.Errorf("doubao audio does not support response_format=%q", responseFormat)
	}
	if providerFormat != "" && providerFormat != mapped {
		return "", fmt.Errorf("doubao audio response_format=%q conflicts with metadata.output_format=%q", responseFormat, outputFormat)
	}
	return mapped, nil
}

func doubaoSpeechRate(speed *float64) (*int, error) {
	if speed == nil {
		return nil, nil
	}
	if *speed < 0.5 || *speed > 2 {
		return nil, errors.New("doubao audio speed must be between 0.5 and 2.0")
	}
	rate := int(math.Round((*speed - 1) * 100))
	return &rate, nil
}

func appendMusicRequirements(prompt string, metadata relaycommon.DCMediaAudioMetadata) string {
	if metadata.MusicLengthMS != nil {
		seconds := float64(*metadata.MusicLengthMS) / 1000
		prompt += fmt.Sprintf("\n目标音频总时长约 %.3g 秒。", seconds)
	}
	if metadata.ForceInstrumental != nil && *metadata.ForceInstrumental {
		prompt += "\n仅生成纯音乐，不包含人声。"
	}
	if metadata.RespectSectionsDurations != nil && *metadata.RespectSectionsDurations {
		prompt += "\n严格遵循提示词中各段落的时长安排。"
	}
	return prompt
}

func doubaoAudioContentType(responseFormat string) string {
	switch strings.ToLower(strings.TrimSpace(responseFormat)) {
	case "opus":
		return "audio/ogg"
	case "pcm":
		return "audio/pcm"
	default:
		return "audio/mpeg"
	}
}

func doubaoAudioUsage(info *relaycommon.RelayInfo, originalDuration, duration float64) (*dto.Usage, error) {
	if originalDuration <= 0 {
		originalDuration = duration
	}
	audioTokens := 0
	if originalDuration > 0 {
		var clamp *common.QuotaClamp
		audioTokens, clamp = common.QuotaFromFloatChecked(math.Ceil(originalDuration))
		if clamp != nil {
			if info != nil && info.QuotaClamp == nil {
				info.QuotaClamp = clamp
			}
			return nil, fmt.Errorf("doubao audio returned invalid original_duration: %w", clamp)
		}
		if originalDuration > float64(maxOutputDurationMS)/1000 {
			return nil, fmt.Errorf(
				"doubao audio original_duration %.3g exceeds provider limit of %d seconds",
				originalDuration,
				maxOutputDurationMS/1000,
			)
		}
	}
	promptTokens := 0
	if info != nil {
		promptTokens = info.GetEstimatePromptTokens()
	}
	return &dto.Usage{
		PromptTokens:     promptTokens,
		CompletionTokens: audioTokens,
		TotalTokens:      promptTokens + audioTokens,
		CompletionTokenDetails: dto.OutputTokenDetails{
			AudioTokens: audioTokens,
		},
	}, nil
}
