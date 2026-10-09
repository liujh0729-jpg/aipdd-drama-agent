package fal

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	taskdto "github.com/QuantumNous/new-api/dto"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel"
	basefal "github.com/QuantumNous/new-api/relay/channel/fal"
	taskcommon "github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/pkg/errors"
)

type TaskAdaptor struct {
	taskcommon.BaseBilling
	ChannelType int
	apiKey      string
	baseURL     string
}

type seedanceRoute struct {
	Endpoint  string
	Action    string
	Fast      bool
	V1ProFast bool
}

type queueSubmitResponse struct {
	RequestID   string `json:"request_id"`
	StatusURL   string `json:"status_url,omitempty"`
	ResponseURL string `json:"response_url,omitempty"`
	CancelURL   string `json:"cancel_url,omitempty"`
	Status      string `json:"status,omitempty"`
}

type falFile struct {
	URL string `json:"url"`
}

type falVideoResult struct {
	Video falFile `json:"video"`
	Seed  *int    `json:"seed,omitempty"`
}

var seedanceModelList = []string{
	basefal.ModelSeedance20,
	basefal.ModelSeedance20Text,
	basefal.ModelSeedance20Image,
	basefal.ModelSeedance20Ref,
	basefal.ModelSeedance20TextID,
	basefal.ModelSeedance20ImageID,
	basefal.ModelSeedance20RefID,
	basefal.ModelSeedance20Fast,
	basefal.ModelSeedance20FastText,
	basefal.ModelSeedance20FastImage,
	basefal.ModelSeedance20FastRef,
	basefal.ModelSeedance20FastTextID,
	basefal.ModelSeedance20FastImageID,
	basefal.ModelSeedance20FastRefID,
	basefal.ModelSeedanceV1ProFast,
	basefal.ModelSeedanceV1ProFastText,
	basefal.ModelSeedanceV1ProFastImage,
	basefal.ModelSeedanceV1ProFastTextID,
	basefal.ModelSeedanceV1ProFastImageID,
}

var seedanceAspectRatios = map[string]struct{}{
	"auto": {},
	"21:9": {},
	"16:9": {},
	"4:3":  {},
	"1:1":  {},
	"3:4":  {},
	"9:16": {},
}

var seedanceResolutions = map[string]struct{}{
	"480p":  {},
	"720p":  {},
	"1080p": {},
}

var seedanceFastResolutions = map[string]struct{}{
	"480p": {},
	"720p": {},
}

var seedanceV1ProFastTextAspectRatios = map[string]struct{}{
	"21:9": {},
	"16:9": {},
	"4:3":  {},
	"1:1":  {},
	"3:4":  {},
	"9:16": {},
}

func (a *TaskAdaptor) Init(info *relaycommon.RelayInfo) {
	if info.TaskRelayInfo == nil {
		info.TaskRelayInfo = &relaycommon.TaskRelayInfo{}
	}
	a.ChannelType = info.ChannelType
	a.baseURL = info.ChannelBaseUrl
	a.apiKey = info.ApiKey
}

func (a *TaskAdaptor) ValidateRequestAndSetAction(c *gin.Context, info *relaycommon.RelayInfo) *taskdto.TaskError {
	if err := relaycommon.ValidateBasicTaskRequest(c, info, constant.TaskActionTextGenerate); err != nil {
		return err
	}
	req, err := falTaskRequestWithTopLevelMetadata(c)
	if err != nil {
		return service.TaskErrorWrapper(err, "get_task_request_failed", http.StatusBadRequest)
	}
	route, err := resolveSeedanceRoute(info, req)
	if err != nil {
		return service.TaskErrorWrapperLocal(err, "invalid_request", http.StatusBadRequest)
	}
	info.Action = route.Action
	c.Set("task_request", req)
	return nil
}

func (a *TaskAdaptor) EstimateBilling(c *gin.Context, info *relaycommon.RelayInfo) map[string]float64 {
	req, err := falTaskRequestWithTopLevelMetadata(c)
	if err != nil {
		return nil
	}

	ratios := map[string]float64{}
	if duration, ok := resolveSeedanceDuration(req); ok && duration != "auto" {
		if seconds, err := strconv.Atoi(duration); err == nil && seconds > 0 {
			ratios["seconds"] = float64(seconds)
		}
	}
	if resolution, ok := resolveSeedanceResolution(req); ok {
		switch resolution {
		case "480p":
			ratios["resolution"] = 480.0 / 720.0
		case "1080p":
			ratios["resolution"] = 1080.0 / 720.0
		default:
			ratios["resolution"] = 1
		}
	} else if route, routeErr := resolveSeedanceRoute(info, req); routeErr == nil && route.V1ProFast {
		ratios["resolution"] = 1080.0 / 720.0
	}
	return ratios
}

