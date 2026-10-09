package comfyui

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel"
	"github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"

	"github.com/gin-gonic/gin"
	"github.com/pkg/errors"
)

const ChannelName = "comfyui"

var ModelList = []string{
	"comfyui-video",
}

type TaskAdaptor struct {
	taskcommon.BaseBilling
	ChannelType int
	baseURL     string
	apiKey      string
	settings    dto.ComfyUISettings
	uploadInput func(*gin.Context, *relaycommon.RelayInfo, string, string) (string, error)
}

var _ channel.TaskAdaptor = (*TaskAdaptor)(nil)
var _ channel.OpenAIVideoConverter = (*TaskAdaptor)(nil)
var _ channel.TaskCanceller = (*TaskAdaptor)(nil)

type promptResponse struct {
	PromptID   string         `json:"prompt_id"`
	Number     int            `json:"number,omitempty"`
	NodeErrors map[string]any `json:"node_errors,omitempty"`
	Error      any            `json:"error,omitempty"`
}

type wrappedHistoryResponse struct {
	PromptID string `json:"prompt_id"`
	BaseURL  string `json:"base_url"`
	History  any    `json:"history,omitempty"`
	Status   string `json:"status,omitempty"`
	Reason   string `json:"reason,omitempty"`
	URL      string `json:"url,omitempty"`
}

type uploadImageResponse struct {
	Name      string `json:"name"`
	Subfolder string `json:"subfolder"`
	Type      string `json:"type"`
}

type comfyMetadata struct {
	Workflow        any      `json:"workflow,omitempty"`
	NegativePrompt  string   `json:"negative_prompt,omitempty"`
	LastFrameImage  string   `json:"last_frame_image,omitempty"`
	ReferenceImages []string `json:"reference_images,omitempty"`
	ReferenceVideos []string `json:"reference_videos,omitempty"`
	ReferenceAudios []string `json:"reference_audios,omitempty"`
	Width           int      `json:"width,omitempty"`
	Height          int      `json:"height,omitempty"`
	Resolution      string   `json:"resolution,omitempty"`
	Ratio           string   `json:"ratio,omitempty"`
	AspectRatio     string   `json:"aspect_ratio,omitempty"`
	Duration        int      `json:"duration,omitempty"`
	Frames          int      `json:"frames,omitempty"`
	FPS             int      `json:"fps,omitempty"`
	Seed            int      `json:"seed,omitempty"`
}

func (a *TaskAdaptor) Init(info *relaycommon.RelayInfo) {
	a.ChannelType = info.ChannelType
	a.baseURL = strings.TrimRight(info.ChannelBaseUrl, "/")
	a.apiKey = info.ApiKey
	if info.ChannelOtherSettings.ComfyUI != nil {
		a.settings = *info.ChannelOtherSettings.ComfyUI
	}
}

func (a *TaskAdaptor) ValidateRequestAndSetAction(c *gin.Context, info *relaycommon.RelayInfo) *taskdto.TaskError {
	return relaycommon.ValidateBasicTaskRequest(c, info, constant.TaskActionGenerate)
}

func (a *TaskAdaptor) BuildRequestURL(_ *relaycommon.RelayInfo) (string, error) {
	return a.baseURL + "/prompt", nil
}

func (a *TaskAdaptor) BuildRequestHeader(_ *gin.Context, req *http.Request, _ *relaycommon.RelayInfo) error {
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	if strings.TrimSpace(a.apiKey) != "" {
		req.Header.Set("Authorization", "Bearer "+a.apiKey)
		req.Header.Set("X-API-Key", a.apiKey)
	}
	return nil
}

func (a *TaskAdaptor) BuildRequestBody(c *gin.Context, info *relaycommon.RelayInfo) (io.Reader, error) {
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return nil, err
	}
	metadata := comfyMetadata{}
	if err := req.UnmarshalMetadata(&metadata); err != nil {
		return nil, err
	}
	req, metadata = mergeTaskContentMedia(req, metadata)
	modelName := firstNonEmpty(info.UpstreamModelName, req.Model)
	workflow, routeMapping, err := a.workflowForRequest(req, metadata, modelName)
	if err != nil {
		return nil, err
	}
	mapping := mergeNodeMappings(a.nodeMappingForModel(modelName, workflow), routeMapping)
	if err := a.applyRequestToWorkflow(c, workflow, req, metadata, mapping, info); err != nil {
		return nil, err
	}
	clientID := strings.TrimSpace(a.settings.ClientID)
	if clientID == "" {
		clientID = "relayclaw-comfyui"
	}
	body := map[string]any{
		"prompt":    workflow,
		"client_id": clientID,
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
		taskErr = service.TaskErrorWrapper(err, "read_response_body_failed", http.StatusInternalServerError)
		return
	}
	_ = resp.Body.Close()

	var submit promptResponse
	if err := common.Unmarshal(responseBody, &submit); err != nil {
		taskErr = service.TaskErrorWrapper(errors.Wrapf(err, "body: %s", responseBody), "unmarshal_response_body_failed", http.StatusInternalServerError)
		return
	}
	if submit.PromptID == "" || len(submit.NodeErrors) > 0 || submit.Error != nil {
		taskErr = service.TaskErrorWrapper(fmt.Errorf("ComfyUI submit failed: %s", string(responseBody)), "comfyui_submit_failed", http.StatusBadGateway)
		return
	}

	ov := dto.NewOpenAIVideo()
	ov.ID = info.PublicTaskID
	ov.TaskID = info.PublicTaskID
	ov.CreatedAt = time.Now().Unix()
	ov.Model = info.OriginModelName
	c.JSON(http.StatusOK, ov)
	return submit.PromptID, responseBody, nil
}

