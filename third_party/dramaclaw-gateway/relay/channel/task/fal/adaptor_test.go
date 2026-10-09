package fal

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	basefal "github.com/QuantumNous/new-api/relay/channel/fal"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSeedanceGenericRoutesTextToVideo(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "make a cinematic video",
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.Action != constant.TaskActionTextGenerate {
		t.Fatalf("action = %q, want text generate", info.Action)
	}
	if info.UpstreamModelName != basefal.ModelSeedance20TextID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedance20TextID)
	}
	if _, ok := body["image_url"]; ok {
		t.Fatalf("text-to-video body should not contain image_url: %#v", body)
	}
}

func TestSeedanceGenericRoutesSingleImageToImageToVideo(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "animate this frame",
		Images: []string{"https://example.com/start.png"},
		Size:   "1280x720",
		Metadata: map[string]any{
			"duration":       5,
			"generate_audio": false,
		},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.Action != constant.TaskActionFirstTailGenerate {
		t.Fatalf("action = %q, want first-tail generate", info.Action)
	}
	if info.UpstreamModelName != basefal.ModelSeedance20ImageID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedance20ImageID)
	}
	if body["image_url"] != "https://example.com/start.png" {
		t.Fatalf("image_url = %#v", body["image_url"])
	}
	if body["resolution"] != "720p" {
		t.Fatalf("resolution = %#v", body["resolution"])
	}
	if body["aspect_ratio"] != "16:9" {
		t.Fatalf("aspect_ratio = %#v", body["aspect_ratio"])
	}
	if body["generate_audio"] != false {
		t.Fatalf("generate_audio false should be preserved: %#v", body["generate_audio"])
	}
}

func TestSeedanceUsesStandardDimensionsAndAutoDuration(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt:       "make a cinematic video",
		DurationAuto: true,
		Width:        1280,
		Height:       720,
	})

	require.NoError(t, err)
	assert.Equal(t, "auto", body["duration"])
	assert.Equal(t, "720p", body["resolution"])
	assert.Equal(t, "16:9", body["aspect_ratio"])
}

func TestSeedanceDoesNotInventResolutionFromNonStandardDimensions(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "make a square video",
		Width:  1024,
		Height: 1024,
	})

	require.NoError(t, err)
	assert.NotContains(t, body, "resolution")
	assert.Equal(t, "1:1", body["aspect_ratio"])
}

func TestSeedanceEstimateBillingDoesNotInventDuration(t *testing.T) {
	gin.SetMode(gin.TestMode)
	adaptor := &TaskAdaptor{}
	info := seedanceRelayInfo(basefal.ModelSeedance20)

	for _, req := range []relaycommon.TaskSubmitReq{
		{Prompt: "automatic duration", DurationAuto: true},
		{Prompt: "upstream default duration"},
	} {
		ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
		ctx.Request = httptest.NewRequest(http.MethodPost, "/v1/videos", nil)
		ctx.Set("task_request", req)

		ratios := adaptor.EstimateBilling(ctx, info)
		assert.NotContains(t, ratios, "seconds")
	}
}

func TestSeedanceStandardDurationTakesPriorityOverLegacyFields(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt:   "make a cinematic video",
		Duration: 8,
		Seconds:  "7",
		Metadata: map[string]any{"duration": 6},
	})

	require.NoError(t, err)
	assert.Equal(t, "8", body["duration"])
}

func TestSeedanceGenericRoutesTwoImagesToStartEnd(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "move from start to end",
		Images: []string{"https://example.com/start.png", "https://example.com/end.png"},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.UpstreamModelName != basefal.ModelSeedance20ImageID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedance20ImageID)
	}
	if body["image_url"] != "https://example.com/start.png" {
		t.Fatalf("image_url = %#v", body["image_url"])
	}
	if body["end_image_url"] != "https://example.com/end.png" {
		t.Fatalf("end_image_url = %#v", body["end_image_url"])
	}
}