func (a *TaskAdaptor) BuildRequestURL(info *relaycommon.RelayInfo) (string, error) {
	endpoint := seedanceEndpointForInfo(info)
	if endpoint == "" {
		return "", fmt.Errorf("unsupported fal seedance action: %s", info.Action)
	}
	return fmt.Sprintf("%s/%s", falQueueBaseURL(a.baseURL), endpoint), nil
}

func (a *TaskAdaptor) BuildRequestHeader(c *gin.Context, req *http.Request, info *relaycommon.RelayInfo) error {
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Key "+info.ApiKey)
	return nil
}

func (a *TaskAdaptor) BuildRequestBody(c *gin.Context, info *relaycommon.RelayInfo) (io.Reader, error) {
	req, err := falTaskRequestWithTopLevelMetadata(c)
	if err != nil {
		return nil, err
	}
	route, err := resolveSeedanceRoute(info, req)
	if err != nil {
		return nil, err
	}
	info.Action = route.Action
	info.UpstreamModelName = route.Endpoint
	c.Set("task_request", req)

	body, err := buildSeedanceRequestBody(req, route)
	if err != nil {
		return nil, err
	}
	data, err := common.Marshal(body)
	if err != nil {
		return nil, err
	}
	return bytes.NewReader(data), nil
}

func (a *TaskAdaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	return channel.DoTaskApiRequest(a, c, info, requestBody)
}

func (a *TaskAdaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (taskID string, taskData []byte, taskErr *taskdto.TaskError) {
	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "read_response_body_failed", http.StatusInternalServerError)
	}

	var submit queueSubmitResponse
	if err := common.Unmarshal(responseBody, &submit); err != nil {
		return "", nil, service.TaskErrorWrapper(errors.Wrap(err, string(responseBody)), "unmarshal_response_failed", http.StatusInternalServerError)
	}
	if strings.TrimSpace(submit.RequestID) == "" {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("fal queue response missing request_id: %s", string(responseBody)), "invalid_response", http.StatusInternalServerError)
	}

	endpoint := seedanceEndpointForInfo(info)
	taskData, err = common.Marshal(map[string]any{
		"endpoint": endpoint,
		"action":   info.Action,
		"submit":   submit,
	})
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "marshal_task_data_failed", http.StatusInternalServerError)
	}

	ov := dto.NewOpenAIVideo()
	ov.ID = info.PublicTaskID
	ov.TaskID = info.PublicTaskID
	ov.CreatedAt = time.Now().Unix()
	ov.Model = info.OriginModelName
	ov.SetMetadata("fal_request_id", submit.RequestID)
	c.JSON(http.StatusOK, ov)
	return encodeFalQueueTaskID(endpoint, submit.RequestID), taskData, nil
}

func (a *TaskAdaptor) FetchTask(baseUrl, key string, body map[string]any, proxy string) (*http.Response, error) {
	taskID, ok := body["task_id"].(string)
	if !ok || strings.TrimSpace(taskID) == "" {
		return nil, fmt.Errorf("invalid task_id")
	}
	action, ok := body["action"].(string)
	if !ok || strings.TrimSpace(action) == "" {
		return nil, fmt.Errorf("invalid action")
	}
	endpoint, requestID := decodeFalQueueTaskID(taskID)
	if endpoint == "" {
		endpoint = seedanceEndpointForAction(action)
	}
	if endpoint == "" {
		return nil, fmt.Errorf("unsupported fal seedance action: %s", action)
	}

	queueBase := falQueueBaseURL(baseUrl)
	statusURL := fmt.Sprintf("%s/%s/requests/%s/status?logs=1", queueBase, endpoint, url.PathEscape(requestID))
	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return nil, fmt.Errorf("new proxy http client failed: %w", err)
	}

	statusBody, statusCode, err := falQueueGet(client, statusURL, key)
	if err != nil {
		return nil, err
	}
	statusMap := map[string]any{}
	_ = common.Unmarshal(statusBody, &statusMap)

	if !isFalQueueCompleted(statusMap) || statusCode != http.StatusOK {
		return falSyntheticResponse(statusCode, statusBody), nil
	}

	responseURL, _ := statusMap["response_url"].(string)
	if strings.TrimSpace(responseURL) == "" {
		responseURL = fmt.Sprintf("%s/%s/requests/%s", queueBase, endpoint, url.PathEscape(requestID))
	}
	resultBody, resultStatusCode, err := falQueueGet(client, responseURL, key)
	if err != nil {
		return nil, err
	}
	if resultStatusCode != http.StatusOK {
		return falSyntheticResponse(resultStatusCode, resultBody), nil
	}

	resultMap := map[string]any{}
	_ = common.Unmarshal(resultBody, &resultMap)
	combined := map[string]any{
		"status":     "COMPLETED",
		"request_id": requestID,
		"response":   resultMap,
	}
	if video, ok := resultMap["video"]; ok {
		combined["video"] = video
	}
	if seed, ok := resultMap["seed"]; ok {
		combined["seed"] = seed
	}
	combinedBody, err := common.Marshal(combined)
	if err != nil {
		return nil, err
	}
	return falSyntheticResponse(http.StatusOK, combinedBody), nil
}

