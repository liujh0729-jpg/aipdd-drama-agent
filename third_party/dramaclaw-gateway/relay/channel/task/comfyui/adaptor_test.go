package comfyui

import (
	"encoding/base64"
	"fmt"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestBuildComfyUIWorkflowFromRequest(t *testing.T) {
	adaptor := &TaskAdaptor{
		settings: dto.ComfyUISettings{
			Workflow: map[string]any{
				"1": map[string]any{"inputs": map[string]any{"text": ""}},
				"2": map[string]any{"inputs": map[string]any{"width": 0, "height": 0}},
				"3": map[string]any{"inputs": map[string]any{"duration": 0}},
				"4": map[string]any{"inputs": map[string]any{"seed": 0}},
			},
			NodeMappings: dto.ComfyUINodeMappings{
				PromptNodeID:   "1",
				PromptInput:    "text",
				WidthNodeID:    "2",
				WidthInput:     "width",
				HeightNodeID:   "2",
				HeightInput:    "height",
				DurationNodeID: "3",
				DurationInput:  "duration",
				SeedNodeID:     "4",
				SeedInput:      "seed",
			},
		},
	}
	req := relaycommon.TaskSubmitReq{
		Model:    "comfyui-video",
		Prompt:   "a local video",
		Width:    1280,
		Height:   720,
		Duration: 5,
		Seed:     42,
	}

	workflow, _, err := adaptor.workflowForRequest(req, comfyMetadata{}, req.Model)
	require.NoError(t, err)
	err = adaptor.applyRequestToWorkflow(nil, workflow, req, comfyMetadata{}, adaptor.nodeMappingForModel(req.Model, workflow), &relaycommon.RelayInfo{})
	require.NoError(t, err)

	assert.Equal(t, "a local video", workflow["1"].(map[string]any)["inputs"].(map[string]any)["text"])
	assert.Equal(t, 1280, workflow["2"].(map[string]any)["inputs"].(map[string]any)["width"])
	assert.Equal(t, 720, workflow["2"].(map[string]any)["inputs"].(map[string]any)["height"])
	assert.Equal(t, 5, workflow["3"].(map[string]any)["inputs"].(map[string]any)["duration"])
	assert.Equal(t, 42, workflow["4"].(map[string]any)["inputs"].(map[string]any)["seed"])
}

func TestMiniMaxH3Dimensions(t *testing.T) {
	tests := []struct {
		name                  string
		width, height         int
		resolution, ratio     string
		wantWidth, wantHeight int
	}{
		{name: "align 720p request", width: 1280, height: 720, resolution: "720p", ratio: "16:9", wantWidth: 1280, wantHeight: 736},
		{name: "derive 480p", resolution: "480p", ratio: "16:9", wantWidth: 864, wantHeight: 480},
		{name: "derive 640p", resolution: "640p", ratio: "16:9", wantWidth: 1152, wantHeight: 640},
		{name: "align 1080p", width: 1920, height: 1080, resolution: "1080p", ratio: "16:9", wantWidth: 1920, wantHeight: 1088},
		{name: "keep valid 2k", resolution: "2K", ratio: "16:9", wantWidth: 2560, wantHeight: 1440},
		{name: "derive 2k square", resolution: "2K", ratio: "1:1", wantWidth: 1440, wantHeight: 1440},
		{name: "derive portrait", resolution: "480p", ratio: "9:16", wantWidth: 480, wantHeight: 864},
		{name: "leave auto dimensions to workflow", resolution: "720p", ratio: "auto", wantWidth: 0, wantHeight: 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			width, height := miniMaxH3Dimensions(tt.width, tt.height, tt.resolution, tt.ratio)
			assert.Equal(t, tt.wantWidth, width)
			assert.Equal(t, tt.wantHeight, height)
			assert.Zero(t, width%32)
			assert.Zero(t, height%32)
		})
	}
}

func TestApplyRequestAlignsOnlyMiniMaxH3WorkflowDimensions(t *testing.T) {
	workflow := map[string]any{
		"1": map[string]any{"class_type": "MiniMaxH3TextToVideo", "inputs": map[string]any{"prompt": "", "width": 0, "height": 0}},
	}
	err := (&TaskAdaptor{}).applyRequestToWorkflow(nil, workflow, relaycommon.TaskSubmitReq{
		Prompt: "video", Width: 1280, Height: 720,
	}, comfyMetadata{}, inferNodeMappings(workflow), &relaycommon.RelayInfo{})
	require.NoError(t, err)
	inputs := workflow["1"].(map[string]any)["inputs"].(map[string]any)
	assert.Equal(t, 1280, inputs["width"])
	assert.Equal(t, 736, inputs["height"])
}

func TestComfyUIWorkflowRejectsRequestMetadataOverride(t *testing.T) {
	configuredWorkflow := map[string]any{
		"1": map[string]any{"class_type": "ConfiguredNode", "inputs": map[string]any{}},
	}
	adaptor := &TaskAdaptor{
		settings: dto.ComfyUISettings{
			WorkflowByModel: map[string]any{"comfyui-video": configuredWorkflow},
		},
	}
	metadata := comfyMetadata{
		Workflow: map[string]any{
			"1": map[string]any{"class_type": "UntrustedNode", "inputs": map[string]any{}},
		},
	}

	workflow, _, err := adaptor.workflowForRequest(
		relaycommon.TaskSubmitReq{Model: "comfyui-video"},
		metadata,
		"comfyui-video",
	)

	require.ErrorContains(t, err, "cannot be supplied in request metadata")
	assert.Nil(t, workflow)
}

func TestComfyUIWorkflowUsesConfiguredModelWorkflow(t *testing.T) {
	modelWorkflow := map[string]any{
		"1": map[string]any{"class_type": "ModelNode", "inputs": map[string]any{}},
	}
	defaultWorkflow := map[string]any{
		"1": map[string]any{"class_type": "DefaultNode", "inputs": map[string]any{}},
	}
	adaptor := &TaskAdaptor{
		settings: dto.ComfyUISettings{
			Workflow:        defaultWorkflow,
			WorkflowByModel: map[string]any{"comfyui-video": modelWorkflow},
		},
	}

	workflow, _, err := adaptor.workflowForRequest(
		relaycommon.TaskSubmitReq{Model: "comfyui-video"},
		comfyMetadata{},
		"comfyui-video",
	)

	require.NoError(t, err)
	assert.Equal(t, "ModelNode", workflow["1"].(map[string]any)["class_type"])
}