func TestSeedanceGenericUsesStandardLastFrameMetadata(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "move from start to end",
		Image:  "https://example.com/start.png",
		Metadata: map[string]any{
			"last_frame_image": "https://example.com/end.png",
			"ratio":            "auto",
			"resolution":       "720p",
		},
	})

	require.NoError(t, err)
	assert.Equal(t, constant.TaskActionFirstTailGenerate, info.Action)
	assert.Equal(t, "https://example.com/start.png", body["image_url"])
	assert.Equal(t, "https://example.com/end.png", body["end_image_url"])
	assert.Equal(t, "auto", body["aspect_ratio"])
}

func TestSeedanceGenericRoutesMultipleImagesToReference(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "use references",
		Images: []string{"https://example.com/1.png", "https://example.com/2.png", "https://example.com/3.png"},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.Action != constant.TaskActionReferenceGenerate {
		t.Fatalf("action = %q, want reference generate", info.Action)
	}
	if info.UpstreamModelName != basefal.ModelSeedance20RefID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedance20RefID)
	}
	imageURLs, ok := body["image_urls"].([]any)
	if !ok || len(imageURLs) != 3 {
		t.Fatalf("image_urls = %#v", body["image_urls"])
	}
}

func TestSeedanceGenericUsesStandardReferenceMetadata(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt:       "use multimodal references",
		DurationAuto: true,
		Metadata: map[string]any{
			"ratio":            "auto",
			"resolution":       "720p",
			"reference_images": []string{"https://example.com/character.png", "https://example.com/scene.png"},
			"reference_videos": []string{"https://example.com/motion.mp4"},
			"reference_audios": []string{"https://example.com/audio.mp3"},
		},
	})

	require.NoError(t, err)
	assert.Equal(t, constant.TaskActionReferenceGenerate, info.Action)
	assert.Equal(t, "auto", body["duration"])
	assert.Equal(t, "auto", body["aspect_ratio"])
	assert.Equal(t, []any{"https://example.com/character.png", "https://example.com/scene.png"}, body["image_urls"])
	assert.Equal(t, []any{"https://example.com/motion.mp4"}, body["video_urls"])
	assert.Equal(t, []any{"https://example.com/audio.mp3"}, body["audio_urls"])
}

func TestSeedanceExplicitTextRejectsMedia(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20Text)
	_, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "text model with image should fail",
		Images: []string{"https://example.com/start.png"},
	})
	if err == nil {
		t.Fatalf("BuildRequestBody expected error")
	}
}

func TestSeedanceGenericIgnoresMappedTextEndpointForDynamicRouting(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	info.UpstreamModelName = basefal.ModelSeedance20TextID
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "animate this frame",
		Images: []string{"https://example.com/start.png"},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.UpstreamModelName != basefal.ModelSeedance20ImageID {
		t.Fatalf("generic model should dynamically switch from mapped text endpoint, got %q", info.UpstreamModelName)
	}
	if body["image_url"] != "https://example.com/start.png" {
		t.Fatalf("image_url = %#v", body["image_url"])
	}
}

func TestSeedanceFastGenericRoutesTextToVideo(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20Fast)
	_, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "make a fast cinematic video",
		Metadata: map[string]any{
			"resolution": "720p",
		},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.Action != constant.TaskActionTextGenerate {
		t.Fatalf("action = %q, want text generate", info.Action)
	}
	if info.UpstreamModelName != basefal.ModelSeedance20FastTextID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedance20FastTextID)
	}
}

func TestSeedanceFastGenericRoutesImageToVideo(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20Fast)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "fast animate",
		Images: []string{"https://example.com/start.png"},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.UpstreamModelName != basefal.ModelSeedance20FastImageID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedance20FastImageID)
	}
	if body["image_url"] != "https://example.com/start.png" {
		t.Fatalf("image_url = %#v", body["image_url"])
	}
}

func TestSeedanceFastGenericRejects1080p(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20Fast)
	_, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "fast 1080p should fail",
		Metadata: map[string]any{
			"resolution": "1080p",
		},
	})
	if err == nil {
		t.Fatalf("BuildRequestBody expected error")
	}
}

