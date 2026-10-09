package fal

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/relay/channel"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/gin-gonic/gin"
)

type Adaptor struct{}

func (a *Adaptor) Init(info *relaycommon.RelayInfo) {}

func (a *Adaptor) GetRequestURL(info *relaycommon.RelayInfo) (string, error) {
	switch info.RelayMode {
	case relayconstant.RelayModeAudioSpeech:
		modelID, _, ok := resolveFalAudioModel(info.UpstreamModelName)
		if !ok {
			return "", fmt.Errorf("unsupported fal audio model: %s", info.UpstreamModelName)
		}
		return fmt.Sprintf("%s/%s", strings.TrimRight(info.ChannelBaseUrl, "/"), modelID), nil
	case relayconstant.RelayModeImagesGenerations, relayconstant.RelayModeImagesEdits:
		modelID := normalizeFalImageModelID(info.UpstreamModelName)
		return fmt.Sprintf("%s/%s", strings.TrimRight(info.ChannelBaseUrl, "/"), modelID), nil
	default:
		return "", fmt.Errorf("unsupported fal relay mode: %d", info.RelayMode)
	}
}

func (a *Adaptor) SetupRequestHeader(c *gin.Context, header *http.Header, info *relaycommon.RelayInfo) error {
	channel.SetupApiRequestHeader(info, c, header)
	header.Set("Content-Type", "application/json")
	header.Set("Accept", "application/json")
	header.Set("Authorization", "Key "+info.ApiKey)
	return nil
}

func (a *Adaptor) ConvertAudioRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.AudioRequest) (io.Reader, error) {
	if info.RelayMode != relayconstant.RelayModeAudioSpeech {
		return nil, fmt.Errorf("unsupported fal audio relay mode: %d", info.RelayMode)
	}
	if request.IsStream(c.Request) {
		return nil, fmt.Errorf("fal audio speech does not support stream_format=%q", request.StreamFormat)
	}
	modelID, kind, ok := resolveFalAudioModel(falAudioModelName(info, request))
	if !ok {
		return nil, fmt.Errorf("unsupported fal audio model: %s", falAudioModelName(info, request))
	}
	request.Model = modelID
	falReq, err := buildFalAudioRequest(c, modelID, kind, request)
	if err != nil {
		return nil, err
	}
	body, err := common.Marshal(falReq)
	if err != nil {
		return nil, fmt.Errorf("marshal fal audio request: %w", err)
	}
	return bytes.NewReader(body), nil
}

func (a *Adaptor) ConvertOpenAIRequest(c *gin.Context, info *relaycommon.RelayInfo, request *dto.GeneralOpenAIRequest) (any, error) {
	return nil, errors.New("fal adaptor: ConvertOpenAIRequest is not implemented")
}

func (a *Adaptor) ConvertRerankRequest(c *gin.Context, relayMode int, request dto.RerankRequest) (any, error) {
	return nil, errors.New("fal adaptor: ConvertRerankRequest is not implemented")
}

func (a *Adaptor) ConvertEmbeddingRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.EmbeddingRequest) (any, error) {
	return nil, errors.New("fal adaptor: ConvertEmbeddingRequest is not implemented")
}

func (a *Adaptor) ConvertImageRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.ImageRequest) (any, error) {
	if info.RelayMode != relayconstant.RelayModeImagesGenerations && info.RelayMode != relayconstant.RelayModeImagesEdits {
		return nil, fmt.Errorf("unsupported fal image relay mode: %d", info.RelayMode)
	}
	if info.RelayMode == relayconstant.RelayModeImagesEdits && len(request.Image) == 0 && len(request.Images) == 0 {
		return nil, errors.New("image is required for fal image edits")
	}
	return buildFalImageRequest(c, info, request)
}

func (a *Adaptor) ConvertOpenAIResponsesRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.OpenAIResponsesRequest) (any, error) {
	return nil, errors.New("fal adaptor: ConvertOpenAIResponsesRequest is not implemented")
}

func (a *Adaptor) ConvertClaudeRequest(c *gin.Context, info *relaycommon.RelayInfo, request *dto.ClaudeRequest) (any, error) {
	return nil, errors.New("fal adaptor: ConvertClaudeRequest is not implemented")
}

func (a *Adaptor) ConvertGeminiRequest(c *gin.Context, info *relaycommon.RelayInfo, request *dto.GeminiChatRequest) (any, error) {
	return nil, errors.New("fal adaptor: ConvertGeminiRequest is not implemented")
}

func (a *Adaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (any, error) {
	return channel.DoApiRequest(a, c, info, requestBody)
}

func (a *Adaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (usage any, err *types.NewAPIError) {
	switch info.RelayMode {
	case relayconstant.RelayModeAudioSpeech:
		return handleFalTTSResponse(c, resp, info)
	case relayconstant.RelayModeImagesGenerations, relayconstant.RelayModeImagesEdits:
		return handleFalImageResponse(c, resp, info)
	default:
		return nil, types.NewOpenAIError(
			fmt.Errorf("unsupported fal relay mode: %d", info.RelayMode),
			types.ErrorCodeInvalidRequest,
			http.StatusBadRequest,
			types.ErrOptionWithSkipRetry(),
		)
	}
}

func (a *Adaptor) GetModelList() []string {
	return ModelList
}

func (a *Adaptor) GetChannelName() string {
	return ChannelName
}

func (a *Adaptor) GetCapabilities() []string {
	return []string{"image", "audio"}
}

func falAudioModelName(info *relaycommon.RelayInfo, request dto.AudioRequest) string {
	if info != nil {
		if info.ChannelMeta != nil && strings.TrimSpace(info.UpstreamModelName) != "" {
			return strings.TrimSpace(info.UpstreamModelName)
		}
		if strings.TrimSpace(info.OriginModelName) != "" {
			return strings.TrimSpace(info.OriginModelName)
		}
	}
	return strings.TrimSpace(request.Model)
}

func resolveFalAudioModel(model string) (string, relaycommon.DCMediaAudioKind, bool) {
	switch strings.TrimPrefix(strings.TrimSpace(model), "/") {
	case ModelIndexTTS2, ModelIndexTTS2FalID:
		return ModelIndexTTS2FalID, relaycommon.DCMediaAudioKindReferenceSpeech, true
	case ModelElevenLabsTTSElevenV3, ModelElevenLabsTTSElevenV3ID:
		return ModelElevenLabsTTSElevenV3ID, relaycommon.DCMediaAudioKindSpeech, true
	case ModelElevenLabsMusic, ModelElevenLabsMusicID:
		return ModelElevenLabsMusicID, relaycommon.DCMediaAudioKindMusic, true
	default:
		return "", "", false
	}
}