func TestComfyUIWorkflowRequiresChannelConfiguration(t *testing.T) {
	_, _, err := (&TaskAdaptor{}).workflowForRequest(
		relaycommon.TaskSubmitReq{Model: "comfyui-video"},
		comfyMetadata{},
		"comfyui-video",
	)

	require.ErrorContains(t, err, "workflow is required in channel settings")
	assertTaskBuildError(t, err, "comfyui_configuration_error", http.StatusInternalServerError)
}

func TestComfyUIWorkflowRoutesByRequestMode(t *testing.T) {
	zero, one, three := 0, 1, 3
	adaptor := &TaskAdaptor{settings: dto.ComfyUISettings{WorkflowRoutes: []dto.ComfyUIWorkflowRoute{
		{ID: "t2v", Priority: 10, Match: dto.ComfyUIWorkflowMatch{Modes: []string{"text_to_video"}, ReferenceImages: dto.ComfyUICountRange{Max: &zero}}, Workflow: map[string]any{"1": map[string]any{"class_type": "T2V"}}},
		{ID: "i2v", Priority: 20, Match: dto.ComfyUIWorkflowMatch{Modes: []string{"image_to_video"}, ReferenceImages: dto.ComfyUICountRange{Min: &one, Max: &one}}, Workflow: map[string]any{"1": map[string]any{"class_type": "I2V"}}},
		{ID: "r2v", Priority: 30, Match: dto.ComfyUIWorkflowMatch{Modes: []string{"reference_to_video"}, ReferenceVideos: dto.ComfyUICountRange{Min: &one, Max: &three}}, Workflow: map[string]any{"1": map[string]any{"class_type": "R2V"}}},
	}}}

	tests := []struct {
		name     string
		req      relaycommon.TaskSubmitReq
		metadata comfyMetadata
		want     string
	}{
		{name: "text", req: relaycommon.TaskSubmitReq{Prompt: "text"}, want: "T2V"},
		{name: "image", req: relaycommon.TaskSubmitReq{Image: "https://example.com/a.png"}, want: "I2V"},
		{name: "reference video", metadata: comfyMetadata{ReferenceVideos: []string{"https://example.com/a.mp4"}}, want: "R2V"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			workflow, _, err := adaptor.workflowForRequest(tt.req, tt.metadata, "minimax-h3")
			require.NoError(t, err)
			assert.Equal(t, tt.want, workflow["1"].(map[string]any)["class_type"])
		})
	}
}

func TestComfyUIWorkflowRoutesVideoEditFromProtocolShape(t *testing.T) {
	zero, one := 0, 1
	adaptor := &TaskAdaptor{settings: dto.ComfyUISettings{WorkflowRoutes: []dto.ComfyUIWorkflowRoute{
		{ID: "r2v", Priority: 10, Match: dto.ComfyUIWorkflowMatch{Modes: []string{"reference_to_video"}}, Workflow: map[string]any{"1": map[string]any{"class_type": "R2V"}}},
		{ID: "edit", Priority: 20, Match: dto.ComfyUIWorkflowMatch{Modes: []string{"video_edit"}, ReferenceVideos: dto.ComfyUICountRange{Min: &one}, ReferenceAudios: dto.ComfyUICountRange{Max: &zero}}, Workflow: map[string]any{"1": map[string]any{"class_type": "VideoEdit"}}},
	}}}
	req := relaycommon.TaskSubmitReq{Model: "local-edit", DurationAuto: true}
	metadata := comfyMetadata{Ratio: "auto", ReferenceVideos: []string{"https://example.com/source.mp4"}}

	route, err := selectWorkflowRoute(adaptor.settings.WorkflowRoutes, req, metadata, req.Model)
	require.NoError(t, err)
	assert.Equal(t, "edit", route.ID)
}

func TestComfyUIWorkflowRoutesInferMiniMaxH3ModeAndLimits(t *testing.T) {
	adaptor := &TaskAdaptor{settings: dto.ComfyUISettings{WorkflowRoutes: []dto.ComfyUIWorkflowRoute{
		{ID: "t2v", Match: dto.ComfyUIWorkflowMatch{Models: []string{"minimax-h3"}}, Workflow: map[string]any{
			"1": map[string]any{"class_type": "MiniMaxH3ImageToVideo"},
		}},
		{ID: "i2v", Match: dto.ComfyUIWorkflowMatch{Models: []string{"minimax-h3"}}, Workflow: map[string]any{
			"1": map[string]any{"class_type": "MiniMaxH3ImageToVideo"},
			"2": map[string]any{"class_type": "LoadImage"},
		}},
		{ID: "r2v", Match: dto.ComfyUIWorkflowMatch{Models: []string{"minimax-h3"}}, Workflow: map[string]any{
			"1": map[string]any{"class_type": "MiniMaxH3ReferenceToVideo"},
		}},
	}}}

	tests := []struct {
		name     string
		req      relaycommon.TaskSubmitReq
		metadata comfyMetadata
		want     string
	}{
		{name: "t2v", req: relaycommon.TaskSubmitReq{Duration: 5}, want: "MiniMaxH3ImageToVideo"},
		{name: "i2v", req: relaycommon.TaskSubmitReq{Duration: 5, Image: "https://example.com/a.png"}, want: "MiniMaxH3ImageToVideo"},
		{name: "r2v", req: relaycommon.TaskSubmitReq{Duration: 5}, metadata: comfyMetadata{ReferenceVideos: []string{"https://example.com/a.mp4"}}, want: "MiniMaxH3ReferenceToVideo"},
		{name: "t2v without duration", req: relaycommon.TaskSubmitReq{}, want: "MiniMaxH3ImageToVideo"},
		{name: "i2v without duration", req: relaycommon.TaskSubmitReq{Image: "https://example.com/a.png"}, want: "MiniMaxH3ImageToVideo"},
		{name: "r2v without duration", metadata: comfyMetadata{ReferenceVideos: []string{"https://example.com/a.mp4"}}, want: "MiniMaxH3ReferenceToVideo"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			workflow, _, err := adaptor.workflowForRequest(tt.req, tt.metadata, "minimax-h3")
			require.NoError(t, err)
			assert.Equal(t, tt.want, workflow["1"].(map[string]any)["class_type"])
		})
	}

	_, _, err := adaptor.workflowForRequest(relaycommon.TaskSubmitReq{
		Duration: 5, Images: []string{"https://example.com/a.png", "https://example.com/b.png"},
	}, comfyMetadata{}, "minimax-h3")
	require.ErrorContains(t, err, "no comfyui workflow route matches")
	assertTaskBuildError(t, err, "invalid_request", http.StatusBadRequest)

	_, _, err = adaptor.workflowForRequest(relaycommon.TaskSubmitReq{Duration: 16}, comfyMetadata{}, "minimax-h3")
	require.ErrorContains(t, err, "no comfyui workflow route matches")
}