func (a *TaskAdaptor) FetchTask(baseUrl, key string, body map[string]any, proxy string) (*http.Response, error) {
	taskID, ok := body["task_id"].(string)
	if !ok || strings.TrimSpace(taskID) == "" {
		return nil, fmt.Errorf("invalid task_id")
	}
	baseUrl = strings.TrimRight(baseUrl, "/")
	req, err := http.NewRequest(http.MethodGet, fmt.Sprintf("%s/history/%s", baseUrl, url.PathEscape(taskID)), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	if strings.TrimSpace(key) != "" {
		req.Header.Set("Authorization", "Bearer "+key)
		req.Header.Set("X-API-Key", key)
	}
	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return nil, fmt.Errorf("new proxy http client failed: %w", err)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= http.StatusBadRequest {
		wrapped := wrapHistoryFailure(
			taskID,
			baseUrl,
			fmt.Sprintf("ComfyUI history failed: HTTP %d - %s", resp.StatusCode, string(responseBody)),
		)
		data, err := common.Marshal(wrapped)
		if err != nil {
			return nil, err
		}
		return &http.Response{
			StatusCode: resp.StatusCode,
			Header:     resp.Header,
			Body:       io.NopCloser(bytes.NewReader(data)),
		}, nil
	}
	wrapped := wrapHistoryResponse(taskID, baseUrl, responseBody)
	data, err := common.Marshal(wrapped)
	if err != nil {
		return nil, err
	}
	return &http.Response{
		StatusCode: resp.StatusCode,
		Header:     resp.Header,
		Body:       io.NopCloser(bytes.NewReader(data)),
	}, nil
}

func (a *TaskAdaptor) CancelTask(ctx context.Context, baseURL, key, upstreamTaskID, proxy string) error {
	baseURL = strings.TrimRight(baseURL, "/")
	upstreamTaskID = strings.TrimSpace(upstreamTaskID)
	if baseURL == "" || upstreamTaskID == "" {
		return channel.ErrTaskNotCancellable
	}
	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return fmt.Errorf("new proxy http client failed: %w", err)
	}
	queueReq, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+"/queue", nil)
	if err != nil {
		return err
	}
	setComfyUIAuthHeaders(queueReq, key)
	queueResp, err := client.Do(queueReq)
	if err != nil {
		return err
	}
	queueBody, readErr := io.ReadAll(queueResp.Body)
	_ = queueResp.Body.Close()
	if readErr != nil {
		return readErr
	}
	if queueResp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("ComfyUI queue query failed: HTTP %d", queueResp.StatusCode)
	}
	location, err := comfyUIQueueTaskLocation(queueBody, upstreamTaskID)
	if err != nil {
		return err
	}
	if location == "running" {
		return channel.ErrTaskCancellationUnsupported
	}
	if location != "pending" {
		return channel.ErrTaskNotCancellable
	}

	payload, err := common.Marshal(map[string]any{"delete": []string{upstreamTaskID}})
	if err != nil {
		return err
	}
	deleteReq, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/queue", bytes.NewReader(payload))
	if err != nil {
		return err
	}
	deleteReq.Header.Set("Content-Type", "application/json")
	setComfyUIAuthHeaders(deleteReq, key)
	deleteResp, err := client.Do(deleteReq)
	if err != nil {
		return err
	}
	defer deleteResp.Body.Close()
	if deleteResp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("ComfyUI queue cancellation failed: HTTP %d", deleteResp.StatusCode)
	}
	location, err = queryComfyUIQueueTaskLocation(ctx, client, baseURL, key, upstreamTaskID)
	if err != nil {
		return err
	}
	if location == "pending" {
		return fmt.Errorf("ComfyUI queue still contains task after cancellation")
	}
	if location == "running" {
		return channel.ErrTaskCancellationUnsupported
	}
	completed, err := comfyUIHistoryContainsTask(ctx, client, baseURL, key, upstreamTaskID)
	if err != nil {
		return err
	}
	if completed {
		return channel.ErrTaskNotCancellable
	}
	return nil
}

func queryComfyUIQueueTaskLocation(ctx context.Context, client *http.Client, baseURL, key, taskID string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+"/queue", nil)
	if err != nil {
		return "", err
	}
	setComfyUIAuthHeaders(req, key)
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	body, readErr := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	if readErr != nil {
		return "", readErr
	}
	if resp.StatusCode >= http.StatusBadRequest {
		return "", fmt.Errorf("ComfyUI queue query failed: HTTP %d", resp.StatusCode)
	}
	return comfyUIQueueTaskLocation(body, taskID)
}

func comfyUIHistoryContainsTask(ctx context.Context, client *http.Client, baseURL, key, taskID string) (bool, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+"/history/"+url.PathEscape(taskID), nil)
	if err != nil {
		return false, err
	}
	setComfyUIAuthHeaders(req, key)
	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	body, readErr := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	if readErr != nil {
		return false, readErr
	}
	if resp.StatusCode == http.StatusNotFound {
		return false, nil
	}
	if resp.StatusCode >= http.StatusBadRequest {
		return false, fmt.Errorf("ComfyUI history query failed: HTTP %d", resp.StatusCode)
	}
	history := map[string]any{}
	if err := common.Unmarshal(body, &history); err != nil {
		return false, err
	}
	_, exists := history[taskID]
	return exists, nil
}

func setComfyUIAuthHeaders(req *http.Request, key string) {
	req.Header.Set("Accept", "application/json")
	if key = strings.TrimSpace(key); key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
		req.Header.Set("X-API-Key", key)
	}
}

func comfyUIQueueTaskLocation(body []byte, taskID string) (string, error) {
	queue := map[string]any{}
	if err := common.Unmarshal(body, &queue); err != nil {
		return "", fmt.Errorf("decode ComfyUI queue: %w", err)
	}
	for _, key := range []string{"queue_pending", "pending"} {
		if containsComfyUIQueueTask(queue[key], taskID) {
			return "pending", nil
		}
	}
	for _, key := range []string{"queue_running", "running"} {
		if containsComfyUIQueueTask(queue[key], taskID) {
			return "running", nil
		}
	}
	return "", nil
}

func containsComfyUIQueueTask(value any, taskID string) bool {
	switch typed := value.(type) {
	case []any:
		for _, item := range typed {
			if containsComfyUIQueueTask(item, taskID) {
				return true
			}
		}
	case map[string]any:
		for key, item := range typed {
			if (key == "prompt_id" || key == "id") && strings.TrimSpace(fmt.Sprint(item)) == taskID {
				return true
			}
			if containsComfyUIQueueTask(item, taskID) {
				return true
			}
		}
	case string:
		return strings.TrimSpace(typed) == taskID
	}
	return false
}

func (a *TaskAdaptor) GetModelList() []string {
	return ModelList
}

func (a *TaskAdaptor) GetChannelName() string {
	return ChannelName
}

func (a *TaskAdaptor) GetCapabilities() []string {
	return []string{"video"}
}

func (a *TaskAdaptor) OverridesSyncCapabilities() bool {
	return true
}

func (a *TaskAdaptor) ParseTaskResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	wrapped := wrappedHistoryResponse{}
	if err := common.Unmarshal(respBody, &wrapped); err != nil {
		return nil, errors.Wrap(err, "unmarshal comfyui task result failed")
	}
	taskResult := relaycommon.TaskInfo{
		TaskID: wrapped.PromptID,
		Code:   0,
	}
	switch wrapped.Status {
	case "succeeded":
		taskResult.Status = model.TaskStatusSuccess
		taskResult.Progress = "100%"
		taskResult.RemoteUrl = wrapped.URL
	case "failed":
		taskResult.Status = model.TaskStatusFailure
		taskResult.Progress = "100%"
		taskResult.Reason = firstNonEmpty(wrapped.Reason, "ComfyUI task failed")
	default:
		taskResult.Status = model.TaskStatusInProgress
		taskResult.Progress = "30%"
	}
	return &taskResult, nil
}