func (a *TaskAdaptor) ParseTaskResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	if url := extractFalVideoURL(respBody); url != "" {
		return &relaycommon.TaskInfo{
			Status:   model.TaskStatusSuccess,
			Url:      url,
			Progress: taskcommon.ProgressComplete,
		}, nil
	}

	var raw map[string]any
	if err := common.Unmarshal(respBody, &raw); err != nil {
		return nil, fmt.Errorf("unmarshal fal queue response failed: %w", err)
	}

	if reason := extractFalError(raw); reason != "" {
		return relaycommon.FailTaskInfo(reason), nil
	}

	status := strings.ToUpper(strings.TrimSpace(stringFromAny(raw["status"])))
	taskInfo := &relaycommon.TaskInfo{}
	switch status {
	case "IN_QUEUE":
		taskInfo.Status = model.TaskStatusQueued
		taskInfo.Progress = taskcommon.ProgressQueued
	case "IN_PROGRESS":
		taskInfo.Status = model.TaskStatusInProgress
		taskInfo.Progress = taskcommon.ProgressInProgress
	case "COMPLETED", "SUCCESS", "SUCCEEDED":
		taskInfo.Status = model.TaskStatusSuccess
		taskInfo.Progress = taskcommon.ProgressComplete
	case "FAILED", "FAILURE", "ERROR", "CANCELED", "CANCELLED":
		taskInfo.Status = model.TaskStatusFailure
		taskInfo.Progress = taskcommon.ProgressComplete
		taskInfo.Reason = extractFalError(raw)
		if taskInfo.Reason == "" {
			taskInfo.Reason = "fal task failed"
		}
	default:
		return nil, fmt.Errorf("unknown fal queue status: %s", status)
	}
	return taskInfo, nil
}

func (a *TaskAdaptor) GetModelList() []string {
	return seedanceModelList
}

func (a *TaskAdaptor) GetChannelName() string {
	return basefal.ChannelName
}

func (a *TaskAdaptor) ConvertToOpenAIVideo(originTask *model.Task) ([]byte, error) {
	openAIVideo := dto.NewOpenAIVideo()
	openAIVideo.ID = originTask.TaskID
	openAIVideo.Status = originTask.Status.ToVideoStatus()
	openAIVideo.SetProgressStr(originTask.Progress)
	openAIVideo.CreatedAt = originTask.CreatedAt
	openAIVideo.CompletedAt = originTask.UpdatedAt
	if originTask.Properties.OriginModelName != "" {
		openAIVideo.Model = originTask.Properties.OriginModelName
	}
	if url := extractFalVideoURL(originTask.Data); url != "" {
		openAIVideo.SetMetadata("url", url)
	}
	if originTask.FailReason != "" {
		openAIVideo.Error = &dto.OpenAIVideoError{
			Message: originTask.FailReason,
			Code:    "task_failed",
		}
	}
	return common.Marshal(openAIVideo)
}

func falTaskRequestWithTopLevelMetadata(c *gin.Context) (relaycommon.TaskSubmitReq, error) {
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return relaycommon.TaskSubmitReq{}, err
	}
	if req.Metadata == nil {
		req.Metadata = map[string]any{}
	}
	var top map[string]any
	if err := common.UnmarshalBodyReusable(c, &top); err == nil {
		for _, key := range []string{
			"image_url",
			"end_image_url",
			"image_urls",
			"video_urls",
			"audio_urls",
			"resolution",
			"duration",
			"aspect_ratio",
			"generate_audio",
			"camera_fixed",
			"enable_safety_checker",
			"num_frames",
			"seed",
			"end_user_id",
			"mode",
			"action",
			"endpoint",
		} {
			if value, ok := top[key]; ok {
				if _, exists := req.Metadata[key]; !exists {
					req.Metadata[key] = value
				}
			}
		}
	}
	if req.InputReference != "" && len(req.Images) == 0 {
		req.Images = []string{req.InputReference}
	}
	return req, nil
}

