package fal

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

const contextKeyImageResponseFormat = "fal_image_response_format"

type falImageModelFamily string

const (
	falImageFamilyGPTImage2   falImageModelFamily = "gpt-image-2"
	falImageFamilyNanoBanana2 falImageModelFamily = "nano-banana-2"
	falImageFamilyUnknown     falImageModelFamily = ""
)

var falImageSourceToBase64Func = falImageSourceToBase64

var falGPTImage2SizeEnums = map[string]struct{}{
	"square_hd":      {},
	"square":         {},
	"portrait_4_3":   {},
	"portrait_16_9":  {},
	"landscape_4_3":  {},
	"landscape_16_9": {},
	"auto":           {},
}

var falNanoBanana2AspectRatioEnums = map[string]struct{}{
	"auto": {},
	"21:9": {},
	"16:9": {},
	"3:2":  {},
	"4:3":  {},
	"5:4":  {},
	"1:1":  {},
	"4:5":  {},
	"3:4":  {},
	"2:3":  {},
	"9:16": {},
	"4:1":  {},
	"1:4":  {},
	"8:1":  {},
	"1:8":  {},
}

type falImageFile struct {
	URL         string `json:"url"`
	ContentType string `json:"content_type,omitempty"`
	FileName    string `json:"file_name,omitempty"`
	FileSize    int64  `json:"file_size,omitempty"`
	Width       int    `json:"width,omitempty"`
	Height      int    `json:"height,omitempty"`
}

type falImageResponse struct {
	Images []falImageFile `json:"images"`
	Usage  *struct {
		InputTokens            int `json:"input_tokens,omitempty"`
		OutputTokens           int `json:"output_tokens,omitempty"`
		TotalTokens            int `json:"total_tokens,omitempty"`
		InputTokensDetails     any `json:"input_tokens_details,omitempty"`
		OutputTokensDetails    any `json:"output_tokens_details,omitempty"`
		PromptTokens           int `json:"prompt_tokens,omitempty"`
		CompletionTokens       int `json:"completion_tokens,omitempty"`
		PromptTokensDetails    any `json:"prompt_tokens_details,omitempty"`
		CompletionTokenDetails any `json:"completion_tokens_details,omitempty"`
	} `json:"usage,omitempty"`
}

type falImageSize struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

type falImageConfig struct {
	AspectRatio  string `json:"aspect_ratio"`
	Ratio        string `json:"ratio"`
	Resolution   string `json:"resolution"`
	ImageSizeRaw any    `json:"image_size"`
}

func buildFalImageRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.ImageRequest) (any, error) {
	if strings.TrimSpace(request.Prompt) == "" {
		return nil, errors.New("prompt is required")
	}

	imageURLs, err := imageURLsFromRequest(request)
	if err != nil {
		return nil, err
	}
	maskURL, err := maskURLFromRequest(request)
	if err != nil {
		return nil, err
	}
	extraImageURLs, extraMaskURL := falImageReferencesFromExtras(request)
	imageURLs = append(imageURLs, extraImageURLs...)
	if maskURL == "" {
		maskURL = extraMaskURL
	}

	hasEditInput := len(imageURLs) > 0 || maskURL != ""
	endpointID, family, err := resolveFalImageEndpoint(info, request, hasEditInput)
	if err != nil {
		return nil, err
	}
	if isFalImageEditEndpoint(endpointID) && len(imageURLs) == 0 {
		return nil, fmt.Errorf("image_urls is required for fal %s edit", falImageFamilyName(family))
	}
	info.UpstreamModelName = endpointID

	body := map[string]any{}
	cfg := falImageConfig{}
	mergeFalImageExtraFields(body, request.ExtraFields, &cfg)
	mergeFalImageExtraMap(body, request.Extra, &cfg)
	mergeFalImageProtocolFields(body, request, &cfg)
	if err := applyFalImageDimensions(body, &cfg); err != nil {
		return nil, err
	}

	body["prompt"] = request.Prompt
	if isFalImageEditEndpoint(endpointID) {
		body["image_urls"] = imageURLs
		if family == falImageFamilyGPTImage2 && maskURL != "" {
			body["mask_url"] = maskURL
		}
	}
	if request.N != nil {
		if *request.N < 1 || *request.N > 4 {
			return nil, fmt.Errorf("fal %s num_images must be between 1 and 4", falImageFamilyName(family))
		}
		body["num_images"] = int(*request.N)
	}
	if family != falImageFamilyNanoBanana2 && strings.TrimSpace(request.Quality) != "" {
		body["quality"] = strings.TrimSpace(request.Quality)
	}
	if outputFormat := outputFormatFromImageRequest(request); outputFormat != "" {
		body["output_format"] = outputFormat
	}
	switch family {
	case falImageFamilyNanoBanana2:
		if err := prepareFalNanoBanana2Params(body, request, cfg); err != nil {
			return nil, err
		}
	default:
		if imageSize, ok := resolveFalImageSize(request, cfg, body["image_size"]); ok {
			body["image_size"] = imageSize
		} else {
			delete(body, "image_size")
		}
	}
	if request.ResponseFormat != "" {
		c.Set(contextKeyImageResponseFormat, request.ResponseFormat)
		if strings.EqualFold(request.ResponseFormat, "b64_json") {
			body["sync_mode"] = true
		}
	}
	return body, nil
}