func (a *TaskAdaptor) ConvertToOpenAIVideo(originTask *model.Task) ([]byte, error) {
	var wrapped wrappedHistoryResponse
	_ = common.Unmarshal(originTask.Data, &wrapped)
	openAIVideo := dto.NewOpenAIVideo()
	openAIVideo.ID = originTask.TaskID
	openAIVideo.TaskID = originTask.TaskID
	openAIVideo.Status = originTask.Status.ToVideoStatus()
	openAIVideo.SetProgressStr(originTask.Progress)
	openAIVideo.CreatedAt = originTask.CreatedAt
	openAIVideo.CompletedAt = originTask.UpdatedAt
	openAIVideo.Model = originTask.Properties.OriginModelName
	resultURL := firstNonEmpty(originTask.GetResultURL(), wrapped.URL)
	if originTask.PrivateData.UpstreamResultURL != "" {
		resultURL = taskcommon.BuildPublicProxyURL(originTask.TaskID)
	}
	if resultURL != "" {
		openAIVideo.SetMetadata("url", resultURL)
		openAIVideo.SetMetadata("video_url", resultURL)
	}
	if wrapped.PromptID != "" {
		openAIVideo.SetMetadata("provider_task_id", wrapped.PromptID)
		openAIVideo.SetMetadata("comfy_prompt_id", wrapped.PromptID)
	}
	if originTask.Status == model.TaskStatusFailure {
		openAIVideo.Error = &dto.OpenAIVideoError{
			Message: firstNonEmpty(originTask.FailReason, wrapped.Reason),
			Code:    "comfyui_task_failed",
		}
	}
	return common.Marshal(openAIVideo)
}

func (a *TaskAdaptor) workflowForRequest(req relaycommon.TaskSubmitReq, metadata comfyMetadata, modelName string) (map[string]any, dto.ComfyUINodeMappings, error) {
	if metadata.Workflow != nil {
		return nil, dto.ComfyUINodeMappings{}, fmt.Errorf("comfyui workflow cannot be supplied in request metadata")
	}
	var raw any
	routeMapping := dto.ComfyUINodeMappings{}
	if len(a.settings.WorkflowRoutes) > 0 {
		route, err := selectWorkflowRoute(a.settings.WorkflowRoutes, req, metadata, modelName)
		if err != nil {
			return nil, routeMapping, err
		}
		raw = route.Workflow
		routeMapping = route.NodeMappings
	}
	if a.settings.WorkflowByModel != nil {
		if raw == nil {
			raw = a.settings.WorkflowByModel[firstNonEmpty(modelName, req.Model)]
		}
	}
	if raw == nil {
		raw = a.settings.Workflow
	}
	if raw == nil {
		return nil, routeMapping, comfyUIConfigurationError(
			"comfyui workflow is required in channel settings",
			nil,
		)
	}
	var workflow map[string]any
	data, err := common.Marshal(raw)
	if err != nil {
		return nil, routeMapping, err
	}
	if err := common.Unmarshal(data, &workflow); err != nil {
		var workflowText string
		if err2 := common.Unmarshal(data, &workflowText); err2 == nil && workflowText != "" {
			if err3 := common.Unmarshal([]byte(workflowText), &workflow); err3 == nil {
				return workflow, routeMapping, nil
			}
		}
		return nil, routeMapping, err
	}
	return workflow, routeMapping, nil
}

type comfyUIRouteRequest struct {
	model, mode, resolution, ratio   string
	duration, images, videos, audios int
}

func selectWorkflowRoute(routes []dto.ComfyUIWorkflowRoute, req relaycommon.TaskSubmitReq, metadata comfyMetadata, modelName string) (dto.ComfyUIWorkflowRoute, error) {
	request, err := classifyWorkflowRouteRequest(req, metadata, modelName)
	if err != nil {
		return dto.ComfyUIWorkflowRoute{}, err
	}
	matches := make([]dto.ComfyUIWorkflowRoute, 0)
	for _, configuredRoute := range routes {
		route, err := normalizeWorkflowRoute(configuredRoute)
		if err != nil {
			return dto.ComfyUIWorkflowRoute{}, err
		}
		if workflowRouteMatches(route.Match, request) {
			matches = append(matches, route)
		}
	}
	if len(matches) == 0 {
		return dto.ComfyUIWorkflowRoute{}, comfyUIRequestError(
			fmt.Sprintf("no comfyui workflow route matches model %q mode %q", request.model, request.mode),
			map[string]any{"model": request.model, "mode": request.mode},
		)
	}
	sort.SliceStable(matches, func(i, j int) bool { return matches[i].Priority > matches[j].Priority })
	if len(matches) > 1 && matches[0].Priority == matches[1].Priority {
		return dto.ComfyUIWorkflowRoute{}, comfyUIConfigurationError(
			fmt.Sprintf("multiple comfyui workflow routes match at priority %d: %q and %q", matches[0].Priority, matches[0].ID, matches[1].ID),
			map[string]any{"priority": matches[0].Priority, "route_ids": []string{matches[0].ID, matches[1].ID}},
		)
	}
	if matches[0].Workflow == nil {
		return dto.ComfyUIWorkflowRoute{}, comfyUIConfigurationError(
			fmt.Sprintf("comfyui workflow route %q has no workflow", matches[0].ID),
			map[string]any{"route_id": matches[0].ID},
		)
	}
	return matches[0], nil
}

func normalizeWorkflowRoute(route dto.ComfyUIWorkflowRoute) (dto.ComfyUIWorkflowRoute, error) {
	if len(route.Match.Modes) > 0 {
		return route, nil
	}
	mode, err := inferMiniMaxH3WorkflowMode(route.Workflow)
	if err != nil {
		return route, fmt.Errorf("comfyui workflow route %q: %w", route.ID, err)
	}
	route.Match.Modes = []string{mode}
	if route.Match.MinDuration == 0 {
		route.Match.MinDuration = 4
	}
	if route.Match.MaxDuration == 0 {
		route.Match.MaxDuration = 15
	}
	zero, one, three, nine := 0, 1, 3, 9
	switch mode {
	case "text_to_video":
		setDefaultCountRange(&route.Match.ReferenceImages, &zero)
		setDefaultCountRange(&route.Match.ReferenceVideos, &zero)
		setDefaultCountRange(&route.Match.ReferenceAudios, &zero)
	case "image_to_video":
		setDefaultCountRange(&route.Match.ReferenceImages, &one)
		setDefaultCountRange(&route.Match.ReferenceVideos, &zero)
		setDefaultCountRange(&route.Match.ReferenceAudios, &zero)
	case "reference_to_video":
		setDefaultCountRange(&route.Match.ReferenceImages, &nine)
		setDefaultCountRange(&route.Match.ReferenceVideos, &three)
		setDefaultCountRange(&route.Match.ReferenceAudios, &three)
	}
	return route, nil
}

func setDefaultCountRange(limit *dto.ComfyUICountRange, maxValue *int) {
	if limit.Min == nil && limit.Max == nil {
		limit.Max = maxValue
	}
}

