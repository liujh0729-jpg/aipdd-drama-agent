package common

import (
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
)

type DCMediaCallShape string

const (
	DCMediaTextToVideo    DCMediaCallShape = "text_to_video"
	DCMediaImageToVideo   DCMediaCallShape = "image_to_video"
	DCMediaFirstFrame     DCMediaCallShape = "first_frame"
	DCMediaFirstLastFrame DCMediaCallShape = "first_last_frame"
	DCMediaImageReference DCMediaCallShape = "image_reference"
	DCMediaAllReference   DCMediaCallShape = "all_reference"
	DCMediaVideoEdit      DCMediaCallShape = "video_edit"
)

type DCMediaMetadata struct {
	Ratio           string   `json:"ratio,omitempty"`
	Resolution      string   `json:"resolution,omitempty"`
	LastFrameImage  string   `json:"last_frame_image,omitempty"`
	ReferenceImages []string `json:"reference_images,omitempty"`
	ReferenceVideos []string `json:"reference_videos,omitempty"`
	ReferenceAudios []string `json:"reference_audios,omitempty"`
	ReferenceFile   string   `json:"reference_file,omitempty"`
	ReferenceLink   string   `json:"reference_link,omitempty"`
	GenerateAudio   *bool    `json:"generate_audio,omitempty"`
	HumanReview     *bool    `json:"human_review,omitempty"`
	Watermark       *bool    `json:"watermark,omitempty"`
	OutputFormat    string   `json:"output_format,omitempty"`
	ReturnLastFrame *bool    `json:"return_last_frame,omitempty"`
}

type DCMediaValidationError struct {
	Code    string
	Field   string
	Message string
}

func (e *DCMediaValidationError) Error() string { return e.Message }

func DCMediaValidationErrorCode(err error) string {
	var validationErr *DCMediaValidationError
	if errors.As(err, &validationErr) && validationErr.Code != "" {
		return validationErr.Code
	}
	return "invalid_media_request"
}

func NormalizeDCMediaTaskRequest(req *TaskSubmitReq) error {
	if req == nil {
		return fmt.Errorf("request is required")
	}
	req.Model = strings.TrimSpace(req.Model)
	req.Prompt = strings.TrimSpace(req.Prompt)
	req.Image = strings.TrimSpace(req.Image)
	req.Images = nonEmptyMediaStrings(req.Images)
	if req.N == 0 {
		req.N = 1
	}
	if req.ResponseFormat == "" {
		req.ResponseFormat = "url"
	}

	if req.Metadata == nil {
		req.Metadata = map[string]interface{}{}
	}
	normalizeDCMediaLegacyMetadata(req)

	ratio := normalizedDCMediaRatio(metadataString(req.Metadata, "ratio"))
	if ratio != "" {
		req.Metadata["ratio"] = ratio
	}
	if resolution := normalizedDCMediaResolution(metadataString(req.Metadata, "resolution")); resolution != "" {
		req.Metadata["resolution"] = resolution
	}
	for _, key := range []string{"last_frame_image", "reference_file", "reference_link"} {
		if value := metadataString(req.Metadata, key); value != "" {
			req.Metadata[key] = value
		}
	}
	for _, key := range []string{"reference_images", "reference_videos", "reference_audios"} {
		if values, ok := metadataStringSlice(req.Metadata[key]); ok {
			req.Metadata[key] = nonEmptyMediaStrings(values)
		}
	}

	if (req.Width == 0 || req.Height == 0) && req.Size != "" {
		if width, height, ok := parseDCMediaDimensions(req.Size); ok {
			req.Width, req.Height = width, height
		}
	}
	return nil
}

func ValidateDCMediaTaskRequest(req *TaskSubmitReq) (DCMediaCallShape, error) {
	if err := NormalizeDCMediaTaskRequest(req); err != nil {
		return "", err
	}
	if req.ImagesSet {
		return "", dcMediaError(
			"unsupported_media_field",
			"images",
			"images is not part of DC-Media; use image for the first frame or metadata.reference_images for references",
		)
	}
	if req.Width < 0 || req.Height < 0 || (req.Width == 0) != (req.Height == 0) {
		return "", dcMediaError("invalid_dimensions", "width,height", "width and height must be positive and provided together")
	}
	if req.N != 1 {
		return "", dcMediaError("invalid_output_count", "n", "n must be 1 for video generation")
	}

	metadata := DCMediaMetadata{}
	if err := req.UnmarshalMetadata(&metadata); err != nil {
		return "", err
	}
	hasFirstFrame := req.Image != ""
	hasLastFrame := strings.TrimSpace(metadata.LastFrameImage) != ""
	hasImages := len(nonEmptyMediaStrings(metadata.ReferenceImages)) > 0
	hasVideos := len(nonEmptyMediaStrings(metadata.ReferenceVideos)) > 0
	hasAudios := len(nonEmptyMediaStrings(metadata.ReferenceAudios)) > 0
	hasFile := strings.TrimSpace(metadata.ReferenceFile) != ""
	hasLink := strings.TrimSpace(metadata.ReferenceLink) != ""

	if hasFirstFrame && hasImages {
		return "", dcMediaError("conflicting_media_inputs", "image,metadata.reference_images", "top-level image cannot be combined with metadata.reference_images")
	}
	if hasFirstFrame && (hasFile || hasLink) {
		return "", dcMediaError("conflicting_media_inputs", "image,metadata.reference_file,metadata.reference_link", "top-level image cannot be combined with a reference file or link")
	}
	if hasLastFrame && (hasImages || hasVideos || hasAudios || hasFile || hasLink) {
		return "", dcMediaError("conflicting_media_inputs", "metadata.last_frame_image", "last_frame_image cannot be combined with reference media")
	}
	if hasFile && hasLink {
		return "", dcMediaError("conflicting_media_inputs", "metadata.reference_file,metadata.reference_link", "reference_file and reference_link cannot be used together")
	}
	if strings.EqualFold(metadata.Ratio, "auto") && (req.Width > 0 || req.Height > 0) {
		return "", dcMediaError("conflicting_dimensions", "width,height,metadata.ratio", "fixed dimensions cannot be combined with auto ratio")
	}
	if req.Width > 0 && metadata.Ratio != "" && metadata.Ratio != "auto" && !dcMediaDimensionsMatchRatio(req.Width, req.Height, metadata.Ratio) {
		return "", dcMediaError("inconsistent_dimensions", "width,height,metadata.ratio", "width and height do not match metadata.ratio")
	}
	if req.DurationAuto {
		if hasFile || hasLink {
			return "", dcMediaError(
				"conflicting_media_inputs",
				"duration,metadata.reference_file,metadata.reference_link",
				"duration=auto video editing cannot be combined with a reference file or link",
			)
		}
		if !strings.EqualFold(metadata.Ratio, "auto") || !hasVideos {
			return "", dcMediaError("invalid_auto_duration", "duration", "duration=auto requires ratio=auto and at least one reference video")
		}
		return DCMediaVideoEdit, nil
	}
	if hasLastFrame {
		return DCMediaFirstLastFrame, nil
	}
	if hasFirstFrame {
		return DCMediaFirstFrame, nil
	}
	if hasVideos || hasAudios || hasFile || hasLink {
		return DCMediaAllReference, nil
	}
	if len(nonEmptyMediaStrings(metadata.ReferenceImages)) > 1 {
		return DCMediaImageReference, nil
	}
	if hasImages {
		return DCMediaImageToVideo, nil
	}
	return DCMediaTextToVideo, nil
}