func mergeFalImageProtocolFields(body map[string]any, request dto.ImageRequest, cfg *falImageConfig) {
	if request.Width != 0 {
		body["width"] = request.Width
	}
	if request.Height != 0 {
		body["height"] = request.Height
	}
	if len(request.Metadata) != 0 {
		mergeFalImageExtra(body, map[string]any{"metadata": request.Metadata}, cfg)
	}
}

func resolveFalImageEndpoint(info *relaycommon.RelayInfo, request dto.ImageRequest, hasEditInput bool) (string, falImageModelFamily, error) {
	origin := ""
	upstream := ""
	if info != nil {
		origin = strings.TrimSpace(info.OriginModelName)
		upstream = strings.TrimSpace(info.UpstreamModelName)
	}
	requestModel := strings.TrimSpace(request.Model)

	for _, model := range []string{origin, requestModel} {
		model = strings.TrimPrefix(strings.TrimSpace(model), "/")
		if model == "" {
			continue
		}
		if endpointID, family, matched, err := resolveKnownFalImageEndpoint(model, origin, hasEditInput); matched || err != nil {
			return endpointID, family, err
		}
	}

	upstream = strings.TrimPrefix(upstream, "/")
	if endpointID, family, matched, err := resolveKnownFalImageEndpoint(upstream, origin, hasEditInput); matched || err != nil {
		return endpointID, family, err
	}
	if upstream == "" {
		if hasEditInput {
			return ModelGPTImage2EditID, falImageFamilyGPTImage2, nil
		}
		return ModelGPTImage2TextID, falImageFamilyGPTImage2, nil
	}
	return upstream, falImageFamilyUnknown, nil
}