func inferMiniMaxH3WorkflowMode(raw any) (string, error) {
	workflow, err := decodeWorkflow(raw)
	if err != nil {
		return "", err
	}
	hasH3ImageToVideo := false
	hasLoadImage := false
	for _, rawNode := range workflow {
		node, ok := rawNode.(map[string]any)
		if !ok {
			continue
		}
		classType := strings.ToLower(strings.TrimSpace(fmt.Sprint(node["class_type"])))
		switch {
		case strings.Contains(classType, "minimaxh3referencetovideo") || strings.Contains(classType, "minimax_h3_reference_to_video"):
			return "reference_to_video", nil
		case strings.Contains(classType, "minimaxh3imagetovideo") || strings.Contains(classType, "minimax_h3_image_to_video"):
			hasH3ImageToVideo = true
		case strings.Contains(classType, "loadimage"):
			hasLoadImage = true
		}
	}
	if hasH3ImageToVideo && hasLoadImage {
		return "image_to_video", nil
	}
	if hasH3ImageToVideo {
		return "text_to_video", nil
	}
	return "", fmt.Errorf("generation mode cannot be inferred from workflow")
}

func decodeWorkflow(raw any) (map[string]any, error) {
	data, err := common.Marshal(raw)
	if err != nil {
		return nil, err
	}
	var workflow map[string]any
	if err := common.Unmarshal(data, &workflow); err == nil {
		return workflow, nil
	}
	var workflowText string
	if err := common.Unmarshal(data, &workflowText); err != nil || strings.TrimSpace(workflowText) == "" {
		return nil, fmt.Errorf("workflow must be a JSON object")
	}
	if err := common.Unmarshal([]byte(workflowText), &workflow); err != nil {
		return nil, err
	}
	return workflow, nil
}

func classifyWorkflowRouteRequest(req relaycommon.TaskSubmitReq, metadata comfyMetadata, modelName string) (comfyUIRouteRequest, error) {
	images := len(nonEmptyReferences(append(append([]string{req.Image}, req.AdditionalReferenceImages()...), metadata.ReferenceImages...)))
	videos := len(nonEmptyStrings(metadata.ReferenceVideos))
	audios := len(nonEmptyStrings(metadata.ReferenceAudios))
	if strings.TrimSpace(metadata.LastFrameImage) != "" {
		images++
	}
	if metadata.LastFrameImage != "" && (videos > 0 || audios > 0) {
		return comfyUIRouteRequest{}, fmt.Errorf("comfyui first/last-frame inputs cannot be mixed with reference video or audio")
	}
	mode := ""
	if req.DurationAuto && strings.EqualFold(firstNonEmpty(metadata.Ratio, metadata.AspectRatio), "auto") && videos > 0 {
		mode = "video_edit"
	} else if videos > 0 || audios > 0 {
		mode = "reference_to_video"
	} else if images > 0 || metadata.LastFrameImage != "" {
		mode = "image_to_video"
	} else {
		mode = "text_to_video"
	}
	return comfyUIRouteRequest{
		model: firstNonEmpty(modelName, req.Model), mode: mode,
		resolution: firstNonEmpty(metadata.Resolution, req.Size), ratio: firstNonEmpty(metadata.Ratio, metadata.AspectRatio),
		duration: firstNonZero(req.Duration, metadata.Duration), images: images, videos: videos, audios: audios,
	}, nil
}

func mergeTaskContentMedia(req relaycommon.TaskSubmitReq, metadata comfyMetadata) (relaycommon.TaskSubmitReq, comfyMetadata) {
	for _, item := range req.Content {
		role := strings.ToLower(strings.TrimSpace(item.Role))
		switch strings.ToLower(strings.TrimSpace(item.Type)) {
		case "image_url":
			mediaURL := taskContentMediaURL(item.ImageURL)
			if mediaURL == "" {
				continue
			}
			switch role {
			case "reference_image":
				metadata.ReferenceImages = appendUniqueString(metadata.ReferenceImages, mediaURL)
			case "last_frame":
				metadata.LastFrameImage = firstNonEmpty(metadata.LastFrameImage, mediaURL)
			default:
				req.Image = firstNonEmpty(req.Image, mediaURL)
			}
		case "video_url":
			metadata.ReferenceVideos = appendUniqueString(metadata.ReferenceVideos, taskContentMediaURL(item.VideoURL))
		case "audio_url":
			metadata.ReferenceAudios = appendUniqueString(metadata.ReferenceAudios, taskContentMediaURL(item.AudioURL))
		}
	}
	return req, metadata
}

func taskContentMediaURL(value any) string {
	switch typed := value.(type) {
	case string:
		return strings.TrimSpace(typed)
	case map[string]any:
		urlValue, ok := typed["url"]
		if !ok || urlValue == nil {
			return ""
		}
		return strings.TrimSpace(fmt.Sprint(urlValue))
	default:
		return ""
	}
}

func appendUniqueString(values []string, value string) []string {
	value = strings.TrimSpace(value)
	if value == "" {
		return values
	}
	for _, existing := range values {
		if strings.TrimSpace(existing) == value {
			return values
		}
	}
	return append(values, value)
}

func normalizeWorkflowMode(mode string) string {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "text_to_video", "t2v":
		return "text_to_video"
	case "image_to_video", "image_reference", "i2v":
		return "image_to_video"
	case "reference_to_video", "all_reference", "r2v":
		return "reference_to_video"
	case "video_edit":
		return "video_edit"
	default:
		return ""
	}
}

func workflowRouteMatches(match dto.ComfyUIWorkflowMatch, req comfyUIRouteRequest) bool {
	return stringMatches(match.Models, req.model) && stringMatches(match.Modes, req.mode) &&
		stringMatches(match.Resolutions, req.resolution) && stringMatches(match.Ratios, req.ratio) &&
		durationMatches(match, req.duration) &&
		countMatches(match.ReferenceImages, req.images) && countMatches(match.ReferenceVideos, req.videos) && countMatches(match.ReferenceAudios, req.audios)
}

func durationMatches(match dto.ComfyUIWorkflowMatch, duration int) bool {
	if duration == 0 {
		return true
	}
	return (match.MinDuration == 0 || duration >= match.MinDuration) &&
		(match.MaxDuration == 0 || duration <= match.MaxDuration)
}

func stringMatches(allowed []string, actual string) bool {
	if len(allowed) == 0 {
		return true
	}
	for _, value := range allowed {
		if strings.EqualFold(strings.TrimSpace(value), strings.TrimSpace(actual)) {
			return true
		}
	}
	return false
}

func countMatches(limit dto.ComfyUICountRange, actual int) bool {
	return (limit.Min == nil || actual >= *limit.Min) && (limit.Max == nil || actual <= *limit.Max)
}