func TestComfyUIWorkflowRouteRejectsUnknownModeInference(t *testing.T) {
	adaptor := &TaskAdaptor{settings: dto.ComfyUISettings{WorkflowRoutes: []dto.ComfyUIWorkflowRoute{{
		ID: "unknown", Workflow: map[string]any{"1": map[string]any{"class_type": "CustomVideoNode"}},
	}}}}

	_, _, err := adaptor.workflowForRequest(relaycommon.TaskSubmitReq{Duration: 5}, comfyMetadata{}, "custom")
	require.ErrorContains(t, err, "generation mode cannot be inferred")
}

func TestComfyUIWorkflowRoutesRejectAmbiguousAndConflictingRequests(t *testing.T) {
	workflow := map[string]any{"1": map[string]any{"class_type": "T2V"}}
	adaptor := &TaskAdaptor{settings: dto.ComfyUISettings{WorkflowRoutes: []dto.ComfyUIWorkflowRoute{
		{ID: "one", Priority: 10, Match: dto.ComfyUIWorkflowMatch{Modes: []string{"text_to_video"}}, Workflow: workflow},
		{ID: "two", Priority: 10, Match: dto.ComfyUIWorkflowMatch{Modes: []string{"text_to_video"}}, Workflow: workflow},
	}}}

	_, _, err := adaptor.workflowForRequest(relaycommon.TaskSubmitReq{}, comfyMetadata{}, "minimax-h3")
	require.ErrorContains(t, err, "multiple comfyui workflow routes match")
	assertTaskBuildError(t, err, "comfyui_configuration_error", http.StatusInternalServerError)

	_, _, err = adaptor.workflowForRequest(relaycommon.TaskSubmitReq{Image: "https://example.com/first.png"}, comfyMetadata{
		LastFrameImage: "https://example.com/last.png", ReferenceVideos: []string{"https://example.com/reference.mp4"},
	}, "minimax-h3")
	require.ErrorContains(t, err, "cannot be mixed")
}

func TestComfyUIWorkflowRouteRequiresWorkflow(t *testing.T) {
	routes := []dto.ComfyUIWorkflowRoute{{
		ID: "missing-workflow",
		Match: dto.ComfyUIWorkflowMatch{
			Modes: []string{"text_to_video"},
		},
	}}

	_, err := selectWorkflowRoute(routes, relaycommon.TaskSubmitReq{}, comfyMetadata{}, "minimax-h3")

	require.ErrorContains(t, err, "has no workflow")
	assertTaskBuildError(t, err, "comfyui_configuration_error", http.StatusInternalServerError)
}

func TestMergeTaskContentMediaMakesInputsAvailableForWorkflowInjection(t *testing.T) {
	req, metadata := mergeTaskContentMedia(relaycommon.TaskSubmitReq{
		Content: []relaycommon.TaskContentItem{
			{Type: "image_url", ImageURL: map[string]any{"url": "https://example.com/first.png"}},
			{Type: "image_url", Role: "last_frame", ImageURL: "https://example.com/last.png"},
			{Type: "image_url", Role: "reference_image", ImageURL: map[string]any{"url": "https://example.com/reference.png"}},
			{Type: "video_url", Role: "reference_video", VideoURL: map[string]any{"url": "https://example.com/reference.mp4"}},
			{Type: "audio_url", Role: "reference_audio", AudioURL: "https://example.com/reference.mp3"},
		},
	}, comfyMetadata{})

	assert.Equal(t, "https://example.com/first.png", req.Image)
	assert.Equal(t, "https://example.com/last.png", metadata.LastFrameImage)
	assert.Equal(t, []string{"https://example.com/reference.png"}, metadata.ReferenceImages)
	assert.Equal(t, []string{"https://example.com/reference.mp4"}, metadata.ReferenceVideos)
	assert.Equal(t, []string{"https://example.com/reference.mp3"}, metadata.ReferenceAudios)

	metadata.LastFrameImage = ""
	routeRequest, err := classifyWorkflowRouteRequest(req, metadata, "minimax-h3")
	require.NoError(t, err)
	assert.Equal(t, 2, routeRequest.images)
	assert.Equal(t, 1, routeRequest.videos)
	assert.Equal(t, 1, routeRequest.audios)
}

func TestReadImageInputRejectsOversizedDataURL(t *testing.T) {
	previousLimit := constant.MaxFileDownloadMB
	constant.MaxFileDownloadMB = 1
	t.Cleanup(func() {
		constant.MaxFileDownloadMB = previousLimit
	})
	payload := base64.StdEncoding.EncodeToString([]byte(strings.Repeat("x", 1024*1024+1)))

	_, _, err := readImageInput(nil, nil, "data:image/png;base64,"+payload)

	require.ErrorContains(t, err, "exceeds maximum allowed size")
}