func resolveSeedanceRoute(info *relaycommon.RelayInfo, req relaycommon.TaskSubmitReq) (seedanceRoute, error) {
	origin := firstNonEmpty(info.OriginModelName, req.Model)
	upstream := firstNonEmpty(info.UpstreamModelName, origin)
	if route, ok, err := routeForExplicitSeedanceModel(upstream, origin, req); ok {
		return route, err
	}
	if route, ok, err := routeForExplicitSeedanceModel(origin, origin, req); ok {
		return route, err
	}

	if isSeedanceV1ProFastGeneric(origin) {
		if hasReferenceInput(req) || isExplicitReferenceMode(req) {
			return seedanceRoute{}, fmt.Errorf("%s does not support reference media", basefal.ModelSeedanceV1ProFast)
		}
		if len(collectSeedanceImages(req)) > 1 || seedanceLastFrame(req) != "" {
			return seedanceRoute{}, fmt.Errorf("%s supports only one input image", basefal.ModelSeedanceV1ProFast)
		}
		if len(collectSeedanceImages(req)) > 0 || metadataString(req.Metadata, "image_url") != "" {
			return seedanceRoute{Endpoint: basefal.ModelSeedanceV1ProFastImageID, Action: constant.TaskActionFirstTailGenerate, V1ProFast: true}, nil
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedanceV1ProFastTextID, Action: constant.TaskActionTextGenerate, V1ProFast: true}, nil
	}

	fast := origin == basefal.ModelSeedance20Fast
	if hasReferenceInput(req) || isExplicitReferenceMode(req) {
		return seedanceRoute{Endpoint: seedanceEndpointForRouteAction(constant.TaskActionReferenceGenerate, fast), Action: constant.TaskActionReferenceGenerate, Fast: fast}, nil
	}
	images := collectSeedanceImages(req)
	if len(images) > 0 || metadataString(req.Metadata, "image_url") != "" || seedanceLastFrame(req) != "" {
		return seedanceRoute{Endpoint: seedanceEndpointForRouteAction(constant.TaskActionFirstTailGenerate, fast), Action: constant.TaskActionFirstTailGenerate, Fast: fast}, nil
	}
	return seedanceRoute{Endpoint: seedanceEndpointForRouteAction(constant.TaskActionTextGenerate, fast), Action: constant.TaskActionTextGenerate, Fast: fast}, nil
}

func routeForExplicitSeedanceModel(modelName string, origin string, req relaycommon.TaskSubmitReq) (seedanceRoute, bool, error) {
	switch modelName {
	case basefal.ModelSeedance20, basefal.ModelSeedance20Fast, basefal.ModelSeedanceV1ProFast:
		return seedanceRoute{}, false, nil
	case basefal.ModelSeedance20Text, basefal.ModelSeedance20TextID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		if hasAnyMediaInput(req) {
			return seedanceRoute{}, true, fmt.Errorf("%s does not support media input; use %s or %s", basefal.ModelSeedance20Text, basefal.ModelSeedance20, basefal.ModelSeedance20Ref)
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedance20TextID, Action: constant.TaskActionTextGenerate}, true, nil
	case basefal.ModelSeedance20Image, basefal.ModelSeedance20ImageID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		if hasReferenceInput(req) {
			return seedanceRoute{}, true, fmt.Errorf("%s supports only start/end images; use %s for reference media", basefal.ModelSeedance20Image, basefal.ModelSeedance20Ref)
		}
		if len(collectSeedanceImages(req)) == 0 && metadataString(req.Metadata, "image_url") == "" {
			return seedanceRoute{}, true, fmt.Errorf("%s requires image_url or images", basefal.ModelSeedance20Image)
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedance20ImageID, Action: constant.TaskActionFirstTailGenerate}, true, nil
	case basefal.ModelSeedance20Ref, basefal.ModelSeedance20RefID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedance20RefID, Action: constant.TaskActionReferenceGenerate}, true, nil
	case basefal.ModelSeedance20FastText, basefal.ModelSeedance20FastTextID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		if hasAnyMediaInput(req) {
			return seedanceRoute{}, true, fmt.Errorf("%s does not support media input; use %s or %s", basefal.ModelSeedance20FastText, basefal.ModelSeedance20Fast, basefal.ModelSeedance20FastRef)
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedance20FastTextID, Action: constant.TaskActionTextGenerate, Fast: true}, true, nil
	case basefal.ModelSeedance20FastImage, basefal.ModelSeedance20FastImageID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		if hasReferenceInput(req) {
			return seedanceRoute{}, true, fmt.Errorf("%s supports only start/end images; use %s for reference media", basefal.ModelSeedance20FastImage, basefal.ModelSeedance20FastRef)
		}
		if len(collectSeedanceImages(req)) == 0 && metadataString(req.Metadata, "image_url") == "" {
			return seedanceRoute{}, true, fmt.Errorf("%s requires image_url or images", basefal.ModelSeedance20FastImage)
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedance20FastImageID, Action: constant.TaskActionFirstTailGenerate, Fast: true}, true, nil
	case basefal.ModelSeedance20FastRef, basefal.ModelSeedance20FastRefID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedance20FastRefID, Action: constant.TaskActionReferenceGenerate, Fast: true}, true, nil
	case basefal.ModelSeedanceV1ProFastText, basefal.ModelSeedanceV1ProFastTextID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		if hasAnyMediaInput(req) {
			return seedanceRoute{}, true, fmt.Errorf("%s does not support media input; use %s or %s", basefal.ModelSeedanceV1ProFastText, basefal.ModelSeedanceV1ProFast, basefal.ModelSeedanceV1ProFastImage)
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedanceV1ProFastTextID, Action: constant.TaskActionTextGenerate, V1ProFast: true}, true, nil
	case basefal.ModelSeedanceV1ProFastImage, basefal.ModelSeedanceV1ProFastImageID:
		if isGenericSeedanceModel(origin) {
			return seedanceRoute{}, false, nil
		}
		if hasReferenceInput(req) || isExplicitReferenceMode(req) {
			return seedanceRoute{}, true, fmt.Errorf("%s does not support reference media", basefal.ModelSeedanceV1ProFastImage)
		}
		if len(collectSeedanceImages(req)) > 1 || seedanceLastFrame(req) != "" {
			return seedanceRoute{}, true, fmt.Errorf("%s supports only one input image", basefal.ModelSeedanceV1ProFastImage)
		}
		if len(collectSeedanceImages(req)) == 0 && metadataString(req.Metadata, "image_url") == "" {
			return seedanceRoute{}, true, fmt.Errorf("%s requires image_url or images", basefal.ModelSeedanceV1ProFastImage)
		}
		return seedanceRoute{Endpoint: basefal.ModelSeedanceV1ProFastImageID, Action: constant.TaskActionFirstTailGenerate, V1ProFast: true}, true, nil
	default:
		return seedanceRoute{}, false, nil
	}
}