func nonEmptyStrings(values []string) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			result = append(result, value)
		}
	}
	return result
}

func (a *TaskAdaptor) nodeMappingForModel(modelName string, workflow map[string]any) dto.ComfyUINodeMappings {
	mapping := mergeNodeMappings(inferNodeMappings(workflow), a.settings.NodeMappings)
	if a.settings.ModelMappings != nil {
		if modelMapping, ok := a.settings.ModelMappings[modelName]; ok {
			mapping = mergeNodeMappings(mapping, modelMapping)
		}
	}
	return mapping
}

func inferNodeMappings(workflow map[string]any) dto.ComfyUINodeMappings {
	mapping := dto.ComfyUINodeMappings{}
	nodeIDs := make([]string, 0, len(workflow))
	for nodeID := range workflow {
		nodeIDs = append(nodeIDs, nodeID)
	}
	sort.Strings(nodeIDs)
	mapping.PromptNodeID, mapping.PromptInput = findWorkflowInput(workflow, nodeIDs, nil, "prompt", "text")
	mapping.ImageNodeID, mapping.ImageInput = findWorkflowInput(workflow, nodeIDs, func(node map[string]any) bool {
		return strings.Contains(strings.ToLower(fmt.Sprint(node["class_type"])), "loadimage")
	}, "image")
	mapping.WidthNodeID, mapping.WidthInput = findWorkflowInput(workflow, nodeIDs, nil, "width")
	mapping.HeightNodeID, mapping.HeightInput = findWorkflowInput(workflow, nodeIDs, nil, "height")
	mapping.DurationNodeID, mapping.DurationInput = findWorkflowInput(workflow, nodeIDs, nil, "duration", "seconds")
	if mapping.DurationNodeID == "" {
		mapping.DurationNodeID, mapping.DurationInput = findPrimitiveDurationInput(workflow, nodeIDs)
	}
	mapping.FramesNodeID, mapping.FramesInput = findWorkflowInput(workflow, nodeIDs, nil, "frames", "num_frames", "length")
	if mapping.DurationNodeID != "" && mapping.FramesInput == "length" && workflowUsesMiniMaxH3(workflow) {
		mapping.FramesNodeID = ""
		mapping.FramesInput = ""
	}
	mapping.FPSNodeID, mapping.FPSInput = findWorkflowInput(workflow, nodeIDs, nil, "fps", "frame_rate")
	mapping.SeedNodeID, mapping.SeedInput = findWorkflowInput(workflow, nodeIDs, nil, "seed", "noise_seed")
	return mapping
}

func findPrimitiveDurationInput(workflow map[string]any, nodeIDs []string) (string, string) {
	for _, nodeID := range nodeIDs {
		node, ok := workflow[nodeID].(map[string]any)
		if !ok || !strings.EqualFold(strings.TrimSpace(fmt.Sprint(node["class_type"])), "PrimitiveFloat") {
			continue
		}
		meta, _ := node["_meta"].(map[string]any)
		title := strings.ToLower(strings.TrimSpace(fmt.Sprint(meta["title"])))
		if title != "float (duration)" && title != "duration" {
			continue
		}
		inputs, _ := node["inputs"].(map[string]any)
		if _, exists := inputs["value"]; exists {
			return nodeID, "value"
		}
	}
	for _, nodeID := range nodeIDs {
		node, ok := workflow[nodeID].(map[string]any)
		if !ok || !workflowNodeIsMiniMaxH3Video(node) {
			continue
		}
		inputs, _ := node["inputs"].(map[string]any)
		lengthNodeID := workflowConnectionNodeID(inputs["length"])
		if durationNodeID := connectedPrimitiveFloatInput(workflow, lengthNodeID); durationNodeID != "" {
			return durationNodeID, "value"
		}
	}
	return "", ""
}

func workflowNodeIsMiniMaxH3Video(node map[string]any) bool {
	classType := strings.ToLower(strings.TrimSpace(fmt.Sprint(node["class_type"])))
	return strings.Contains(classType, "minimaxh3") || strings.Contains(classType, "minimax_h3")
}

func connectedPrimitiveFloatInput(workflow map[string]any, nodeID string) string {
	return connectedPrimitiveFloatInputWithVisited(workflow, nodeID, map[string]struct{}{})
}

func connectedPrimitiveFloatInputWithVisited(workflow map[string]any, nodeID string, visited map[string]struct{}) string {
	if nodeID == "" {
		return ""
	}
	if _, exists := visited[nodeID]; exists {
		return ""
	}
	visited[nodeID] = struct{}{}
	node, ok := workflow[nodeID].(map[string]any)
	if !ok {
		return ""
	}
	if strings.EqualFold(strings.TrimSpace(fmt.Sprint(node["class_type"])), "PrimitiveFloat") {
		inputs, _ := node["inputs"].(map[string]any)
		if _, exists := inputs["value"]; exists {
			return nodeID
		}
		return ""
	}
	inputs, _ := node["inputs"].(map[string]any)
	for _, inputName := range []string{"values.a", "a", "duration", "seconds"} {
		if primitiveNodeID := connectedPrimitiveFloatInputWithVisited(workflow, workflowConnectionNodeID(inputs[inputName]), visited); primitiveNodeID != "" {
			return primitiveNodeID
		}
	}
	return ""
}

func findWorkflowInput(workflow map[string]any, nodeIDs []string, accept func(map[string]any) bool, aliases ...string) (string, string) {
	for _, alias := range aliases {
		for _, nodeID := range nodeIDs {
			node, ok := workflow[nodeID].(map[string]any)
			if !ok || (accept != nil && !accept(node)) {
				continue
			}
			inputs, ok := node["inputs"].(map[string]any)
			if !ok {
				continue
			}
			if _, ok := inputs[alias]; ok {
				return nodeID, alias
			}
		}
	}
	return "", ""
}

