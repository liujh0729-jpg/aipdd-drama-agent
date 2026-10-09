package hailuo

import (
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
)

const (
	h3ModelName    = "MiniMax-H3"
	h3TaskIDPrefix = "h3:"
)

type requestParameterError struct {
	field   string
	message string
}

func (e *requestParameterError) Error() string            { return e.message }
func (e *requestParameterError) TaskErrorCode() string    { return "invalid_parameter" }
func (e *requestParameterError) TaskErrorStatusCode() int { return http.StatusBadRequest }
func (e *requestParameterError) TaskErrorLocal() bool     { return true }
func (e *requestParameterError) TaskErrorData() any       { return map[string]any{"field": e.field} }

type h3VideoRequest struct {
	Model         string          `json:"model"`
	Content       []h3ContentItem `json:"content"`
	Resolution    string          `json:"resolution"`
	Duration      int             `json:"duration"`
	Ratio         string          `json:"ratio,omitempty"`
	CallbackURL   string          `json:"callback_url,omitempty"`
	AigcWatermark *bool           `json:"aigc_watermark,omitempty"`
}

type h3ContentItem struct {
	Type     string       `json:"type"`
	Text     string       `json:"text,omitempty"`
	ImageURL *h3URLObject `json:"image_url,omitempty"`
	VideoURL *h3URLObject `json:"video_url,omitempty"`
	AudioURL *h3URLObject `json:"audio_url,omitempty"`
	Role     string       `json:"role,omitempty"`
}

type h3URLObject struct {
	URL string `json:"url"`
}

type h3VideoMetadata struct {
	ReferenceImages []string `json:"reference_images,omitempty"`
	ReferenceVideos []string `json:"reference_videos,omitempty"`
	ReferenceAudios []string `json:"reference_audios,omitempty"`
	ReferenceFile   string   `json:"reference_file,omitempty"`
	ReferenceLink   string   `json:"reference_link,omitempty"`
	LastFrameImage  string   `json:"last_frame_image,omitempty"`
	Resolution      string   `json:"resolution,omitempty"`
	Ratio           string   `json:"ratio,omitempty"`
	CallbackURL     string   `json:"callback_url,omitempty"`
	AigcWatermark   *bool    `json:"aigc_watermark,omitempty"`
	Watermark       *bool    `json:"watermark,omitempty"`
}

type h3CreateResponse struct {
	TaskID string       `json:"task_id"`
	Error  *h3ErrorBody `json:"error,omitempty"`
}

type h3QueryResponse struct {
	Task  h3Task       `json:"task"`
	Error *h3ErrorBody `json:"error,omitempty"`
}

type h3Task struct {
	ID         string        `json:"id"`
	Status     string        `json:"status"`
	Content    h3TaskContent `json:"content,omitempty"`
	Resolution string        `json:"resolution,omitempty"`
	Duration   int           `json:"duration,omitempty"`
	Ratio      string        `json:"ratio,omitempty"`
	Error      *h3TaskError  `json:"error,omitempty"`
}

type h3TaskContent struct {
	URL string `json:"url,omitempty"`
}

type h3TaskError struct {
	Code    string `json:"code,omitempty"`
	Message string `json:"message,omitempty"`
}

type h3ErrorBody struct {
	Type     string `json:"type,omitempty"`
	Message  string `json:"message,omitempty"`
	HTTPCode string `json:"http_code,omitempty"`
}

func isH3Model(model string) bool {
	return strings.EqualFold(strings.TrimSpace(model), h3ModelName)
}