func resolveKnownFalImageEndpoint(model string, origin string, hasEditInput bool) (string, falImageModelFamily, bool, error) {
	model = strings.TrimPrefix(strings.TrimSpace(model), "/")
	switch model {
	case ModelGPTImage2:
		if hasEditInput {
			return ModelGPTImage2EditID, falImageFamilyGPTImage2, true, nil
		}
		return ModelGPTImage2TextID, falImageFamilyGPTImage2, true, nil
	case ModelGPTImage2Text:
		if hasEditInput {
			return "", falImageFamilyGPTImage2, true, errors.New("gpt-image-2-text does not support image or mask input; use gpt-image-2 or gpt-image-2-edit")
		}
		return ModelGPTImage2TextID, falImageFamilyGPTImage2, true, nil
	case ModelGPTImage2Edit:
		return ModelGPTImage2EditID, falImageFamilyGPTImage2, true, nil
	case ModelGPTImage2TextID:
		if hasEditInput && origin == ModelGPTImage2 {
			return ModelGPTImage2EditID, falImageFamilyGPTImage2, true, nil
		}
		return ModelGPTImage2TextID, falImageFamilyGPTImage2, true, nil
	case ModelGPTImage2EditID:
		return ModelGPTImage2EditID, falImageFamilyGPTImage2, true, nil
	case ModelNanoBanana2:
		if hasEditInput {
			return ModelNanoBanana2EditID, falImageFamilyNanoBanana2, true, nil
		}
		return ModelNanoBanana2TextID, falImageFamilyNanoBanana2, true, nil
	case ModelNanoBanana2Text:
		if hasEditInput {
			return "", falImageFamilyNanoBanana2, true, errors.New("nano-banana-2-text does not support image or mask input; use nano-banana-2 or nano-banana-2-edit")
		}
		return ModelNanoBanana2TextID, falImageFamilyNanoBanana2, true, nil
	case ModelNanoBanana2Edit:
		return ModelNanoBanana2EditID, falImageFamilyNanoBanana2, true, nil
	case ModelNanoBanana2TextID:
		if hasEditInput && origin == ModelNanoBanana2 {
			return ModelNanoBanana2EditID, falImageFamilyNanoBanana2, true, nil
		}
		return ModelNanoBanana2TextID, falImageFamilyNanoBanana2, true, nil
	case ModelNanoBanana2EditID:
		return ModelNanoBanana2EditID, falImageFamilyNanoBanana2, true, nil
	default:
		return "", falImageFamilyUnknown, false, nil
	}
}

func isFalImageEditEndpoint(model string) bool {
	switch strings.TrimPrefix(strings.TrimSpace(model), "/") {
	case ModelGPTImage2EditID, ModelNanoBanana2EditID:
		return true
	default:
		return false
	}
}

func falImageFamilyName(family falImageModelFamily) string {
	if family == "" {
		return "image"
	}
	return string(family)
}

func normalizeFalImageModelID(model string) string {
	model = strings.TrimPrefix(strings.TrimSpace(model), "/")
	switch model {
	case "", ModelGPTImage2, ModelGPTImage2Text:
		return ModelGPTImage2TextID
	case ModelGPTImage2Edit:
		return ModelGPTImage2EditID
	case ModelNanoBanana2, ModelNanoBanana2Text:
		return ModelNanoBanana2TextID
	case ModelNanoBanana2Edit:
		return ModelNanoBanana2EditID
	default:
		return model
	}
}

func mergeFalImageExtraFields(body map[string]any, raw []byte, cfg *falImageConfig) {
	if len(raw) == 0 {
		return
	}
	var extra map[string]any
	if err := common.Unmarshal(raw, &extra); err != nil {
		return
	}
	mergeFalImageExtra(body, extra, cfg)
}

func mergeFalImageExtraMap(body map[string]any, raw map[string]json.RawMessage, cfg *falImageConfig) {
	if len(raw) == 0 {
		return
	}
	extra := make(map[string]any, len(raw))
	for key, value := range raw {
		var decoded any
		if err := common.Unmarshal(value, &decoded); err == nil {
			extra[key] = decoded
		}
	}
	mergeFalImageExtra(body, extra, cfg)
}

func mergeFalImageExtra(body map[string]any, extra map[string]any, cfg *falImageConfig) {
	for key, value := range extra {
		switch key {
		case "prompt", "image_urls", "mask_url":
			continue
		case "aspect_ratio":
			cfg.AspectRatio, _ = value.(string)
			continue
		case "ratio":
			cfg.Ratio, _ = value.(string)
			continue
		case "resolution":
			cfg.Resolution, _ = value.(string)
			continue
		case "image_size":
			cfg.ImageSizeRaw = value
			body[key] = value
			continue
		case "metadata":
			metadata, ok := value.(map[string]any)
			if !ok {
				continue
			}
			for metadataKey, metadataValue := range metadata {
				switch metadataKey {
				case "aspect_ratio":
					cfg.AspectRatio, _ = metadataValue.(string)
				case "ratio":
					cfg.Ratio, _ = metadataValue.(string)
				case "resolution":
					cfg.Resolution, _ = metadataValue.(string)
				case "image_size":
					cfg.ImageSizeRaw = metadataValue
					body[metadataKey] = metadataValue
				}
			}
			continue
		}
		body[key] = value
	}
}