func (a *TaskAdaptor) applyRequestToWorkflow(c *gin.Context, workflow map[string]any, req relaycommon.TaskSubmitReq, metadata comfyMetadata, mapping dto.ComfyUINodeMappings, info *relaycommon.RelayInfo) error {
	if strings.TrimSpace(req.Prompt) != "" && mapping.PromptNodeID == "" {
		return comfyUIConfigurationError(
			"comfyui workflow prompt input could not be inferred; configure prompt_node_id explicitly",
			nil,
		)
	}
	if err := setWorkflowInput(workflow, mapping.PromptNodeID, firstNonEmpty(mapping.PromptInput, "text"), req.Prompt); err != nil {
		return err
	}
	if metadata.NegativePrompt != "" && mapping.NegativePromptNodeID != "" {
		if err := setWorkflowInput(workflow, mapping.NegativePromptNodeID, firstNonEmpty(mapping.NegativePromptInput, "text"), metadata.NegativePrompt); err != nil {
			return err
		}
	}
	_, referenceWorkflow := referenceWorkflowSpecFor(workflow)
	if referenceWorkflow && strings.TrimSpace(req.Image) != "" {
		return fmt.Errorf("comfyui reference workflow cannot consume top-level image as a first frame")
	}
	workflowMode, _ := inferMiniMaxH3WorkflowMode(workflow)
	workflowImage := firstNonEmpty(req.Image, firstString(req.Images))
	if !referenceWorkflow && workflowMode == "image_to_video" && workflowImage == "" {
		referenceImages := nonEmptyStrings(metadata.ReferenceImages)
		if len(referenceImages) == 1 && len(req.AdditionalReferenceImages()) == 0 {
			workflowImage = referenceImages[0]
			metadata.ReferenceImages = nil
		}
	}
	referenceWorkflow, err := a.applyReferenceMediaToWorkflow(c, workflow, req, metadata, info)
	if err != nil {
		return err
	}
	if !referenceWorkflow && (len(req.AdditionalReferenceImages()) > 0 ||
		len(nonEmptyStrings(metadata.ReferenceImages)) > 0 ||
		len(nonEmptyStrings(metadata.ReferenceVideos)) > 0 ||
		len(nonEmptyStrings(metadata.ReferenceAudios)) > 0) {
		return fmt.Errorf("comfyui workflow has no compatible reference media input")
	}
	if !referenceWorkflow && workflowImage != "" && mapping.ImageNodeID == "" {
		return fmt.Errorf("comfyui workflow image input could not be inferred; configure image_node_id explicitly")
	}
	if !referenceWorkflow && workflowImage != "" && mapping.ImageNodeID != "" {
		uploaded, err := a.uploadImageInput(c, info, workflowImage)
		if err != nil {
			return err
		}
		if err := setWorkflowInput(workflow, mapping.ImageNodeID, firstNonEmpty(mapping.ImageInput, "image"), uploaded); err != nil {
			return err
		}
	}
	if metadata.LastFrameImage != "" && mapping.LastFrameNodeID == "" {
		return fmt.Errorf("comfyui workflow last-frame input could not be inferred; configure last_frame_node_id explicitly")
	}
	if metadata.LastFrameImage != "" && mapping.LastFrameNodeID != "" {
		uploaded, err := a.uploadImageInput(c, info, metadata.LastFrameImage)
		if err != nil {
			return err
		}
		if err := setWorkflowInput(workflow, mapping.LastFrameNodeID, firstNonEmpty(mapping.LastFrameInput, "image"), uploaded); err != nil {
			return err
		}
	}
	width := firstNonZero(req.Width, metadata.Width)
	height := firstNonZero(req.Height, metadata.Height)
	if workflowUsesMiniMaxH3(workflow) {
		width, height = miniMaxH3Dimensions(
			width,
			height,
			firstNonEmpty(metadata.Resolution, req.Size),
			firstNonEmpty(metadata.Ratio, metadata.AspectRatio),
		)
	}
	if width > 0 && mapping.WidthNodeID != "" {
		if err := setWorkflowInput(workflow, mapping.WidthNodeID, firstNonEmpty(mapping.WidthInput, "width"), width); err != nil {
			return err
		}
	}
	if height > 0 && mapping.HeightNodeID != "" {
		if err := setWorkflowInput(workflow, mapping.HeightNodeID, firstNonEmpty(mapping.HeightInput, "height"), height); err != nil {
			return err
		}
	}
	duration := firstNonZero(req.Duration, metadata.Duration)
	if duration > 0 && mapping.DurationNodeID != "" {
		if err := setWorkflowInput(workflow, mapping.DurationNodeID, firstNonEmpty(mapping.DurationInput, "duration"), duration); err != nil {
			return err
		}
	}
	frames := firstNonZero(req.Fps*req.Duration, metadata.Frames)
	if frames > 0 && mapping.FramesNodeID != "" {
		if err := setWorkflowInput(workflow, mapping.FramesNodeID, firstNonEmpty(mapping.FramesInput, "frames"), frames); err != nil {
			return err
		}
	}
	fps := firstNonZero(req.Fps, metadata.FPS)
	if fps > 0 && mapping.FPSNodeID != "" {
		if err := setWorkflowInput(workflow, mapping.FPSNodeID, firstNonEmpty(mapping.FPSInput, "fps"), fps); err != nil {
			return err
		}
	}
	seed := firstNonZero(req.Seed, metadata.Seed)
	if seed > 0 && mapping.SeedNodeID != "" {
		if err := setWorkflowInput(workflow, mapping.SeedNodeID, firstNonEmpty(mapping.SeedInput, "seed"), seed); err != nil {
			return err
		}
	}
	return nil
}

func workflowUsesMiniMaxH3(workflow map[string]any) bool {
	for _, rawNode := range workflow {
		node, ok := rawNode.(map[string]any)
		if !ok {
			continue
		}
		classType := strings.ToLower(strings.TrimSpace(fmt.Sprint(node["class_type"])))
		if strings.Contains(classType, "minimaxh3") || strings.Contains(classType, "minimax_h3") {
			return true
		}
	}
	return false
}

func miniMaxH3Dimensions(width, height int, resolution, ratio string) (int, int) {
	const multiple = 32

	if width <= 0 || height <= 0 {
		normalizedRatio := strings.ToLower(strings.TrimSpace(ratio))
		if normalizedRatio == "auto" || normalizedRatio == "adaptive" {
			return 0, 0
		}
		width, height = miniMaxH3PresetDimensions(resolution, ratio)
	}
	if width <= 0 || height <= 0 {
		return width, height
	}
	width = alignH3DimensionNearest(width, multiple)
	height = alignH3DimensionNearest(height, multiple)
	return width, height
}

func miniMaxH3PresetDimensions(resolution, ratio string) (int, int) {
	tier := strings.ToLower(strings.TrimSpace(resolution))
	var width, height int
	switch tier {
	case "480", "480p":
		width, height = 854, 480
	case "640", "640p":
		width, height = 1138, 640
	case "720", "720p":
		width, height = 1280, 720
	case "768", "768p":
		width, height = 1344, 768
	case "1080", "1080p":
		width, height = 1920, 1080
	case "2k", "1440p":
		width, height = 2560, 1440
	default:
		return 0, 0
	}
	normalizedRatio := strings.ToLower(strings.TrimSpace(ratio))
	if normalizedRatio == "1:1" {
		return height, height
	}
	if normalizedRatio == "9:16" || normalizedRatio == "3:4" || normalizedRatio == "portrait" {
		return height, width
	}
	return width, height
}

func alignH3DimensionNearest(value, multiple int) int {
	if value < multiple {
		return multiple
	}
	return ((value + multiple/2) / multiple) * multiple
}

func (a *TaskAdaptor) uploadImageInput(c *gin.Context, info *relaycommon.RelayInfo, input string) (string, error) {
	if a.uploadInput != nil {
		return a.uploadInput(c, info, input, "image")
	}
	return a.uploadComfyUIInput(c, info, input, "image")
}