func TestReadImageInputAcceptsDataURLAtSizeLimit(t *testing.T) {
	previousLimit := constant.MaxFileDownloadMB
	constant.MaxFileDownloadMB = 1
	t.Cleanup(func() {
		constant.MaxFileDownloadMB = previousLimit
	})
	payload := base64.StdEncoding.EncodeToString([]byte(strings.Repeat("x", 1024*1024)))

	filename, data, err := readImageInput(nil, nil, "data:image/png;base64,"+payload)

	require.NoError(t, err)
	assert.Equal(t, "relayclaw-input.png", filename)
	assert.Len(t, data, 1024*1024)
}

func TestInferComfyUIWorkflowNodeMappings(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs": map[string]any{
				"prompt": "old prompt",
				"width":  []any{"20", 0},
				"height": []any{"20", 1},
				"length": []any{"30", 0},
			},
		},
		"5": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "old.png"},
		},
		"40": map[string]any{
			"class_type": "CreateVideo",
			"inputs":     map[string]any{"fps": 24},
		},
	}

	mapping := inferNodeMappings(workflow)
	assert.Equal(t, "10", mapping.PromptNodeID)
	assert.Equal(t, "prompt", mapping.PromptInput)
	assert.Equal(t, "5", mapping.ImageNodeID)
	assert.Equal(t, "image", mapping.ImageInput)
	assert.Equal(t, "10", mapping.WidthNodeID)
	assert.Equal(t, "10", mapping.HeightNodeID)
	assert.Equal(t, "10", mapping.FramesNodeID)
	assert.Equal(t, "40", mapping.FPSNodeID)

	req := relaycommon.TaskSubmitReq{
		Prompt:   "new prompt",
		Width:    1280,
		Height:   720,
		Duration: 5,
		Fps:      24,
	}
	err := (&TaskAdaptor{}).applyRequestToWorkflow(nil, workflow, req, comfyMetadata{}, mapping, &relaycommon.RelayInfo{})
	require.NoError(t, err)
	inputs := workflow["10"].(map[string]any)["inputs"].(map[string]any)
	assert.Equal(t, "new prompt", inputs["prompt"])
	assert.Equal(t, 1280, inputs["width"])
	assert.Equal(t, 736, inputs["height"])
	assert.Equal(t, 120, inputs["length"])
	assert.Equal(t, 24, workflow["40"].(map[string]any)["inputs"].(map[string]any)["fps"])
}

func TestApplyRequestPromotesSingleReferenceImageForPlainImageToVideoWorkflow(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs":     map[string]any{"prompt": "old prompt"},
		},
		"114": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "transparent_rgb_gaming_mouse.png"},
		},
	}
	adaptor := &TaskAdaptor{
		uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, source, mediaType string) (string, error) {
			assert.Equal(t, "https://example.com/input.png", source)
			assert.Equal(t, "image", mediaType)
			return "uploaded-input.png", nil
		},
	}

	err := adaptor.applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Prompt: "new prompt"},
		comfyMetadata{ReferenceImages: []string{" ", "https://example.com/input.png"}},
		inferNodeMappings(workflow),
		&relaycommon.RelayInfo{},
	)

	require.NoError(t, err)
	assert.Equal(t, "uploaded-input.png", workflow["114"].(map[string]any)["inputs"].(map[string]any)["image"])
}

func TestApplyRequestRejectsMultipleReferenceImagesForPlainImageToVideoWorkflow(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs":     map[string]any{"prompt": "old prompt"},
		},
		"114": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "example.png"},
		},
	}

	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Prompt: "new prompt"},
		comfyMetadata{ReferenceImages: []string{
			"https://example.com/first.png",
			"https://example.com/second.png",
		}},
		inferNodeMappings(workflow),
		&relaycommon.RelayInfo{},
	)

	require.Error(t, err)
	assert.Contains(t, err.Error(), "no compatible reference media input")
	assert.Equal(t, "example.png", workflow["114"].(map[string]any)["inputs"].(map[string]any)["image"])
}

func TestApplyRequestDoesNotPromoteReferenceImageForNonImageToVideoWorkflow(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs":     map[string]any{"prompt": "old prompt"},
		},
	}

	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Prompt: "new prompt"},
		comfyMetadata{ReferenceImages: []string{"https://example.com/input.png"}},
		inferNodeMappings(workflow),
		&relaycommon.RelayInfo{},
	)

	require.ErrorContains(t, err, "no compatible reference media input")
}

func TestApplyRequestInjectsSingleReferenceImageWithoutModeHint(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs":     map[string]any{"prompt": "old prompt"},
		},
		"114": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "example.png"},
		},
	}

	adaptor := &TaskAdaptor{uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, source, mediaType string) (string, error) {
		assert.Equal(t, "https://example.com/input.png", source)
		assert.Equal(t, "image", mediaType)
		return "uploaded-input.png", nil
	}}
	err := adaptor.applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Prompt: "new prompt"},
		comfyMetadata{ReferenceImages: []string{"https://example.com/input.png"}},
		inferNodeMappings(workflow),
		&relaycommon.RelayInfo{},
	)

	require.NoError(t, err)
	assert.Equal(t, "uploaded-input.png", workflow["114"].(map[string]any)["inputs"].(map[string]any)["image"])
}

func TestApplyRequestRejectsTopLevelFirstFrameCombinedWithReferenceImage(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs":     map[string]any{"prompt": "old prompt"},
		},
		"114": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "example.png"},
		},
	}
	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Prompt: "new prompt", Image: "https://example.com/input.png"},
		comfyMetadata{ReferenceImages: []string{" https://example.com/input.png "}},
		inferNodeMappings(workflow),
		&relaycommon.RelayInfo{},
	)

	require.ErrorContains(t, err, "no compatible reference media input")
}

func TestApplyRequestRejectsReferenceImageDifferentFromTopLevelFirstFrame(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs":     map[string]any{"prompt": "old prompt"},
		},
		"114": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "example.png"},
		},
	}

	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Prompt: "new prompt", Image: "https://example.com/first.png"},
		comfyMetadata{ReferenceImages: []string{"https://example.com/reference.png"}},
		inferNodeMappings(workflow),
		&relaycommon.RelayInfo{},
	)

	require.ErrorContains(t, err, "no compatible reference media input")
}