func applyFalImageDimensions(body map[string]any, cfg *falImageConfig) error {
	width, widthSet := numberToInt(body["width"])
	height, heightSet := numberToInt(body["height"])
	delete(body, "width")
	delete(body, "height")
	if !widthSet && !heightSet {
		return nil
	}
	if !widthSet || !heightSet || width <= 0 || height <= 0 {
		return errors.New("width and height must be positive and provided together")
	}
	body["image_size"] = falImageSize{Width: width, Height: height}
	if cfg.AspectRatio == "" && cfg.Ratio == "" {
		divisor := gcd(width, height)
		cfg.AspectRatio = fmt.Sprintf("%d:%d", width/divisor, height/divisor)
	}
	return nil
}

func imageURLsFromRequest(request dto.ImageRequest) ([]string, error) {
	var images []string
	if len(request.Image) > 0 {
		values, err := falImageValuesFromRaw(request.Image)
		if err != nil {
			return nil, fmt.Errorf("invalid image field: %w", err)
		}
		images = append(images, values...)
	}
	if len(request.Images) > 0 {
		values, err := falImageValuesFromRaw(request.Images)
		if err != nil {
			return nil, fmt.Errorf("invalid images field: %w", err)
		}
		images = append(images, values...)
	}
	return images, nil
}

func maskURLFromRequest(request dto.ImageRequest) (string, error) {
	if len(request.Mask) == 0 {
		return "", nil
	}
	values, err := falImageValuesFromRaw(request.Mask)
	if err != nil {
		return "", fmt.Errorf("invalid mask field: %w", err)
	}
	if len(values) == 0 {
		return "", nil
	}
	return values[0], nil
}

func falImageValuesFromRaw(raw []byte) ([]string, error) {
	if len(raw) == 0 || strings.TrimSpace(string(raw)) == "null" {
		return nil, nil
	}
	var single string
	if err := common.Unmarshal(raw, &single); err == nil && strings.TrimSpace(single) != "" {
		return []string{strings.TrimSpace(single)}, nil
	}
	var multiple []string
	if err := common.Unmarshal(raw, &multiple); err == nil {
		out := make([]string, 0, len(multiple))
		for _, item := range multiple {
			if strings.TrimSpace(item) != "" {
				out = append(out, strings.TrimSpace(item))
			}
		}
		return out, nil
	}
	var wrapped []struct {
		URL string `json:"url"`
	}
	if err := common.Unmarshal(raw, &wrapped); err == nil {
		out := make([]string, 0, len(wrapped))
		for _, item := range wrapped {
			if strings.TrimSpace(item.URL) != "" {
				out = append(out, strings.TrimSpace(item.URL))
			}
		}
		return out, nil
	}
	var object struct {
		URL string `json:"url"`
	}
	if err := common.Unmarshal(raw, &object); err == nil && strings.TrimSpace(object.URL) != "" {
		return []string{strings.TrimSpace(object.URL)}, nil
	}
	return nil, errors.New("unsupported image reference format")
}

func falImageReferencesFromExtras(request dto.ImageRequest) ([]string, string) {
	var imageURLs []string
	var maskURL string
	collect := func(extra map[string]any) {
		if len(extra) == 0 {
			return
		}
		if raw, ok := marshalAnyToRaw(extra["image_urls"]); ok {
			if values, err := falImageValuesFromRaw(raw); err == nil {
				imageURLs = append(imageURLs, values...)
			}
		}
		if maskURL == "" {
			if raw, ok := marshalAnyToRaw(extra["mask_url"]); ok {
				if values, err := falImageValuesFromRaw(raw); err == nil && len(values) > 0 {
					maskURL = values[0]
				}
			}
		}
	}
	if len(request.ExtraFields) > 0 {
		var extra map[string]any
		if err := common.Unmarshal(request.ExtraFields, &extra); err == nil {
			collect(extra)
		}
	}
	if len(request.Extra) > 0 {
		extra := make(map[string]any, len(request.Extra))
		for key, value := range request.Extra {
			var decoded any
			if err := common.Unmarshal(value, &decoded); err == nil {
				extra[key] = decoded
			}
		}
		collect(extra)
	}
	return imageURLs, maskURL
}