func (a *TaskAdaptor) uploadComfyUIInput(c *gin.Context, info *relaycommon.RelayInfo, input, mediaType string) (string, error) {
	filename, data, err := readMediaInput(c, info, input, mediaType)
	if err != nil {
		return "", err
	}
	filename = uniqueComfyUIInputFilename(filename, mediaType)
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("image", filename)
	if err != nil {
		return "", err
	}
	if _, err := part.Write(data); err != nil {
		return "", err
	}
	_ = writer.WriteField("type", "input")
	_ = writer.WriteField("overwrite", "true")
	if err := writer.Close(); err != nil {
		return "", err
	}
	req, err := http.NewRequest(http.MethodPost, a.baseURL+"/upload/image", &body)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("Accept", "application/json")
	if strings.TrimSpace(a.apiKey) != "" {
		req.Header.Set("Authorization", "Bearer "+a.apiKey)
		req.Header.Set("X-API-Key", a.apiKey)
	}
	client, err := service.GetHttpClientWithProxy(info.ChannelSetting.Proxy)
	if err != nil {
		return "", err
	}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	if resp.StatusCode >= http.StatusBadRequest {
		return "", fmt.Errorf("ComfyUI %s upload failed: HTTP %d - %s", mediaType, resp.StatusCode, string(respBody))
	}
	upload := uploadImageResponse{}
	if err := common.Unmarshal(respBody, &upload); err != nil {
		return "", err
	}
	if upload.Name == "" {
		return "", fmt.Errorf("ComfyUI %s upload returned empty name", mediaType)
	}
	if upload.Subfolder != "" {
		return strings.Trim(upload.Subfolder, "/") + "/" + upload.Name, nil
	}
	return upload.Name, nil
}

func readImageInput(c *gin.Context, info *relaycommon.RelayInfo, input string) (string, []byte, error) {
	return readMediaInput(c, info, input, "image")
}

func readMediaInput(c *gin.Context, info *relaycommon.RelayInfo, input, mediaType string) (string, []byte, error) {
	input = strings.TrimSpace(input)
	maxBytes := maxImageInputBytes()
	if strings.HasPrefix(input, "data:") {
		header, data, err := decodeDataURL(input, maxBytes)
		if err != nil {
			return "", nil, err
		}
		if !strings.HasPrefix(strings.ToLower(header), "data:"+mediaType+"/") {
			return "", nil, fmt.Errorf("invalid %s data URL content type", mediaType)
		}
		return "relayclaw-input" + extensionFromDataURLHeader(header, mediaType), data, nil
	}
	if strings.HasPrefix(input, "http://") || strings.HasPrefix(input, "https://") {
		resp, err := service.DoDownloadRequest(input, "comfyui "+mediaType+" input")
		if err != nil {
			return "", nil, err
		}
		defer resp.Body.Close()
		if resp.StatusCode >= http.StatusBadRequest {
			return "", nil, fmt.Errorf("download %s failed: HTTP %d", mediaType, resp.StatusCode)
		}
		contentType := strings.ToLower(strings.TrimSpace(strings.Split(resp.Header.Get("Content-Type"), ";")[0]))
		if contentType != "" && contentType != "application/octet-stream" && !strings.HasPrefix(contentType, mediaType+"/") {
			return "", nil, fmt.Errorf("invalid %s content type: %s", mediaType, contentType)
		}
		if resp.ContentLength > maxBytes {
			return "", nil, imageInputSizeError(resp.ContentLength, maxBytes)
		}
		data, err := io.ReadAll(io.LimitReader(resp.Body, maxBytes+1))
		if err != nil {
			return "", nil, err
		}
		if int64(len(data)) > maxBytes {
			return "", nil, imageInputSizeError(int64(len(data)), maxBytes)
		}
		return filenameFromURLWithFallback(input, mediaType, contentType), data, nil
	}
	return "", nil, fmt.Errorf("unsupported %s input: only data URL and http/https URL are supported", mediaType)
}

func setWorkflowInput(workflow map[string]any, nodeID, inputName string, value any) error {
	nodeID = strings.TrimSpace(nodeID)
	inputName = strings.TrimSpace(inputName)
	if nodeID == "" || inputName == "" {
		return nil
	}
	node, ok := workflow[nodeID].(map[string]any)
	if !ok {
		return fmt.Errorf("comfyui workflow node %q not found", nodeID)
	}
	inputs, ok := node["inputs"].(map[string]any)
	if !ok {
		inputs = map[string]any{}
		node["inputs"] = inputs
	}
	inputs[inputName] = value
	return nil
}

func wrapHistoryResponse(promptID, baseURL string, responseBody []byte) wrappedHistoryResponse {
	wrapped := wrappedHistoryResponse{
		PromptID: promptID,
		BaseURL:  baseURL,
		Status:   "running",
	}
	var history any
	if err := common.Unmarshal(responseBody, &history); err != nil {
		wrapped.Status = "failed"
		wrapped.Reason = err.Error()
		return wrapped
	}
	wrapped.History = history
	entry := historyEntry(history, promptID)
	if entry == nil {
		return wrapped
	}
	if failed, reason := historyFailed(entry); failed {
		wrapped.Status = "failed"
		wrapped.Reason = reason
		return wrapped
	}
	outputURL := firstOutputViewURL(entry, baseURL)
	if outputURL == "" {
		if historyCompleted(entry) {
			wrapped.Status = "failed"
			wrapped.Reason = "ComfyUI task completed without output media"
		}
		return wrapped
	}
	wrapped.Status = "succeeded"
	wrapped.URL = outputURL
	return wrapped
}

func wrapHistoryFailure(promptID, baseURL, reason string) wrappedHistoryResponse {
	return wrappedHistoryResponse{
		PromptID: promptID,
		BaseURL:  baseURL,
		Status:   "failed",
		Reason:   firstNonEmpty(reason, "ComfyUI task failed"),
	}
}

func historyEntry(history any, promptID string) map[string]any {
	root, ok := history.(map[string]any)
	if !ok {
		return nil
	}
	if entry, ok := root[promptID].(map[string]any); ok {
		return entry
	}
	if _, ok := root["outputs"]; ok {
		return root
	}
	return nil
}

func historyFailed(entry map[string]any) (bool, string) {
	status, ok := entry["status"].(map[string]any)
	if !ok {
		return false, ""
	}
	statusText := strings.ToLower(fmt.Sprint(status["status_str"]))
	if statusText == "error" || statusText == "failed" {
		return true, firstNonEmpty(fmt.Sprint(status["message"]), "ComfyUI execution failed")
	}
	if messages, ok := status["messages"].([]any); ok {
		for _, message := range messages {
			text := strings.ToLower(fmt.Sprint(message))
			if strings.Contains(text, "execution_error") {
				return true, fmt.Sprint(message)
			}
		}
	}
	return false, ""
}