func h3VideoRequestFromTask(req relaycommon.TaskSubmitReq) (*h3VideoRequest, error) {
	shape, err := relaycommon.ValidateDCMediaTaskRequest(&req)
	if err != nil {
		return nil, err
	}
	if shape == relaycommon.DCMediaVideoEdit {
		return nil, &requestParameterError{
			field:   "duration",
			message: "MiniMax-H3 generation API does not support video editing with duration=auto",
		}
	}
	if req.Prompt == "" {
		return nil, &requestParameterError{field: "prompt", message: "prompt is required for MiniMax-H3"}
	}

	metadata := h3VideoMetadata{}
	if err := req.UnmarshalMetadata(&metadata); err != nil {
		return nil, err
	}
	if strings.TrimSpace(metadata.ReferenceFile) != "" {
		return nil, &requestParameterError{
			field:   "metadata.reference_file",
			message: "MiniMax-H3 does not support reference files",
		}
	}
	if strings.TrimSpace(metadata.ReferenceLink) != "" {
		return nil, &requestParameterError{
			field:   "metadata.reference_link",
			message: "MiniMax-H3 does not support reference links",
		}
	}
	if len(metadata.ReferenceImages) > 9 {
		return nil, h3MediaLimitError("metadata.reference_images", 9)
	}
	if len(metadata.ReferenceVideos) > 3 {
		return nil, h3MediaLimitError("metadata.reference_videos", 3)
	}
	if len(metadata.ReferenceAudios) > 3 {
		return nil, h3MediaLimitError("metadata.reference_audios", 3)
	}
	if len(metadata.ReferenceAudios) > 0 && len(metadata.ReferenceImages) == 0 && len(metadata.ReferenceVideos) == 0 {
		return nil, &requestParameterError{
			field:   "metadata.reference_audios",
			message: "MiniMax-H3 reference audio requires at least one reference image or video",
		}
	}

	content := []h3ContentItem{{Type: "text", Text: req.Prompt}}
	appendH3ImageContent := func(rawURL, role string) {
		if rawURL = strings.TrimSpace(rawURL); rawURL != "" {
			content = append(content, h3ContentItem{Type: "image_url", ImageURL: &h3URLObject{URL: rawURL}, Role: role})
		}
	}
	appendH3ImageContent(req.Image, "first_frame")
	appendH3ImageContent(metadata.LastFrameImage, "last_frame")
	for _, image := range metadata.ReferenceImages {
		appendH3ImageContent(image, "reference_image")
	}
	for _, video := range metadata.ReferenceVideos {
		if video = strings.TrimSpace(video); video != "" {
			content = append(content, h3ContentItem{Type: "video_url", VideoURL: &h3URLObject{URL: video}, Role: "reference_video"})
		}
	}
	for _, audio := range metadata.ReferenceAudios {
		if audio = strings.TrimSpace(audio); audio != "" {
			content = append(content, h3ContentItem{Type: "audio_url", AudioURL: &h3URLObject{URL: audio}, Role: "reference_audio"})
		}
	}

	duration, err := normalizeH3Duration(req.Duration, req.DurationAuto)
	if err != nil {
		return nil, err
	}
	ratio := metadata.Ratio
	if ratio == "" && req.Width > 0 && req.Height > 0 {
		ratio = h3AspectRatioFromDimensions(req.Width, req.Height)
	}
	ratio, err = normalizeH3Ratio(ratio, shape)
	if err != nil {
		return nil, err
	}
	resolution := metadata.Resolution
	if resolution == "" && req.Width > 0 && req.Height > 0 {
		resolution = h3ResolutionFromDimensions(req.Width, req.Height)
	}
	resolution, err = normalizeH3Resolution(resolution)
	if err != nil {
		return nil, err
	}

	watermark := metadata.AigcWatermark
	if watermark == nil {
		watermark = metadata.Watermark
	}
	return &h3VideoRequest{
		Model:         h3ModelName,
		Content:       content,
		Resolution:    resolution,
		Duration:      duration,
		Ratio:         ratio,
		CallbackURL:   strings.TrimSpace(metadata.CallbackURL),
		AigcWatermark: watermark,
	}, nil
}

func normalizeH3Duration(value int, automatic bool) (int, error) {
	if automatic {
		return 0, &requestParameterError{field: "duration", message: "MiniMax-H3 does not support duration=auto"}
	}
	if value == 0 {
		return 5, nil
	}
	if value < 4 || value > 15 {
		return 0, &requestParameterError{field: "duration", message: "MiniMax-H3 duration must be between 4 and 15 seconds"}
	}
	return value, nil
}