func buildSeedanceRequestBody(req relaycommon.TaskSubmitReq, route seedanceRoute) (map[string]any, error) {
	body := map[string]any{"prompt": req.Prompt}
	if err := applySeedanceCommonParams(body, req, route); err != nil {
		return nil, err
	}

	switch route.Action {
	case constant.TaskActionTextGenerate:
		if hasAnyMediaInput(req) {
			if route.V1ProFast {
				return nil, fmt.Errorf("%s does not support media input", basefal.ModelSeedanceV1ProFastText)
			}
			return nil, fmt.Errorf("%s does not support media input", basefal.ModelSeedance20Text)
		}
	case constant.TaskActionFirstTailGenerate:
		if route.V1ProFast {
			imageURL := metadataString(req.Metadata, "image_url")
			images := collectSeedanceImages(req)
			if imageURL == "" && len(images) > 0 {
				imageURL = images[0]
			}
			if imageURL == "" {
				return nil, fmt.Errorf("image_url is required for fal seedance v1 pro fast image-to-video")
			}
			if len(images) > 1 || seedanceLastFrame(req) != "" {
				return nil, fmt.Errorf("fal seedance v1 pro fast image-to-video supports only one input image")
			}
			body["image_url"] = imageURL
			return body, nil
		}
		imageURL := metadataString(req.Metadata, "image_url")
		endImageURL := seedanceLastFrame(req)
		images := collectSeedanceImages(req)
		if imageURL == "" && len(images) > 0 {
			imageURL = images[0]
		}
		if endImageURL == "" && len(images) > 1 {
			endImageURL = images[1]
		}
		if imageURL == "" {
			return nil, fmt.Errorf("image_url is required for fal seedance image-to-video")
		}
		if len(images) > 2 {
			return nil, fmt.Errorf("fal seedance image-to-video supports only start and end images; use reference-to-video for additional images")
		}
		body["image_url"] = imageURL
		if endImageURL != "" {
			body["end_image_url"] = endImageURL
		}
	case constant.TaskActionReferenceGenerate:
		imageURLs := seedanceReferenceImages(req)
		if len(imageURLs) == 0 {
			imageURLs = collectSeedanceImages(req)
			if imageURL := metadataString(req.Metadata, "image_url"); imageURL != "" {
				imageURLs = append([]string{imageURL}, imageURLs...)
			}
			if endImageURL := seedanceLastFrame(req); endImageURL != "" {
				imageURLs = append(imageURLs, endImageURL)
			}
		}
		if len(imageURLs) > 0 {
			body["image_urls"] = imageURLs
		}
		if videoURLs := seedanceReferenceVideos(req); len(videoURLs) > 0 {
			body["video_urls"] = videoURLs
		}
		if audioURLs := seedanceReferenceAudios(req); len(audioURLs) > 0 {
			body["audio_urls"] = audioURLs
		}
	default:
		return nil, fmt.Errorf("unsupported fal seedance action: %s", route.Action)
	}
	return body, nil
}