func TestInferComfyUIDurationFromPrimitiveTitle(t *testing.T) {
	workflow := map[string]any{
		"100": map[string]any{
			"class_type": "PrimitiveFloat",
			"inputs":     map[string]any{"value": float64(3)},
			"_meta":      map[string]any{"title": "Audio Duration"},
		},
		"101": map[string]any{
			"class_type": "DurationMultiplier",
			"inputs":     map[string]any{"value": float64(2)},
			"_meta":      map[string]any{"title": "Float (duration)"},
		},
		"105:111": map[string]any{
			"class_type": "PrimitiveFloat",
			"inputs":     map[string]any{"value": float64(5)},
			"_meta":      map[string]any{"title": "Float (duration)"},
		},
		"105:107": map[string]any{
			"class_type": "ComfyMathExpression",
			"inputs": map[string]any{
				"expression": "max(5, round(a * 24))",
				"values.a":   []any{"105:111", 0},
			},
		},
		"200": map[string]any{
			"class_type": "PrimitiveFloat",
			"inputs":     map[string]any{"value": float64(7.5)},
			"_meta":      map[string]any{"title": "Float (guidance)"},
		},
	}

	mapping := inferNodeMappings(workflow)
	assert.Equal(t, "105:111", mapping.DurationNodeID)
	assert.Equal(t, "value", mapping.DurationInput)

	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Duration: 12},
		comfyMetadata{},
		mapping,
		&relaycommon.RelayInfo{},
	)
	require.NoError(t, err)
	assert.Equal(t, 12, workflow["105:111"].(map[string]any)["inputs"].(map[string]any)["value"])
	assert.Equal(t, float64(3), workflow["100"].(map[string]any)["inputs"].(map[string]any)["value"])
	assert.Equal(t, float64(2), workflow["101"].(map[string]any)["inputs"].(map[string]any)["value"])
	assert.Equal(t, float64(7.5), workflow["200"].(map[string]any)["inputs"].(map[string]any)["value"])
}

func TestInferMiniMaxH3DurationFromWorkflowConnectionsWithoutMetadata(t *testing.T) {
	workflow := map[string]any{
		"10": map[string]any{
			"class_type": "MiniMaxH3ImageToVideo",
			"inputs": map[string]any{
				"length": []any{"20", 1},
			},
		},
		"20": map[string]any{
			"class_type": "ComfyMathExpression",
			"inputs": map[string]any{
				"expression": "max(5, round(a * 24))",
				"values.a":   []any{"30", 0},
			},
		},
		"30": map[string]any{
			"class_type": "PrimitiveFloat",
			"inputs":     map[string]any{"value": float64(5)},
		},
		"40": map[string]any{
			"class_type": "CreateVideo",
			"inputs":     map[string]any{"fps": 24},
		},
	}

	mapping := inferNodeMappings(workflow)
	assert.Equal(t, "30", mapping.DurationNodeID)
	assert.Equal(t, "value", mapping.DurationInput)
	assert.Empty(t, mapping.FramesNodeID)
	assert.Empty(t, mapping.FramesInput)

	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Duration: 12, Fps: 24},
		comfyMetadata{},
		mapping,
		&relaycommon.RelayInfo{},
	)
	require.NoError(t, err)
	assert.Equal(t, 12, workflow["30"].(map[string]any)["inputs"].(map[string]any)["value"])
	assert.Equal(t, []any{"20", 1}, workflow["10"].(map[string]any)["inputs"].(map[string]any)["length"])
	assert.Equal(t, 24, workflow["40"].(map[string]any)["inputs"].(map[string]any)["fps"])

	explicitFrames := mergeNodeMappings(mapping, dto.ComfyUINodeMappings{FramesNodeID: "10", FramesInput: "length"})
	err = (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Duration: 6, Fps: 24},
		comfyMetadata{},
		explicitFrames,
		&relaycommon.RelayInfo{},
	)
	require.NoError(t, err)
	assert.Equal(t, 144, workflow["10"].(map[string]any)["inputs"].(map[string]any)["length"])
}

func TestApplyReferenceMediaRebuildsMiniMaxH3Inputs(t *testing.T) {
	workflow := miniMaxH3ReferenceWorkflow()
	uploads := make([]string, 0)
	adaptor := &TaskAdaptor{
		uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, source, mediaType string) (string, error) {
			uploads = append(uploads, mediaType+":"+source)
			return mediaType + "-" + strconv.Itoa(len(uploads)), nil
		},
	}
	metadata := comfyMetadata{
		ReferenceImages: []string{"https://example.com/one.png", "https://example.com/two.png"},
		ReferenceVideos: []string{"https://example.com/one.mp4"},
		ReferenceAudios: []string{"https://example.com/one.mp3"},
	}

	handled, err := adaptor.applyReferenceMediaToWorkflow(nil, workflow, relaycommon.TaskSubmitReq{}, metadata, &relaycommon.RelayInfo{})
	require.NoError(t, err)
	assert.True(t, handled)
	assert.Equal(t, []string{
		"image:https://example.com/one.png",
		"image:https://example.com/two.png",
		"video:https://example.com/one.mp4",
		"audio:https://example.com/one.mp3",
	}, uploads)
	assertWorkflowDoesNotContainInputValue(t, workflow, "red_superboy_on_city_roof.png")
	assertWorkflowDoesNotContainInputValue(t, workflow, "mecha_dragon_lightning.png")

	targetInputs := workflow["136"].(map[string]any)["inputs"].(map[string]any)
	assert.Equal(t, "keep", targetInputs["prompt"])
	assertWorkflowConnectionClass(t, workflow, targetInputs["ref_images.ref_image_0"], "LoadImage", 0)
	assertWorkflowConnectionClass(t, workflow, targetInputs["ref_images.ref_image_1"], "LoadImage", 0)
	videoComponentsID := assertWorkflowConnectionClass(t, workflow, targetInputs["ref_videos.ref_video_0"], "GetVideoComponents", 0)
	assert.Equal(t, []any{videoComponentsID, 1}, targetInputs["ref_video_audios.ref_video_audio_0"])
	componentsInputs := workflow[videoComponentsID].(map[string]any)["inputs"].(map[string]any)
	assertWorkflowConnectionClass(t, workflow, componentsInputs["video"], "LoadVideo", 0)
	assertWorkflowConnectionClass(t, workflow, targetInputs["ref_audios.ref_audio_0"], "LoadAudio", 0)
}