func marshalAnyToRaw(value any) ([]byte, bool) {
	if value == nil {
		return nil, false
	}
	raw, err := common.Marshal(value)
	return raw, err == nil
}

func outputFormatFromImageRequest(request dto.ImageRequest) string {
	if len(request.OutputFormat) == 0 || strings.TrimSpace(string(request.OutputFormat)) == "null" {
		return ""
	}
	var outputFormat string
	if err := common.Unmarshal(request.OutputFormat, &outputFormat); err != nil {
		return ""
	}
	switch strings.ToLower(strings.TrimSpace(outputFormat)) {
	case "jpg":
		return "jpeg"
	case "jpeg", "png", "webp":
		return strings.ToLower(strings.TrimSpace(outputFormat))
	default:
		return ""
	}
}

func prepareFalNanoBanana2Params(body map[string]any, request dto.ImageRequest, cfg falImageConfig) error {
	delete(body, "image_size")
	delete(body, "quality")
	delete(body, "mask_url")
	delete(body, "ratio")

	if aspectRatio, ok := resolveFalNanoBanana2AspectRatio(request, cfg, body["aspect_ratio"]); ok {
		body["aspect_ratio"] = aspectRatio
	} else if strings.TrimSpace(cfg.AspectRatio) != "" || strings.TrimSpace(cfg.Ratio) != "" {
		return fmt.Errorf("fal nano-banana-2 aspect_ratio %q is not supported", firstNonEmpty(cfg.AspectRatio, cfg.Ratio))
	} else {
		delete(body, "aspect_ratio")
	}
	if resolution, ok := resolveFalNanoBanana2Resolution(cfg, body["resolution"]); ok {
		body["resolution"] = resolution
	} else if strings.TrimSpace(cfg.Resolution) != "" {
		return fmt.Errorf("fal nano-banana-2 resolution %q is not supported", cfg.Resolution)
	} else {
		delete(body, "resolution")
	}
	return nil
}

func resolveFalNanoBanana2AspectRatio(request dto.ImageRequest, cfg falImageConfig, current any) (string, bool) {
	if aspectRatio, ok := normalizeFalNanoBanana2AspectRatio(stringFromAny(current)); ok {
		return aspectRatio, true
	}
	if aspectRatio, ok := normalizeFalNanoBanana2AspectRatio(cfg.AspectRatio); ok {
		return aspectRatio, true
	}
	if aspectRatio, ok := normalizeFalNanoBanana2AspectRatio(cfg.Ratio); ok {
		return aspectRatio, true
	}
	if aspectRatio, ok := aspectRatioFromOpenAISize(request.Size); ok {
		return aspectRatio, true
	}
	return "", false
}

func resolveFalNanoBanana2Resolution(cfg falImageConfig, current any) (string, bool) {
	if resolution, ok := normalizeFalNanoBanana2Resolution(stringFromAny(current)); ok {
		return resolution, true
	}
	if resolution, ok := normalizeFalNanoBanana2Resolution(cfg.Resolution); ok {
		return resolution, true
	}
	if resolution, ok := normalizeFalNanoBanana2Resolution(stringFromAny(cfg.ImageSizeRaw)); ok {
		return resolution, true
	}
	return "", false
}

func normalizeFalNanoBanana2AspectRatio(value string) (string, bool) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", false
	}
	value = strings.ToLower(value)
	if _, ok := falNanoBanana2AspectRatioEnums[value]; ok {
		return value, true
	}
	return "", false
}

func normalizeFalNanoBanana2Resolution(value string) (string, bool) {
	value = strings.ToUpper(strings.ReplaceAll(strings.TrimSpace(value), " ", ""))
	switch value {
	case "0.5K", "1K", "2K", "4K":
		return value, true
	default:
		return "", false
	}
}