func applySeedanceCommonParams(body map[string]any, req relaycommon.TaskSubmitReq, route seedanceRoute) error {
	if resolution, ok := resolveSeedanceResolution(req); ok {
		supportedResolutions := seedanceResolutions
		supportedText := "480p, 720p, or 1080p"
		if route.Fast && !route.V1ProFast {
			supportedResolutions = seedanceFastResolutions
			supportedText = "480p or 720p"
		}
		if _, supported := supportedResolutions[resolution]; !supported {
			return fmt.Errorf("fal seedance resolution %q is not supported for %s; use %s", resolution, route.Endpoint, supportedText)
		}
		body["resolution"] = resolution
	}
	if duration, ok := resolveSeedanceDuration(req); ok {
		if err := validateSeedanceDuration(duration, route); err != nil {
			return err
		}
		body["duration"] = duration
	}
	if aspectRatio, ok := resolveSeedanceAspectRatio(req); ok {
		supportedRatios := seedanceAspectRatios
		if route.V1ProFast && route.Action == constant.TaskActionTextGenerate {
			supportedRatios = seedanceV1ProFastTextAspectRatios
		}
		if _, supported := supportedRatios[aspectRatio]; !supported {
			return fmt.Errorf("fal seedance aspect_ratio %q is not supported", aspectRatio)
		}
		body["aspect_ratio"] = aspectRatio
	}
	if route.V1ProFast {
		for _, key := range []string{"camera_fixed", "enable_safety_checker", "seed"} {
			if value, ok := req.Metadata[key]; ok {
				body[key] = value
			}
		}
		if value, ok := req.Metadata["num_frames"]; ok {
			numFrames, valid := intFromAny(value)
			if !valid || numFrames < 29 || numFrames > 289 {
				return fmt.Errorf("fal seedance num_frames %q is not supported; use 29-289", stringFromAny(value))
			}
			body["num_frames"] = numFrames
		}
		return nil
	}
	for _, key := range []string{"generate_audio", "seed", "end_user_id"} {
		if value, ok := req.Metadata[key]; ok {
			body[key] = value
		}
	}
	return nil
}

func resolveSeedanceResolution(req relaycommon.TaskSubmitReq) (string, bool) {
	if value := metadataString(req.Metadata, "resolution"); value != "" {
		return strings.ToLower(value), true
	}
	if req.Width > 0 && req.Height > 0 {
		resolution := fmt.Sprintf("%dp", min(req.Width, req.Height))
		if _, ok := seedanceResolutions[resolution]; ok {
			return resolution, true
		}
	}
	if value := strings.TrimSpace(req.Size); value != "" {
		lower := strings.ToLower(value)
		if _, ok := seedanceResolutions[lower]; ok {
			return lower, true
		}
		if resolution, ok := seedanceResolutionFromSize(lower); ok {
			return resolution, true
		}
	}
	return "", false
}

func resolveSeedanceDuration(req relaycommon.TaskSubmitReq) (string, bool) {
	if req.DurationAuto {
		return "auto", true
	}
	if req.Duration > 0 {
		return strconv.Itoa(req.Duration), true
	}
	if value, ok := req.Metadata["duration"]; ok {
		return durationString(value), true
	}
	if strings.TrimSpace(req.Seconds) != "" {
		return strings.TrimSpace(req.Seconds), true
	}
	return "", false
}

func resolveSeedanceAspectRatio(req relaycommon.TaskSubmitReq) (string, bool) {
	if value := firstNonEmpty(metadataString(req.Metadata, "aspect_ratio"), metadataString(req.Metadata, "ratio")); value != "" {
		if strings.EqualFold(value, "adaptive") {
			return "auto", true
		}
		return strings.ToLower(value), true
	}
	if req.Width > 0 && req.Height > 0 {
		if ratio, ok := seedanceAspectRatioFromSize(fmt.Sprintf("%dx%d", req.Width, req.Height)); ok {
			return ratio, true
		}
	}
	if value := strings.TrimSpace(req.Size); value != "" {
		if ratio, ok := seedanceAspectRatioFromSize(value); ok {
			return ratio, true
		}
	}
	return "", false
}

func validateSeedanceDuration(duration string, route seedanceRoute) error {
	duration = strings.TrimSpace(duration)
	if route.V1ProFast {
		seconds, err := strconv.Atoi(duration)
		if err != nil || seconds < 2 || seconds > 12 {
			return fmt.Errorf("fal seedance duration %q is not supported for %s; use 2-12 seconds", duration, route.Endpoint)
		}
		return nil
	}
	if duration == "auto" {
		return nil
	}
	seconds, err := strconv.Atoi(duration)
	if err != nil || seconds < 4 || seconds > 15 {
		return fmt.Errorf("fal seedance duration %q is not supported; use auto or 4-15 seconds", duration)
	}
	return nil
}

func seedanceEndpointForInfo(info *relaycommon.RelayInfo) string {
	if isSeedanceEndpoint(info.UpstreamModelName) {
		return info.UpstreamModelName
	}
	return seedanceEndpointForAction(info.Action)
}

func seedanceEndpointForAction(action string) string {
	return seedanceEndpointForRouteAction(action, false)
}

func seedanceEndpointForRouteAction(action string, fast bool) string {
	switch action {
	case constant.TaskActionTextGenerate:
		if fast {
			return basefal.ModelSeedance20FastTextID
		}
		return basefal.ModelSeedance20TextID
	case constant.TaskActionGenerate, constant.TaskActionFirstTailGenerate:
		if fast {
			return basefal.ModelSeedance20FastImageID
		}
		return basefal.ModelSeedance20ImageID
	case constant.TaskActionReferenceGenerate:
		if fast {
			return basefal.ModelSeedance20FastRefID
		}
		return basefal.ModelSeedance20RefID
	default:
		return ""
	}
}