func TestApplyRequestRejectsTopLevelFirstFrameForReferenceOnlyWorkflow(t *testing.T) {
	workflow := miniMaxH3ReferenceWorkflow()
	adaptor := &TaskAdaptor{
		uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, source, _ string) (string, error) {
			return source, nil
		},
	}
	req := relaycommon.TaskSubmitReq{
		Image: "https://example.com/first.png",
	}
	metadata := comfyMetadata{
		ReferenceVideos: []string{"https://example.com/reference.mp4"},
	}

	err := adaptor.applyRequestToWorkflow(
		nil,
		workflow,
		req,
		metadata,
		dto.ComfyUINodeMappings{},
		&relaycommon.RelayInfo{},
	)

	require.ErrorContains(t, err, "cannot consume top-level image as a first frame")
}

func TestApplyRequestLeavesWorkflowDurationForAuto(t *testing.T) {
	workflow := map[string]any{
		"1": map[string]any{
			"class_type": "VideoNode",
			"inputs":     map[string]any{"duration": 12},
		},
	}
	req := relaycommon.TaskSubmitReq{DurationAuto: true}
	mapping := dto.ComfyUINodeMappings{DurationNodeID: "1", DurationInput: "duration"}

	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		req,
		comfyMetadata{},
		mapping,
		&relaycommon.RelayInfo{},
	)

	require.NoError(t, err)
	assert.Equal(t, 12, workflow["1"].(map[string]any)["inputs"].(map[string]any)["duration"])
}

func TestApplyReferenceMediaSupportsDeclaredMaximums(t *testing.T) {
	workflow := miniMaxH3ReferenceWorkflow()
	adaptor := &TaskAdaptor{
		uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, source, _ string) (string, error) {
			return source, nil
		},
	}
	metadata := comfyMetadata{
		ReferenceImages: numberedReferences("image", 9),
		ReferenceVideos: numberedReferences("video", 3),
		ReferenceAudios: numberedReferences("audio", 3),
	}

	_, err := adaptor.applyReferenceMediaToWorkflow(nil, workflow, relaycommon.TaskSubmitReq{}, metadata, &relaycommon.RelayInfo{})
	require.NoError(t, err)
	inputs := workflow["136"].(map[string]any)["inputs"].(map[string]any)
	for index := 0; index < 9; index++ {
		assert.Contains(t, inputs, "ref_images.ref_image_"+strconv.Itoa(index))
	}
	for index := 0; index < 3; index++ {
		assert.Contains(t, inputs, "ref_videos.ref_video_"+strconv.Itoa(index))
		assert.Contains(t, inputs, "ref_video_audios.ref_video_audio_"+strconv.Itoa(index))
		assert.Contains(t, inputs, "ref_audios.ref_audio_"+strconv.Itoa(index))
	}
}

func TestApplyReferenceMediaPreservesExplicitDuplicateOrder(t *testing.T) {
	workflow := miniMaxH3ReferenceWorkflow()
	uploads := make([]string, 0)
	adaptor := &TaskAdaptor{
		uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, source, _ string) (string, error) {
			uploads = append(uploads, source)
			return source, nil
		},
	}
	metadata := comfyMetadata{ReferenceImages: []string{"A", "A", "B"}}

	_, err := adaptor.applyReferenceMediaToWorkflow(nil, workflow, relaycommon.TaskSubmitReq{}, metadata, &relaycommon.RelayInfo{})

	require.NoError(t, err)
	assert.Equal(t, []string{"A", "A", "B"}, uploads)
	inputs := workflow["136"].(map[string]any)["inputs"].(map[string]any)
	assert.Contains(t, inputs, "ref_images.ref_image_2")
}

func TestApplyReferenceMediaRejectsCountsAboveNodeDeclaration(t *testing.T) {
	tests := []struct {
		name     string
		metadata comfyMetadata
		message  string
	}{
		{"images", comfyMetadata{ReferenceImages: numberedReferences("image", 10)}, "reference_images supports at most 9"},
		{"videos", comfyMetadata{ReferenceVideos: numberedReferences("video", 4)}, "reference_videos supports at most 3"},
		{"audios", comfyMetadata{ReferenceAudios: numberedReferences("audio", 4)}, "reference_audios supports at most 3"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			uploaded := false
			adaptor := &TaskAdaptor{
				uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, _, _ string) (string, error) {
					uploaded = true
					return "", nil
				},
			}
			_, err := adaptor.applyReferenceMediaToWorkflow(nil, miniMaxH3ReferenceWorkflow(), relaycommon.TaskSubmitReq{}, test.metadata, &relaycommon.RelayInfo{})
			require.ErrorContains(t, err, test.message)
			assert.False(t, uploaded)
		})
	}
}

func TestApplyReferenceMediaRejectsAudioOnlyReference(t *testing.T) {
	uploaded := false
	adaptor := &TaskAdaptor{
		uploadInput: func(_ *gin.Context, _ *relaycommon.RelayInfo, _, _ string) (string, error) {
			uploaded = true
			return "", nil
		},
	}
	metadata := comfyMetadata{ReferenceAudios: []string{"https://example.com/reference.mp3"}}

	_, err := adaptor.applyReferenceMediaToWorkflow(
		nil,
		miniMaxH3ReferenceWorkflow(),
		relaycommon.TaskSubmitReq{},
		metadata,
		&relaycommon.RelayInfo{},
	)

	require.ErrorContains(t, err, "reference_audios cannot be used alone")
	assert.False(t, uploaded)
}