func normalizeH3Ratio(value string, shape relaycommon.DCMediaCallShape) (string, error) {
	if shape == relaycommon.DCMediaFirstFrame || shape == relaycommon.DCMediaFirstLastFrame {
		return "adaptive", nil
	}
	ratio := strings.ToLower(strings.TrimSpace(value))
	if shape == relaycommon.DCMediaImageToVideo || shape == relaycommon.DCMediaImageReference || shape == relaycommon.DCMediaAllReference {
		if ratio == "" || ratio == "auto" || ratio == "adaptive" {
			return "adaptive", nil
		}
	}
	if ratio == "" {
		return "16:9", nil
	}
	if ratio == "auto" || ratio == "adaptive" {
		return "", &requestParameterError{field: "metadata.ratio", message: "MiniMax-H3 text-to-video requires a fixed ratio"}
	}
	valid := map[string]bool{"21:9": true, "16:9": true, "4:3": true, "1:1": true, "3:4": true, "9:16": true}
	if !valid[ratio] {
		return "", &requestParameterError{field: "metadata.ratio", message: fmt.Sprintf("MiniMax-H3 does not support ratio %q", value)}
	}
	return ratio, nil
}

func h3MediaLimitError(field string, maximum int) error {
	return &requestParameterError{field: field, message: fmt.Sprintf("MiniMax-H3 %s supports at most %d items", field, maximum)}
}

func normalizeH3Resolution(value string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "2k", "1440p":
		return "2K", nil
	case "768p", "720p":
		return "768P", nil
	default:
		return "", &requestParameterError{field: "metadata.resolution", message: fmt.Sprintf("MiniMax-H3 does not support resolution %q", value)}
	}
}

func h3AspectRatioFromDimensions(width, height int) string {
	if width <= 0 || height <= 0 {
		return ""
	}
	for _, candidate := range []struct {
		name          string
		width, height int64
	}{
		{name: "21:9", width: 21, height: 9},
		{name: "16:9", width: 16, height: 9},
		{name: "4:3", width: 4, height: 3},
		{name: "1:1", width: 1, height: 1},
		{name: "3:4", width: 3, height: 4},
		{name: "9:16", width: 9, height: 16},
	} {
		actual := float64(width) / float64(height)
		expected := float64(candidate.width) / float64(candidate.height)
		if actual/expected > 0.98 && actual/expected < 1.02 {
			return candidate.name
		}
	}
	return ""
}

func h3ResolutionFromDimensions(width, height int) string {
	if min(width, height) <= 768 {
		return "768P"
	}
	return "2K"
}

func encodeH3TaskID(taskID string) string {
	taskID = strings.TrimSpace(taskID)
	if taskID == "" || strings.HasPrefix(taskID, h3TaskIDPrefix) {
		return taskID
	}
	return h3TaskIDPrefix + taskID
}

func decodeH3TaskID(taskID string) (string, bool) {
	taskID = strings.TrimSpace(taskID)
	if !strings.HasPrefix(taskID, h3TaskIDPrefix) {
		return taskID, false
	}
	return strings.TrimPrefix(taskID, h3TaskIDPrefix), true
}

func h3FetchURL(baseURL, taskID string) string {
	return fmt.Sprintf("%s%s/%s", strings.TrimRight(baseURL, "/"), H3QueryTaskEndpoint, url.PathEscape(taskID))
}

func h3TaskInfo(resp h3QueryResponse) *relaycommon.TaskInfo {
	result := &relaycommon.TaskInfo{TaskID: resp.Task.ID}
	switch resp.Task.Status {
	case "queued":
		result.Status, result.Progress = model.TaskStatusSubmitted, "20%"
	case "running":
		result.Status, result.Progress = model.TaskStatusInProgress, "50%"
	case "succeeded":
		result.Status, result.Progress, result.Url = model.TaskStatusSuccess, "100%", strings.TrimSpace(resp.Task.Content.URL)
	case "failed", "cancelled":
		result.Status, result.Progress = model.TaskStatusFailure, "100%"
		if resp.Task.Error != nil {
			result.Reason = firstNonEmpty(resp.Task.Error.Message, resp.Task.Error.Code)
			result.Code, _ = strconv.Atoi(resp.Task.Error.Code)
		}
		if result.Reason == "" {
			result.Reason = resp.Task.Status
		}
	default:
		result.Status, result.Progress = model.TaskStatusInProgress, "30%"
	}
	return result
}

func h3OpenAIVideoError(task h3Task) *dto.OpenAIVideoError {
	if task.Error == nil {
		return nil
	}
	return &dto.OpenAIVideoError{Message: task.Error.Message, Code: task.Error.Code}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value = strings.TrimSpace(value); value != "" {
			return value
		}
	}
	return ""
}