func TestSeedanceFastGenericIgnoresMappedTextEndpointForDynamicRouting(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedance20Fast)
	info.UpstreamModelName = basefal.ModelSeedance20FastTextID
	_, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "use fast references",
		Images: []string{
			"https://example.com/1.png",
			"https://example.com/2.png",
			"https://example.com/3.png",
		},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.UpstreamModelName != basefal.ModelSeedance20FastRefID {
		t.Fatalf("generic fast model should dynamically switch from mapped text endpoint, got %q", info.UpstreamModelName)
	}
}

func TestSeedanceV1ProFastGenericRoutesTextToVideo(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedanceV1ProFast)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "make a pro fast cinematic video",
		Metadata: map[string]any{
			"resolution":            "1080p",
			"duration":              "12",
			"aspect_ratio":          "16:9",
			"camera_fixed":          false,
			"enable_safety_checker": true,
			"num_frames":            120,
		},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.Action != constant.TaskActionTextGenerate {
		t.Fatalf("action = %q, want text generate", info.Action)
	}
	if info.UpstreamModelName != basefal.ModelSeedanceV1ProFastTextID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedanceV1ProFastTextID)
	}
	if body["resolution"] != "1080p" {
		t.Fatalf("resolution = %#v", body["resolution"])
	}
	if body["duration"] != "12" {
		t.Fatalf("duration = %#v", body["duration"])
	}
	if body["camera_fixed"] != false {
		t.Fatalf("camera_fixed false should be preserved: %#v", body["camera_fixed"])
	}
	if body["enable_safety_checker"] != true {
		t.Fatalf("enable_safety_checker = %#v", body["enable_safety_checker"])
	}
	if body["num_frames"] != float64(120) {
		t.Fatalf("num_frames = %#v", body["num_frames"])
	}
	if _, ok := body["image_url"]; ok {
		t.Fatalf("text-to-video body should not contain image_url: %#v", body)
	}
}

func TestSeedanceV1ProFastGenericRoutesImageToVideo(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedanceV1ProFast)
	body, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "animate this frame",
		Images: []string{"https://example.com/start.png"},
		Metadata: map[string]any{
			"aspect_ratio": "auto",
			"resolution":   "1080p",
			"duration":     2,
		},
	})
	if err != nil {
		t.Fatalf("BuildRequestBody returned error: %v", err)
	}

	if info.Action != constant.TaskActionFirstTailGenerate {
		t.Fatalf("action = %q, want first-tail generate", info.Action)
	}
	if info.UpstreamModelName != basefal.ModelSeedanceV1ProFastImageID {
		t.Fatalf("upstream = %q, want %q", info.UpstreamModelName, basefal.ModelSeedanceV1ProFastImageID)
	}
	if body["image_url"] != "https://example.com/start.png" {
		t.Fatalf("image_url = %#v", body["image_url"])
	}
	if _, ok := body["end_image_url"]; ok {
		t.Fatalf("v1 pro fast image-to-video body should not contain end_image_url: %#v", body)
	}
	if body["aspect_ratio"] != "auto" {
		t.Fatalf("aspect_ratio = %#v", body["aspect_ratio"])
	}
	if body["duration"] != "2" {
		t.Fatalf("duration = %#v", body["duration"])
	}
}

func TestSeedanceV1ProFastRejectsMultipleImages(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedanceV1ProFast)
	_, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "two images should fail",
		Images: []string{"https://example.com/start.png", "https://example.com/end.png"},
	})
	if err == nil {
		t.Fatalf("BuildRequestBody expected error")
	}
}

func TestSeedanceV1ProFastTextRejectsAutoAspectRatio(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedanceV1ProFast)
	_, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "auto ratio text should fail",
		Metadata: map[string]any{
			"aspect_ratio": "auto",
		},
	})
	if err == nil {
		t.Fatalf("BuildRequestBody expected error")
	}
}