func isGenericSeedanceModel(modelName string) bool {
	return modelName == basefal.ModelSeedance20 || modelName == basefal.ModelSeedance20Fast || modelName == basefal.ModelSeedanceV1ProFast
}

func isSeedanceV1ProFastGeneric(modelName string) bool {
	return modelName == basefal.ModelSeedanceV1ProFast
}

func isSeedanceEndpoint(modelName string) bool {
	switch modelName {
	case basefal.ModelSeedance20TextID,
		basefal.ModelSeedance20ImageID,
		basefal.ModelSeedance20RefID,
		basefal.ModelSeedance20FastTextID,
		basefal.ModelSeedance20FastImageID,
		basefal.ModelSeedance20FastRefID,
		basefal.ModelSeedanceV1ProFastTextID,
		basefal.ModelSeedanceV1ProFastImageID:
		return true
	default:
		return false
	}
}

func encodeFalQueueTaskID(endpoint string, requestID string) string {
	if endpoint == "" {
		return requestID
	}
	return endpoint + "|" + requestID
}

func decodeFalQueueTaskID(taskID string) (endpoint string, requestID string) {
	parts := strings.SplitN(taskID, "|", 2)
	if len(parts) != 2 {
		return "", taskID
	}
	if !isSeedanceEndpoint(parts[0]) {
		return "", taskID
	}
	return parts[0], parts[1]
}

func falQueueBaseURL(baseURL string) string {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if baseURL == "" {
		return "https://queue.fal.run"
	}
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Host == "" {
		return baseURL
	}
	if parsed.Host == "fal.run" {
		parsed.Host = "queue.fal.run"
	}
	return strings.TrimRight(parsed.String(), "/")
}

func falQueueGet(client *http.Client, requestURL string, key string) ([]byte, int, error) {
	req, err := http.NewRequest(http.MethodGet, requestURL, nil)
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Key "+key)

	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, err
	}
	return body, resp.StatusCode, nil
}

func falSyntheticResponse(statusCode int, body []byte) *http.Response {
	return &http.Response{
		StatusCode: statusCode,
		Status:     fmt.Sprintf("%d %s", statusCode, http.StatusText(statusCode)),
		Header:     make(http.Header),
		Body:       io.NopCloser(bytes.NewReader(body)),
	}
}

func isFalQueueCompleted(raw map[string]any) bool {
	return strings.EqualFold(stringFromAny(raw["status"]), "COMPLETED")
}

func extractFalVideoURL(data []byte) string {
	if len(data) == 0 {
		return ""
	}
	var result falVideoResult
	if err := common.Unmarshal(data, &result); err == nil && result.Video.URL != "" {
		return result.Video.URL
	}
	var raw map[string]any
	if err := common.Unmarshal(data, &raw); err != nil {
		return ""
	}
	if response, ok := raw["response"].(map[string]any); ok {
		if url := extractVideoURLFromMap(response); url != "" {
			return url
		}
	}
	return extractVideoURLFromMap(raw)
}

func extractVideoURLFromMap(raw map[string]any) string {
	video, ok := raw["video"].(map[string]any)
	if !ok {
		return ""
	}
	return stringFromAny(video["url"])
}

func extractFalError(raw map[string]any) string {
	for _, key := range []string{"error", "detail", "message"} {
		if value, ok := raw[key]; ok {
			if text := stringFromAny(value); text != "" {
				return text
			}
			if obj, ok := value.(map[string]any); ok {
				if text := stringFromAny(obj["message"]); text != "" {
					return text
				}
			}
		}
	}
	return ""
}

func hasAnyMediaInput(req relaycommon.TaskSubmitReq) bool {
	return len(collectSeedanceImages(req)) > 0 ||
		metadataString(req.Metadata, "image_url") != "" ||
		seedanceLastFrame(req) != "" ||
		len(seedanceReferenceImages(req)) > 0 ||
		hasReferenceInput(req)
}

func hasReferenceInput(req relaycommon.TaskSubmitReq) bool {
	return len(seedanceReferenceImages(req)) > 0 ||
		len(seedanceReferenceVideos(req)) > 0 ||
		len(seedanceReferenceAudios(req)) > 0 ||
		len(collectSeedanceImages(req)) > 2
}

func seedanceLastFrame(req relaycommon.TaskSubmitReq) string {
	return firstNonEmpty(
		metadataString(req.Metadata, "last_frame_image"),
		metadataString(req.Metadata, "end_image_url"),
	)
}

func seedanceReferenceImages(req relaycommon.TaskSubmitReq) []string {
	if values := metadataStrings(req.Metadata, "reference_images"); len(values) > 0 {
		return values
	}
	return metadataStrings(req.Metadata, "image_urls")
}

func seedanceReferenceVideos(req relaycommon.TaskSubmitReq) []string {
	if values := metadataStrings(req.Metadata, "reference_videos"); len(values) > 0 {
		return values
	}
	return metadataStrings(req.Metadata, "video_urls")
}