func aspectRatioFromOpenAISize(size string) (string, bool) {
	parts := strings.Split(strings.TrimSpace(strings.ToLower(size)), "x")
	if len(parts) != 2 {
		return "", false
	}
	width, errW := strconv.Atoi(strings.TrimSpace(parts[0]))
	height, errH := strconv.Atoi(strings.TrimSpace(parts[1]))
	if errW != nil || errH != nil || width <= 0 || height <= 0 {
		return "", false
	}
	divisor := gcd(width, height)
	ratio := fmt.Sprintf("%d:%d", width/divisor, height/divisor)
	if _, ok := falNanoBanana2AspectRatioEnums[ratio]; ok {
		return ratio, true
	}
	return "", false
}

func gcd(a int, b int) int {
	for b != 0 {
		a, b = b, a%b
	}
	if a < 0 {
		return -a
	}
	return a
}

func stringFromAny(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case fmt.Stringer:
		return v.String()
	default:
		return ""
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func resolveFalImageSize(request dto.ImageRequest, cfg falImageConfig, current any) (any, bool) {
	if isValidFalImageSize(current) {
		return current, true
	}
	if size, ok := imageSizeFromOpenAISize(request.Size); ok {
		return size, true
	}
	ratio := strings.TrimSpace(cfg.AspectRatio)
	if ratio == "" {
		ratio = strings.TrimSpace(cfg.Ratio)
	}
	if ratio == "" {
		return nil, false
	}
	if size, ok := imageSizeFromRatioAndResolution(ratio, cfg.Resolution); ok {
		return size, true
	}
	return nil, false
}

func isValidFalImageSize(value any) bool {
	switch v := value.(type) {
	case string:
		_, ok := falGPTImage2SizeEnums[strings.TrimSpace(v)]
		return ok
	case map[string]any:
		width, okW := numberToInt(v["width"])
		height, okH := numberToInt(v["height"])
		return okW && okH && width > 0 && height > 0
	case falImageSize:
		return v.Width > 0 && v.Height > 0
	case nil:
		return false
	default:
		return false
	}
}

func imageSizeFromOpenAISize(size string) (falImageSize, bool) {
	parts := strings.Split(strings.TrimSpace(size), "x")
	if len(parts) != 2 {
		return falImageSize{}, false
	}
	width, errW := strconv.Atoi(strings.TrimSpace(parts[0]))
	height, errH := strconv.Atoi(strings.TrimSpace(parts[1]))
	if errW != nil || errH != nil || width <= 0 || height <= 0 {
		return falImageSize{}, false
	}
	return falImageSize{Width: roundToMultiple(width, 16), Height: roundToMultiple(height, 16)}, true
}

func imageSizeFromRatioAndResolution(ratio string, resolution string) (falImageSize, bool) {
	parts := strings.Split(strings.TrimSpace(ratio), ":")
	if len(parts) != 2 {
		return falImageSize{}, false
	}
	rw, errW := strconv.Atoi(strings.TrimSpace(parts[0]))
	rh, errH := strconv.Atoi(strings.TrimSpace(parts[1]))
	if errW != nil || errH != nil || rw <= 0 || rh <= 0 {
		return falImageSize{}, false
	}
	longEdge := resolutionLongEdge(resolution)
	if longEdge <= 0 {
		longEdge = 1024
	}
	var width, height int
	if rw >= rh {
		width = longEdge
		height = int(math.Round(float64(longEdge) * float64(rh) / float64(rw)))
	} else {
		height = longEdge
		width = int(math.Round(float64(longEdge) * float64(rw) / float64(rh)))
	}
	return falImageSize{Width: roundToMultiple(width, 16), Height: roundToMultiple(height, 16)}, true
}

func resolutionLongEdge(resolution string) int {
	switch strings.ToLower(strings.TrimSpace(resolution)) {
	case "1k":
		return 1024
	case "2k":
		return 2048
	case "4k":
		return 3840
	default:
		return 0
	}
}

func roundToMultiple(value int, multiple int) int {
	if multiple <= 0 || value <= 0 {
		return value
	}
	rounded := int(math.Round(float64(value)/float64(multiple))) * multiple
	if rounded < multiple {
		return multiple
	}
	return rounded
}

func numberToInt(value any) (int, bool) {
	switch v := value.(type) {
	case int:
		return v, true
	case int64:
		return int(v), true
	case float64:
		return int(v), true
	case float32:
		return int(v), true
	default:
		return 0, false
	}
}

func handleFalImageResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (*dto.Usage, *types.NewAPIError) {
	defer service.CloseResponseBodyGracefully(resp)
	body, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		return nil, types.NewOpenAIError(readErr, types.ErrorCodeReadResponseBodyFailed, http.StatusInternalServerError)
	}
	var falResp falImageResponse
	if err := common.Unmarshal(body, &falResp); err != nil {
		return nil, types.NewOpenAIError(
			fmt.Errorf("fal image response decode failed: %w; body: %s", err, string(body)),
			types.ErrorCodeBadResponseBody,
			http.StatusBadGateway,
		)
	}
	if len(falResp.Images) == 0 {
		return nil, types.NewOpenAIError(
			fmt.Errorf("fal image response missing images: %s", string(body)),
			types.ErrorCodeBadResponseBody,
			http.StatusBadGateway,
		)
	}

	wantsBase64 := strings.EqualFold(c.GetString(contextKeyImageResponseFormat), "b64_json")
	imageResponse := dto.ImageResponse{
		Created: time.Now().Unix(),
		Data:    make([]dto.ImageData, 0, len(falResp.Images)),
	}
	for _, image := range falResp.Images {
		source := strings.TrimSpace(image.URL)
		if source == "" {
			continue
		}
		if wantsBase64 {
			b64, err := falImageSourceToBase64Func(source)
			if err != nil {
				return nil, types.NewError(err, types.ErrorCodeBadResponse)
			}
			imageResponse.Data = append(imageResponse.Data, dto.ImageData{B64Json: b64})
		} else {
			imageResponse.Data = append(imageResponse.Data, dto.ImageData{Url: source})
		}
	}
	if len(imageResponse.Data) == 0 {
		return nil, types.NewOpenAIError(
			errors.New("fal image response has no usable image"),
			types.ErrorCodeBadResponseBody,
			http.StatusBadGateway,
		)
	}
	metadata, _ := common.Marshal(map[string]any{"images": falResp.Images})
	imageResponse.Metadata = metadata

	responseBytes, err := common.Marshal(imageResponse)
	if err != nil {
		return nil, types.NewError(err, types.ErrorCodeBadResponseBody)
	}
	c.Writer.Header().Set("Content-Type", "application/json")
	c.Writer.WriteHeader(http.StatusOK)
	if _, err := c.Writer.Write(responseBytes); err != nil {
		return nil, types.NewError(err, types.ErrorCodeBadResponseBody)
	}
	return usageFromFalImageResponse(&falResp), nil
}

func falImageSourceToBase64(source string) (string, error) {
	if strings.HasPrefix(source, "data:") {
		_, data, err := service.DecodeBase64FileData(source)
		return data, err
	}
	_, data, err := service.GetImageFromUrl(source)
	return data, err
}

func usageFromFalImageResponse(response *falImageResponse) *dto.Usage {
	if response == nil || response.Usage == nil {
		return &dto.Usage{}
	}
	usage := &dto.Usage{
		PromptTokens:     response.Usage.InputTokens,
		CompletionTokens: response.Usage.OutputTokens,
		TotalTokens:      response.Usage.TotalTokens,
		InputTokens:      response.Usage.InputTokens,
		OutputTokens:     response.Usage.OutputTokens,
		UsageSource:      "fal",
	}
	if usage.PromptTokens == 0 {
		usage.PromptTokens = response.Usage.PromptTokens
	}
	if usage.CompletionTokens == 0 {
		usage.CompletionTokens = response.Usage.CompletionTokens
	}
	if usage.TotalTokens == 0 {
		usage.TotalTokens = usage.PromptTokens + usage.CompletionTokens
	}
	return usage
}