func TestSeedanceV1ProFastRejectsAutoDuration(t *testing.T) {
	info := seedanceRelayInfo(basefal.ModelSeedanceV1ProFast)
	_, err := buildSeedanceBodyForTest(info, relaycommon.TaskSubmitReq{
		Prompt: "auto duration should fail",
		Metadata: map[string]any{
			"duration": "auto",
		},
	})
	if err == nil {
		t.Fatalf("BuildRequestBody expected error")
	}
}

func TestSeedanceParseTaskResultCompleted(t *testing.T) {
	payload := []byte(`{"status":"COMPLETED","response":{"video":{"url":"https://example.com/out.mp4"},"seed":42}}`)
	taskInfo, err := (&TaskAdaptor{}).ParseTaskResult(payload)
	if err != nil {
		t.Fatalf("ParseTaskResult returned error: %v", err)
	}
	if taskInfo.Status != string(model.TaskStatusSuccess) {
		t.Fatalf("status = %q, want success", taskInfo.Status)
	}
	if taskInfo.Url != "https://example.com/out.mp4" {
		t.Fatalf("url = %q", taskInfo.Url)
	}
}

func TestSeedanceDoResponseReturnsPublicTask(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/videos", nil)
	info := seedanceRelayInfo(basefal.ModelSeedance20)
	info.Action = constant.TaskActionTextGenerate
	info.PublicTaskID = "task_public"

	resp := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(bytes.NewBufferString(`{"request_id":"fal_req","status_url":"https://queue.fal.run/x/status","response_url":"https://queue.fal.run/x"}`)),
	}
	taskID, taskData, taskErr := (&TaskAdaptor{}).DoResponse(c, resp, info)
	if taskErr != nil {
		t.Fatalf("DoResponse returned task error: %v", taskErr)
	}
	if taskID != basefal.ModelSeedance20TextID+"|fal_req" {
		t.Fatalf("taskID = %q", taskID)
	}
	var stored map[string]any
	if err := common.Unmarshal(taskData, &stored); err != nil {
		t.Fatalf("taskData unmarshal error: %v", err)
	}
	if stored["endpoint"] != basefal.ModelSeedance20TextID {
		t.Fatalf("endpoint = %#v", stored["endpoint"])
	}
	if recorder.Code != http.StatusOK {
		t.Fatalf("response code = %d", recorder.Code)
	}
}

func TestSeedanceDecodeFalQueueTaskID(t *testing.T) {
	endpoint, requestID := decodeFalQueueTaskID(basefal.ModelSeedance20FastImageID + "|fal_req")
	if endpoint != basefal.ModelSeedance20FastImageID {
		t.Fatalf("endpoint = %q", endpoint)
	}
	if requestID != "fal_req" {
		t.Fatalf("requestID = %q", requestID)
	}

	endpoint, requestID = decodeFalQueueTaskID(basefal.ModelSeedanceV1ProFastImageID + "|fal_req_v1")
	if endpoint != basefal.ModelSeedanceV1ProFastImageID {
		t.Fatalf("endpoint = %q", endpoint)
	}
	if requestID != "fal_req_v1" {
		t.Fatalf("requestID = %q", requestID)
	}

	endpoint, requestID = decodeFalQueueTaskID("legacy_req")
	if endpoint != "" || requestID != "legacy_req" {
		t.Fatalf("legacy decode = %q, %q", endpoint, requestID)
	}
}

func seedanceRelayInfo(modelName string) *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		OriginModelName: modelName,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelBaseUrl:    "https://fal.run",
			UpstreamModelName: modelName,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{},
	}
}

func buildSeedanceBodyForTest(info *relaycommon.RelayInfo, req relaycommon.TaskSubmitReq) (map[string]any, error) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/videos", nil)
	c.Set("task_request", req)

	adaptor := &TaskAdaptor{}
	adaptor.Init(info)
	reader, err := adaptor.BuildRequestBody(c, info)
	if err != nil {
		return nil, err
	}
	bodyBytes, err := io.ReadAll(reader)
	if err != nil {
		return nil, err
	}
	var body map[string]any
	if err := common.Unmarshal(bodyBytes, &body); err != nil {
		return nil, err
	}
	return body, nil
}