func dcMediaError(code, field, message string) error {
	return &DCMediaValidationError{Code: code, Field: field, Message: message}
}

func normalizeDCMediaLegacyMetadata(req *TaskSubmitReq) {
	metadata := req.Metadata
	if _, exists := metadata["ratio"]; !exists {
		if ratio := metadataString(metadata, "aspect_ratio"); ratio != "" {
			metadata["ratio"] = ratio
		}
	}
	if req.Image == "" {
		for _, key := range []string{"first_frame_image", "image_url"} {
			if image := metadataString(metadata, key); image != "" {
				req.Image = image
				break
			}
		}
	}
	if _, exists := metadata["last_frame_image"]; !exists {
		if image := metadataString(metadata, "end_image_url"); image != "" {
			metadata["last_frame_image"] = image
		}
	}
	copyLegacyMediaArray(metadata, "reference_images", "image_urls")
	copyLegacyMediaArray(metadata, "reference_videos", "video_urls")
	copyLegacyMediaArray(metadata, "reference_audios", "audio_urls")
}

func copyLegacyMediaArray(metadata map[string]interface{}, canonical, legacy string) {
	if _, exists := metadata[canonical]; exists {
		return
	}
	if values, ok := metadataStringSlice(metadata[legacy]); ok && len(values) > 0 {
		metadata[canonical] = values
	}
}

func normalizedDCMediaRatio(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "adaptive" {
		return "auto"
	}
	return value
}

func normalizedDCMediaResolution(value string) string {
	value = strings.TrimSpace(value)
	lower := strings.ToLower(value)
	switch lower {
	case "1k", "2k", "4k", "480p", "720p", "768p", "1080p":
		return lower
	default:
		return value
	}
}

func metadataString(metadata map[string]interface{}, key string) string {
	value, ok := metadata[key].(string)
	if !ok {
		return ""
	}
	return strings.TrimSpace(value)
}

func metadataStringSlice(value interface{}) ([]string, bool) {
	switch values := value.(type) {
	case []string:
		return values, true
	case []interface{}:
		result := make([]string, 0, len(values))
		for _, value := range values {
			text, ok := value.(string)
			if !ok {
				return nil, false
			}
			result = append(result, text)
		}
		return result, true
	default:
		return nil, false
	}
}

func nonEmptyMediaStrings(values []string) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		if value = strings.TrimSpace(value); value != "" {
			result = append(result, value)
		}
	}
	return result
}

func parseDCMediaDimensions(value string) (int, int, bool) {
	normalized := strings.NewReplacer("X", "x", "*", "x", "×", "x").Replace(strings.TrimSpace(value))
	parts := strings.Split(normalized, "x")
	if len(parts) != 2 {
		return 0, 0, false
	}
	width, widthErr := strconv.Atoi(strings.TrimSpace(parts[0]))
	height, heightErr := strconv.Atoi(strings.TrimSpace(parts[1]))
	if widthErr != nil || heightErr != nil || width <= 0 || height <= 0 {
		return 0, 0, false
	}
	return width, height, true
}

func dcMediaDimensionsMatchRatio(width, height int, ratio string) bool {
	parts := strings.Split(strings.TrimSpace(ratio), ":")
	if len(parts) != 2 {
		return true
	}
	ratioWidth, widthErr := strconv.ParseFloat(parts[0], 64)
	ratioHeight, heightErr := strconv.ParseFloat(parts[1], 64)
	if widthErr != nil || heightErr != nil || ratioWidth <= 0 || ratioHeight <= 0 {
		return true
	}
	actual := float64(width) / float64(height)
	expected := ratioWidth / ratioHeight
	return math.Abs(actual-expected)/expected <= 0.02
}