func TestReferenceValidationErrorsAreStructuredBadRequests(t *testing.T) {
	err := validateReferenceCounts(
		referenceWorkflowSpec{imageLimit: 9, videoLimit: 3, audioLimit: 3},
		numberedReferences("image", 10),
		nil,
		nil,
	)

	var validationErr *referenceValidationError
	require.ErrorAs(t, err, &validationErr)
	assert.Equal(t, http.StatusBadRequest, validationErr.TaskErrorStatusCode())
	assert.Equal(t, "invalid_request", validationErr.TaskErrorCode())
	assert.True(t, validationErr.TaskErrorLocal())
	assert.Equal(t, map[string]any{"field": "reference_images", "count": 10, "limit": 9}, validationErr.TaskErrorData())
}

func TestRemoveExistingReferenceInputsKeepsSharedLoader(t *testing.T) {
	workflow := miniMaxH3ReferenceWorkflow()
	workflow["200"] = map[string]any{
		"class_type": "PreviewImage",
		"inputs":     map[string]any{"images": []any{"139", 0}},
	}
	targetInputs := workflow["136"].(map[string]any)["inputs"].(map[string]any)

	removeExistingReferenceInputs(workflow, "136", targetInputs)

	assert.NotContains(t, workflow, "137")
	assert.Contains(t, workflow, "139")
	assert.NotContains(t, targetInputs, "ref_images.ref_image_0")
	assert.NotContains(t, targetInputs, "ref_images.ref_image_1")
}

func TestRemoveUnusedReferenceLoadersRemovesDisconnectedChains(t *testing.T) {
	workflow := miniMaxH3ReferenceWorkflow()
	workflow["150"] = map[string]any{
		"class_type": "LoadImage",
		"inputs":     map[string]any{"image": "unused.png"},
	}
	workflow["151"] = map[string]any{
		"class_type": "LoadVideo",
		"inputs":     map[string]any{"file": "unused.mp4"},
	}
	workflow["152"] = map[string]any{
		"class_type": "GetVideoComponents",
		"inputs":     map[string]any{"video": []any{"151", 0}},
	}
	workflow["160"] = map[string]any{
		"class_type": "LoadAudio",
		"inputs":     map[string]any{"audio": "used.mp3"},
	}
	workflow["161"] = map[string]any{
		"class_type": "AudioPreview",
		"inputs":     map[string]any{"audio": []any{"160", 0}},
	}

	removeUnusedReferenceLoaders(workflow, "136")

	assert.NotContains(t, workflow, "150")
	assert.NotContains(t, workflow, "151")
	assert.NotContains(t, workflow, "152")
	assert.Contains(t, workflow, "160")
	assert.Contains(t, workflow, "161")
}

func TestComfyUIInputFilenamesAreUniqueAndKeepMediaExtension(t *testing.T) {
	first := uniqueComfyUIInputFilename("relayclaw-input.png", "image")
	second := uniqueComfyUIInputFilename("relayclaw-input.png", "image")

	assert.NotEqual(t, first, second)
	assert.Equal(t, ".png", filepath.Ext(first))
	assert.Equal(t, ".png", filepath.Ext(second))
}

func TestFilenameFromURLUsesContentTypeForUnreliableExtension(t *testing.T) {
	tests := []struct {
		name        string
		url         string
		mediaType   string
		contentType string
		expected    string
	}{
		{"no extension", "https://example.com/media/download?id=123", "video", "video/mp4", "download.mp4"},
		{"wrong extension", "https://example.com/media/file.php?id=123", "audio", "audio/mpeg", "file.mp3"},
		{"valid extension", "https://example.com/media/input.mov?token=x", "video", "application/octet-stream", "input.mov"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			assert.Equal(t, test.expected, filenameFromURLWithFallback(test.url, test.mediaType, test.contentType))
		})
	}
}

func TestReadMediaInputValidatesDataURLType(t *testing.T) {
	previousLimit := constant.MaxFileDownloadMB
	constant.MaxFileDownloadMB = 1
	t.Cleanup(func() {
		constant.MaxFileDownloadMB = previousLimit
	})
	payload := base64.StdEncoding.EncodeToString([]byte("media"))
	filename, data, err := readMediaInput(nil, nil, "data:video/mp4;base64,"+payload, "video")
	require.NoError(t, err)
	assert.Equal(t, "relayclaw-input.mp4", filename)
	assert.Equal(t, []byte("media"), data)

	_, _, err = readMediaInput(nil, nil, "data:text/plain;base64,"+payload, "audio")
	require.ErrorContains(t, err, "invalid audio data URL content type")
}

func miniMaxH3ReferenceWorkflow() map[string]any {
	return map[string]any{
		"136": map[string]any{
			"class_type": miniMaxH3ReferenceNode,
			"inputs": map[string]any{
				"prompt":                 "keep",
				"ref_images.ref_image_0": []any{"137", 0},
				"ref_images.ref_image_1": []any{"139", 0},
			},
		},
		"137": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "red_superboy_on_city_roof.png"},
		},
		"139": map[string]any{
			"class_type": "LoadImage",
			"inputs":     map[string]any{"image": "mecha_dragon_lightning.png"},
		},
	}
}

func assertWorkflowConnectionClass(t *testing.T, workflow map[string]any, raw any, classType string, output int) string {
	t.Helper()
	connection, ok := raw.([]any)
	require.True(t, ok)
	require.Len(t, connection, 2)
	nodeID := fmt.Sprint(connection[0])
	assert.Equal(t, output, connection[1])
	node, ok := workflow[nodeID].(map[string]any)
	require.True(t, ok)
	assert.Equal(t, classType, node["class_type"])
	return nodeID
}

func assertWorkflowDoesNotContainInputValue(t *testing.T, workflow map[string]any, unwanted any) {
	t.Helper()
	for nodeID, raw := range workflow {
		node, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		inputs, _ := node["inputs"].(map[string]any)
		for inputName, value := range inputs {
			assert.NotEqual(t, unwanted, value, "workflow node %s input %s still contains sample media", nodeID, inputName)
		}
	}
}