func seedanceReferenceAudios(req relaycommon.TaskSubmitReq) []string {
	if values := metadataStrings(req.Metadata, "reference_audios"); len(values) > 0 {
		return values
	}
	return metadataStrings(req.Metadata, "audio_urls")
}

func isExplicitReferenceMode(req relaycommon.TaskSubmitReq) bool {
	values := []string{
		req.Mode,
		metadataString(req.Metadata, "mode"),
		metadataString(req.Metadata, "action"),
		metadataString(req.Metadata, "endpoint"),
	}
	for _, value := range values {
		value = strings.ToLower(strings.TrimSpace(value))
		if strings.Contains(value, "reference") || value == constant.TaskActionReferenceGenerate {
			return true
		}
	}
	return false
}

func collectSeedanceImages(req relaycommon.TaskSubmitReq) []string {
	images := make([]string, 0, len(req.Images)+1)
	if req.Image != "" {
		images = append(images, strings.TrimSpace(req.Image))
	}
	for _, image := range req.Images {
		if image = strings.TrimSpace(image); image != "" {
			images = append(images, image)
		}
	}
	if req.InputReference != "" {
		images = append(images, strings.TrimSpace(req.InputReference))
	}
	return dedupeStrings(images)
}

func metadataString(metadata map[string]any, key string) string {
	if metadata == nil {
		return ""
	}
	value, ok := metadata[key]
	if !ok {
		return ""
	}
	return strings.TrimSpace(stringFromAny(value))
}

func metadataStrings(metadata map[string]any, key string) []string {
	if metadata == nil {
		return nil
	}
	value, ok := metadata[key]
	if !ok {
		return nil
	}
	switch v := value.(type) {
	case []string:
		return dedupeStrings(v)
	case []any:
		values := make([]string, 0, len(v))
		for _, item := range v {
			if text := strings.TrimSpace(stringFromAny(item)); text != "" {
				values = append(values, text)
			}
		}
		return dedupeStrings(values)
	case string:
		if strings.TrimSpace(v) == "" {
			return nil
		}
		if strings.Contains(v, ",") {
			return dedupeStrings(strings.Split(v, ","))
		}
		return []string{strings.TrimSpace(v)}
	default:
		text := strings.TrimSpace(stringFromAny(v))
		if text == "" {
			return nil
		}
		return []string{text}
	}
}

func durationString(value any) string {
	switch v := value.(type) {
	case string:
		return strings.TrimSpace(v)
	case int:
		return strconv.Itoa(v)
	case int64:
		return strconv.FormatInt(v, 10)
	case float64:
		return strconv.Itoa(int(v))
	default:
		return strings.TrimSpace(fmt.Sprintf("%v", v))
	}
}

func intFromAny(value any) (int, bool) {
	switch v := value.(type) {
	case int:
		return v, true
	case int64:
		return int(v), true
	case float64:
		if v != float64(int(v)) {
			return 0, false
		}
		return int(v), true
	case string:
		parsed, err := strconv.Atoi(strings.TrimSpace(v))
		return parsed, err == nil
	default:
		return 0, false
	}
}

func seedanceAspectRatioFromSize(size string) (string, bool) {
	width, height, ok := parseSize(size)
	if !ok || width == 0 || height == 0 {
		return "", false
	}
	ratio := float64(width) / float64(height)
	candidates := map[string]float64{
		"21:9": 21.0 / 9.0,
		"16:9": 16.0 / 9.0,
		"4:3":  4.0 / 3.0,
		"1:1":  1,
		"3:4":  3.0 / 4.0,
		"9:16": 9.0 / 16.0,
	}
	best := ""
	bestDelta := 999.0
	for name, candidate := range candidates {
		delta := ratio - candidate
		if delta < 0 {
			delta = -delta
		}
		if delta < bestDelta {
			best = name
			bestDelta = delta
		}
	}
	if bestDelta <= 0.04 {
		return best, true
	}
	return "", false
}

func seedanceResolutionFromSize(size string) (string, bool) {
	width, height, ok := parseSize(size)
	if !ok {
		return "", false
	}
	shortEdge := width
	if height < shortEdge {
		shortEdge = height
	}
	switch {
	case shortEdge <= 540:
		return "480p", true
	case shortEdge <= 900:
		return "720p", true
	default:
		return "1080p", true
	}
}

func parseSize(size string) (int, int, bool) {
	parts := strings.Split(strings.ToLower(strings.TrimSpace(size)), "x")
	if len(parts) != 2 {
		return 0, 0, false
	}
	width, errW := strconv.Atoi(strings.TrimSpace(parts[0]))
	height, errH := strconv.Atoi(strings.TrimSpace(parts[1]))
	return width, height, errW == nil && errH == nil && width > 0 && height > 0
}

func stringFromAny(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case fmt.Stringer:
		return v.String()
	case nil:
		return ""
	default:
		return fmt.Sprintf("%v", v)
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func dedupeStrings(values []string) []string {
	seen := map[string]struct{}{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	return result
}