func historyCompleted(entry map[string]any) bool {
	status, ok := entry["status"].(map[string]any)
	if !ok {
		return false
	}
	completed, ok := status["completed"].(bool)
	return ok && completed
}

func firstOutputViewURL(entry map[string]any, baseURL string) string {
	outputs, ok := entry["outputs"].(map[string]any)
	if !ok {
		return ""
	}
	nodeIDs := make([]string, 0, len(outputs))
	for nodeID := range outputs {
		nodeIDs = append(nodeIDs, nodeID)
	}
	sort.Strings(nodeIDs)
	for _, key := range []string{"videos", "gifs", "images"} {
		for _, nodeID := range nodeIDs {
			output := outputs[nodeID]
			outputMap, ok := output.(map[string]any)
			if !ok {
				continue
			}
			items, ok := outputMap[key].([]any)
			if !ok || len(items) == 0 {
				continue
			}
			for _, item := range items {
				itemMap, ok := item.(map[string]any)
				if !ok {
					continue
				}
				if viewURL := buildViewURL(baseURL, itemMap); viewURL != "" {
					return viewURL
				}
			}
		}
	}
	return ""
}

func buildViewURL(baseURL string, item map[string]any) string {
	filename := strings.TrimSpace(fmt.Sprint(item["filename"]))
	if filename == "" || filename == "<nil>" {
		return ""
	}
	query := url.Values{}
	query.Set("filename", filename)
	subfolder := strings.TrimSpace(fmt.Sprint(item["subfolder"]))
	if subfolder != "" && subfolder != "<nil>" {
		query.Set("subfolder", subfolder)
	}
	outputType := strings.TrimSpace(fmt.Sprint(item["type"]))
	if outputType == "" || outputType == "<nil>" {
		outputType = "output"
	}
	query.Set("type", outputType)
	return strings.TrimRight(baseURL, "/") + "/view?" + query.Encode()
}

func filenameFromURL(rawURL string) string {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return "relayclaw-input.png"
	}
	name := filepath.Base(parsed.Path)
	if name == "." || name == "/" || name == "" {
		return "relayclaw-input.png"
	}
	return name
}

func filenameFromURLWithFallback(rawURL, mediaType, contentType string) string {
	name := filenameFromURL(rawURL)
	extension := strings.ToLower(filepath.Ext(name))
	if mediaExtensionAllowed(mediaType, extension) {
		return name
	}
	return strings.TrimSuffix(name, filepath.Ext(name)) + extensionFromContentType(contentType, mediaType)
}

func uniqueComfyUIInputFilename(filename, mediaType string) string {
	extension := strings.ToLower(filepath.Ext(filename))
	if !mediaExtensionAllowed(mediaType, extension) {
		extension = defaultMediaExtension(mediaType)
	}
	return "relayclaw-" + common.GetUUID() + extension
}

func extensionFromContentType(contentType, mediaType string) string {
	switch strings.ToLower(strings.TrimSpace(strings.Split(contentType, ";")[0])) {
	case "image/jpeg":
		return ".jpg"
	case "image/png":
		return ".png"
	case "image/webp":
		return ".webp"
	case "video/mp4":
		return ".mp4"
	case "video/quicktime":
		return ".mov"
	case "video/webm":
		return ".webm"
	case "audio/wav", "audio/x-wav":
		return ".wav"
	case "audio/mpeg":
		return ".mp3"
	}
	return defaultMediaExtension(mediaType)
}

func mediaExtensionAllowed(mediaType, extension string) bool {
	extension = strings.ToLower(extension)
	switch mediaType {
	case "image":
		return extension == ".jpg" || extension == ".jpeg" || extension == ".png" || extension == ".webp"
	case "video":
		return extension == ".mp4" || extension == ".mov" || extension == ".webm"
	case "audio":
		return extension == ".wav" || extension == ".mp3"
	default:
		return false
	}
}

func extensionFromDataURLHeader(header, mediaType string) string {
	mimeType := strings.TrimPrefix(strings.ToLower(strings.Split(header, ";")[0]), "data:")
	switch mimeType {
	case "image/jpeg":
		return ".jpg"
	case "image/png":
		return ".png"
	case "image/webp":
		return ".webp"
	case "video/quicktime":
		return ".mov"
	case "video/webm":
		return ".webm"
	case "audio/wav", "audio/x-wav":
		return ".wav"
	case "audio/mpeg":
		return ".mp3"
	}
	return defaultMediaExtension(mediaType)
}

func defaultMediaExtension(mediaType string) string {
	switch mediaType {
	case "video":
		return ".mp4"
	case "audio":
		return ".mp3"
	default:
		return ".png"
	}
}

func decodeDataURL(value string, maxBytes int64) (string, []byte, error) {
	parts := strings.SplitN(value, ",", 2)
	if len(parts) != 2 {
		return "", nil, fmt.Errorf("invalid data url")
	}
	decodedLen := base64.StdEncoding.DecodedLen(len(parts[1]))
	if strings.HasSuffix(parts[1], "=") {
		decodedLen--
	}
	if strings.HasSuffix(parts[1], "==") {
		decodedLen--
	}
	if int64(decodedLen) > maxBytes {
		return "", nil, imageInputSizeError(int64(decodedLen), maxBytes)
	}
	data, err := base64.StdEncoding.DecodeString(parts[1])
	if err != nil {
		return "", nil, err
	}
	if int64(len(data)) > maxBytes {
		return "", nil, imageInputSizeError(int64(len(data)), maxBytes)
	}
	return parts[0], data, nil
}

func maxImageInputBytes() int64 {
	return int64(constant.MaxFileDownloadMB) * 1024 * 1024
}

func imageInputSizeError(size, maxBytes int64) error {
	return fmt.Errorf("image size %d exceeds maximum allowed size of %d bytes", size, maxBytes)
}

func mergeNodeMappings(base, override dto.ComfyUINodeMappings) dto.ComfyUINodeMappings {
	data, _ := common.Marshal(base)
	var merged dto.ComfyUINodeMappings
	_ = common.Unmarshal(data, &merged)
	overlay, _ := common.Marshal(override)
	values := map[string]any{}
	_ = common.Unmarshal(overlay, &values)
	baseValues := map[string]any{}
	_ = common.Unmarshal(data, &baseValues)
	for key, value := range values {
		if strings.TrimSpace(fmt.Sprint(value)) != "" {
			baseValues[key] = value
		}
	}
	mergedData, _ := common.Marshal(baseValues)
	_ = common.Unmarshal(mergedData, &merged)
	return merged
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" && trimmed != "<nil>" {
			return trimmed
		}
	}
	return ""
}

func firstNonZero(values ...int) int {
	for _, value := range values {
		if value != 0 {
			return value
		}
	}
	return 0
}

func firstString(values []string) string {
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			return trimmed
		}
	}
	return ""
}