func numberedReferences(prefix string, count int) []string {
	values := make([]string, count)
	for index := range values {
		values[index] = prefix + "-" + strconv.Itoa(index+1)
	}
	return values
}

func TestComfyUIWorkflowRejectsUnmappedPrompt(t *testing.T) {
	workflow := map[string]any{
		"1": map[string]any{"class_type": "SaveVideo", "inputs": map[string]any{}},
	}
	err := (&TaskAdaptor{}).applyRequestToWorkflow(
		nil,
		workflow,
		relaycommon.TaskSubmitReq{Prompt: "must not be ignored"},
		comfyMetadata{},
		dto.ComfyUINodeMappings{},
		&relaycommon.RelayInfo{},
	)
	require.ErrorContains(t, err, "prompt input could not be inferred")
	assertTaskBuildError(t, err, "comfyui_configuration_error", http.StatusInternalServerError)
}

func assertTaskBuildError(t *testing.T, err error, code string, statusCode int) {
	t.Helper()
	metadata, ok := err.(interface {
		TaskErrorCode() string
		TaskErrorStatusCode() int
		TaskErrorLocal() bool
	})
	require.True(t, ok)
	assert.Equal(t, code, metadata.TaskErrorCode())
	assert.Equal(t, statusCode, metadata.TaskErrorStatusCode())
	assert.True(t, metadata.TaskErrorLocal())
}

func TestParseComfyUIHistoryVideoOutput(t *testing.T) {
	body := []byte(`{
		"prompt-1": {
			"status": {"status_str": "success", "completed": true},
			"outputs": {
				"9": {
					"videos": [
						{"filename": "out.mp4", "subfolder": "videos", "type": "output"}
					]
				}
			}
		}
	}`)

	wrapped := wrapHistoryResponse("prompt-1", "http://127.0.0.1:8188", body)
	require.Equal(t, "succeeded", wrapped.Status)
	assert.Equal(t, "http://127.0.0.1:8188/view?filename=out.mp4&subfolder=videos&type=output", wrapped.URL)

	data, err := common.Marshal(wrapped)
	require.NoError(t, err)
	info, err := (&TaskAdaptor{}).ParseTaskResult(data)
	require.NoError(t, err)
	assert.Equal(t, "SUCCESS", info.Status)
	assert.Empty(t, info.Url)
	assert.Equal(t, wrapped.URL, info.RemoteUrl)
}

func TestFirstOutputViewURLUsesStableNodeOrder(t *testing.T) {
	entry := map[string]any{
		"outputs": map[string]any{
			"20": map[string]any{"videos": []any{map[string]any{"filename": "second.mp4"}}},
			"10": map[string]any{"videos": []any{map[string]any{"filename": "first.mp4"}}},
		},
	}

	result := firstOutputViewURL(entry, "http://127.0.0.1:8188")
	assert.Equal(t, "http://127.0.0.1:8188/view?filename=first.mp4&type=output", result)
}

func TestFetchTaskUsesChannelBaseURLForInternalResultURL(t *testing.T) {
	body := []byte(`{
		"prompt-1": {
			"status": {"status_str": "success", "completed": true},
			"outputs": {
				"9": {
					"videos": [
						{"filename": "out.mp4", "subfolder": "videos", "type": "output"}
					]
				}
			}
		}
	}`)

	adaptor := &TaskAdaptor{
		settings: dto.ComfyUISettings{OutputBaseURL: "http://public.example"},
	}
	wrapped := wrapHistoryResponse("prompt-1", "http://127.0.0.1:8188", body)

	require.Equal(t, "succeeded", wrapped.Status)
	assert.Equal(t, "http://127.0.0.1:8188/view?filename=out.mp4&subfolder=videos&type=output", wrapped.URL)
	assert.NotContains(t, wrapped.URL, adaptor.settings.OutputBaseURL)
}

func TestParseComfyUIHistoryPendingWhenNoEntry(t *testing.T) {
	wrapped := wrapHistoryResponse("prompt-1", "http://127.0.0.1:8188", []byte(`{}`))

	assert.Equal(t, "running", wrapped.Status)
	data, err := common.Marshal(wrapped)
	require.NoError(t, err)
	info, err := (&TaskAdaptor{}).ParseTaskResult(data)
	require.NoError(t, err)
	assert.Equal(t, "IN_PROGRESS", info.Status)
}

func TestParseComfyUIHistoryFailsWhenCompletedWithoutOutput(t *testing.T) {
	body := []byte(`{
		"prompt-1": {
			"status": {"status_str": "success", "completed": true},
			"outputs": {}
		}
	}`)

	wrapped := wrapHistoryResponse("prompt-1", "http://127.0.0.1:8188", body)
	require.Equal(t, "failed", wrapped.Status)
	assert.Contains(t, wrapped.Reason, "without output media")
}

func TestWrapComfyUIHistoryHTTPFailure(t *testing.T) {
	wrapped := wrapHistoryFailure("prompt-1", "http://127.0.0.1:8188", "ComfyUI history failed: HTTP 404")

	data, err := common.Marshal(wrapped)
	require.NoError(t, err)
	info, err := (&TaskAdaptor{}).ParseTaskResult(data)
	require.NoError(t, err)
	assert.Equal(t, "FAILURE", info.Status)
	assert.Contains(t, info.Reason, "HTTP 404")
}

func TestComfyUIQueueTaskLocation(t *testing.T) {
	body := []byte(`{"queue_running":[[1,"running-id",{}]],"queue_pending":[[2,"pending-id",{}]]}`)

	location, err := comfyUIQueueTaskLocation(body, "pending-id")
	require.NoError(t, err)
	assert.Equal(t, "pending", location)
	location, err = comfyUIQueueTaskLocation(body, "running-id")
	require.NoError(t, err)
	assert.Equal(t, "running", location)
	location, err = comfyUIQueueTaskLocation(body, "missing-id")
	require.NoError(t, err)
	assert.Empty(t, location)
}
