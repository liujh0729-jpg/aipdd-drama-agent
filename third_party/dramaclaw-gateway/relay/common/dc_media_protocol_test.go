package common

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNormalizeDCMediaTaskRequestCanonicalizesCompatibilityFields(t *testing.T) {
	req := TaskSubmitReq{
		Size: "1920x1080",
		Metadata: map[string]interface{}{
			"aspect_ratio":  "adaptive",
			"resolution":    "1080P",
			"image_url":     " https://example.com/first.png ",
			"end_image_url": " https://example.com/last.png ",
		},
	}

	require.NoError(t, NormalizeDCMediaTaskRequest(&req))
	assert.Equal(t, "https://example.com/first.png", req.Image)
	assert.Equal(t, 1920, req.Width)
	assert.Equal(t, 1080, req.Height)
	assert.Equal(t, "auto", req.Metadata["ratio"])
	assert.Equal(t, "1080p", req.Metadata["resolution"])
	assert.Equal(t, "https://example.com/last.png", req.Metadata["last_frame_image"])
}

func TestValidateDCMediaTaskRequestClassifiesMaterialShapes(t *testing.T) {
	tests := []struct {
		name      string
		req       TaskSubmitReq
		wantShape DCMediaCallShape
	}{
		{name: "text", req: TaskSubmitReq{}, wantShape: DCMediaTextToVideo},
		{name: "first frame", req: TaskSubmitReq{Image: "first.png"}, wantShape: DCMediaFirstFrame},
		{name: "last frame", req: TaskSubmitReq{Metadata: map[string]interface{}{"last_frame_image": "last.png"}}, wantShape: DCMediaFirstLastFrame},
		{name: "single image", req: TaskSubmitReq{Metadata: map[string]interface{}{"reference_images": []interface{}{"one.png"}}}, wantShape: DCMediaImageToVideo},
		{name: "multiple images", req: TaskSubmitReq{Metadata: map[string]interface{}{"reference_images": []string{"one.png", "two.png"}}}, wantShape: DCMediaImageReference},
		{name: "reference video", req: TaskSubmitReq{Metadata: map[string]interface{}{"reference_videos": []string{"one.mp4"}}}, wantShape: DCMediaAllReference},
		{name: "reference file", req: TaskSubmitReq{Metadata: map[string]interface{}{"reference_file": " brief.pdf "}}, wantShape: DCMediaAllReference},
		{name: "reference link", req: TaskSubmitReq{Metadata: map[string]interface{}{"reference_link": " https://example.com/article "}}, wantShape: DCMediaAllReference},
		{name: "video edit", req: TaskSubmitReq{DurationAuto: true, DurationSet: true, Metadata: map[string]interface{}{"ratio": "auto", "reference_videos": []string{"one.mp4"}}}, wantShape: DCMediaVideoEdit},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			shape, err := ValidateDCMediaTaskRequest(&test.req)
			require.NoError(t, err)
			assert.Equal(t, test.wantShape, shape)
		})
	}
}

func TestValidateDCMediaTaskRequestRejectsAmbiguousCombinations(t *testing.T) {
	tests := []struct {
		name string
		req  TaskSubmitReq
	}{
		{name: "first frame and references", req: TaskSubmitReq{Image: "first.png", Metadata: map[string]interface{}{"reference_images": []string{"ref.png"}}}},
		{name: "last frame and references", req: TaskSubmitReq{Metadata: map[string]interface{}{"last_frame_image": "last.png", "reference_videos": []string{"ref.mp4"}}}},
		{name: "auto ratio and dimensions", req: TaskSubmitReq{Width: 1280, Height: 720, Metadata: map[string]interface{}{"ratio": "auto"}}},
		{name: "auto duration without video", req: TaskSubmitReq{DurationAuto: true, DurationSet: true, Metadata: map[string]interface{}{"ratio": "auto", "reference_images": []string{"ref.png"}}}},
		{name: "video edit with file", req: TaskSubmitReq{DurationAuto: true, DurationSet: true, Metadata: map[string]interface{}{"ratio": "auto", "reference_videos": []string{"ref.mp4"}, "reference_file": "brief.pdf"}}},
		{name: "video edit with link", req: TaskSubmitReq{DurationAuto: true, DurationSet: true, Metadata: map[string]interface{}{"ratio": "auto", "reference_videos": []string{"ref.mp4"}, "reference_link": "https://example.com/article"}}},
		{name: "file and link", req: TaskSubmitReq{Metadata: map[string]interface{}{"reference_file": "brief.pdf", "reference_link": "https://example.com/article"}}},
		{name: "first frame and file", req: TaskSubmitReq{Image: "first.png", Metadata: map[string]interface{}{"reference_file": "brief.pdf"}}},
		{name: "last frame and link", req: TaskSubmitReq{Metadata: map[string]interface{}{"last_frame_image": "last.png", "reference_link": "https://example.com/article"}}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := ValidateDCMediaTaskRequest(&test.req)
			require.Error(t, err)
		})
	}
}

func TestNormalizeDCMediaTaskRequestTrimsReferenceFileAndLink(t *testing.T) {
	req := TaskSubmitReq{Metadata: map[string]interface{}{
		"reference_file": " https://example.com/brief.pdf ",
		"reference_link": " https://example.com/article ",
	}}

	require.NoError(t, NormalizeDCMediaTaskRequest(&req))
	assert.Equal(t, "https://example.com/brief.pdf", req.Metadata["reference_file"])
	assert.Equal(t, "https://example.com/article", req.Metadata["reference_link"])
}

func TestValidateDCMediaTaskRequestAllowsRoundedDimensions(t *testing.T) {
	req := TaskSubmitReq{Width: 854, Height: 480, Metadata: map[string]interface{}{"ratio": "16:9"}}
	shape, err := ValidateDCMediaTaskRequest(&req)
	require.NoError(t, err)
	assert.Equal(t, DCMediaTextToVideo, shape)
}

func TestValidateDCMediaTaskRequestAllowsResolutionTierWithAutoRatio(t *testing.T) {
	req := TaskSubmitReq{Size: "720p", Metadata: map[string]interface{}{"ratio": "auto"}}
	shape, err := ValidateDCMediaTaskRequest(&req)
	require.NoError(t, err)
	assert.Equal(t, DCMediaTextToVideo, shape)
}

func TestValidateDCMediaTaskRequestReturnsStableErrorCode(t *testing.T) {
	req := TaskSubmitReq{Image: "first.png", Metadata: map[string]interface{}{"reference_images": []string{"ref.png"}}}
	_, err := ValidateDCMediaTaskRequest(&req)
	require.Error(t, err)
	assert.Equal(t, "conflicting_media_inputs", DCMediaValidationErrorCode(err))
}

func TestTaskSubmitReqParsesAutomaticDuration(t *testing.T) {
	var req TaskSubmitReq
	require.NoError(t, req.UnmarshalJSON([]byte(`{"duration":"auto"}`)))
	assert.True(t, req.DurationSet)
	assert.True(t, req.DurationAuto)
	assert.Zero(t, req.Duration)
}

func TestTaskSubmitReqTracksExplicitImagesField(t *testing.T) {
	var req TaskSubmitReq
	require.NoError(t, req.UnmarshalJSON([]byte(`{"images":["https://example.com/ref.png"]}`)))
	assert.True(t, req.ImagesSet)
	assert.Equal(t, []string{"https://example.com/ref.png"}, req.Images)
}

func TestValidateDCMediaTaskRequestRejectsExplicitImagesField(t *testing.T) {
	var req TaskSubmitReq
	require.NoError(t, req.UnmarshalJSON([]byte(`{"images":["https://example.com/ref.png"]}`)))

	_, err := ValidateDCMediaTaskRequest(&req)

	require.Error(t, err)
	assert.Equal(t, "unsupported_media_field", DCMediaValidationErrorCode(err))
	assert.Contains(t, err.Error(), "metadata.reference_images")
}

func TestValidateDCMediaTaskRequestAcceptsInternallyMirroredFirstFrame(t *testing.T) {
	req := TaskSubmitReq{Image: "first.png", Images: []string{"first.png"}}

	shape, err := ValidateDCMediaTaskRequest(&req)

	require.NoError(t, err)
	assert.Equal(t, DCMediaFirstFrame, shape)
}

func TestNormalizeDCMediaTaskRequestParsesCompatibleDimensionSeparators(t *testing.T) {
	for _, size := range []string{"832*480", "1024×1024", "1280X720"} {
		t.Run(size, func(t *testing.T) {
			req := TaskSubmitReq{Size: size}
			require.NoError(t, NormalizeDCMediaTaskRequest(&req))
			assert.Positive(t, req.Width)
			assert.Positive(t, req.Height)
		})
	}
}

func TestValidateDCMediaTaskRequestRejectsAutoRatioWithCompatibleFixedSize(t *testing.T) {
	for _, size := range []string{"832*480", "1024×1024"} {
		t.Run(size, func(t *testing.T) {
			req := TaskSubmitReq{Size: size, Metadata: map[string]interface{}{"ratio": "auto"}}
			_, err := ValidateDCMediaTaskRequest(&req)
			require.Error(t, err)
			assert.Equal(t, "conflicting_dimensions", DCMediaValidationErrorCode(err))
		})
	}
}

func TestValidateBasicTaskRequestStoresCanonicalDCMediaRequest(t *testing.T) {
	gin.SetMode(gin.TestMode)
	request := httptest.NewRequest(http.MethodPost, "/v1/video/generations", strings.NewReader(`{
		"model":"example-video",
		"prompt":"animate",
		"duration":5,
		"width":854,
		"height":480,
		"metadata":{
			"aspect_ratio":"16:9",
			"resolution":"480P",
			"reference_images":["https://example.com/ref.png"]
		}
	}`))
	request.Header.Set("Content-Type", "application/json")
	context, _ := gin.CreateTestContext(httptest.NewRecorder())
	context.Request = request
	info := &RelayInfo{TaskRelayInfo: &TaskRelayInfo{}}

	taskErr := ValidateBasicTaskRequest(context, info, constant.TaskActionGenerate)
	require.Nil(t, taskErr)
	stored, err := GetTaskRequest(context)
	require.NoError(t, err)
	assert.Equal(t, 5, stored.Duration)
	assert.Empty(t, stored.Seconds)
	assert.Equal(t, "16:9", stored.Metadata["ratio"])
	assert.Equal(t, "480p", stored.Metadata["resolution"])
	assert.Equal(t, 1, stored.N)
	assert.Equal(t, "url", stored.ResponseFormat)
}

func TestValidateBasicTaskRequestNormalizesSecondsIntoDuration(t *testing.T) {
	gin.SetMode(gin.TestMode)
	request := httptest.NewRequest(http.MethodPost, "/v1/video/generations", strings.NewReader(`{
		"model":"seedance-test",
		"prompt":"animate",
		"seconds":"10"
	}`))
	request.Header.Set("Content-Type", "application/json")
	context, _ := gin.CreateTestContext(httptest.NewRecorder())
	context.Request = request

	taskErr := ValidateBasicTaskRequest(context, &RelayInfo{TaskRelayInfo: &TaskRelayInfo{}}, constant.TaskActionGenerate)
	require.Nil(t, taskErr)
	stored, err := GetTaskRequest(context)
	require.NoError(t, err)
	assert.Equal(t, 10, stored.Duration)
	assert.True(t, stored.DurationSet)
	assert.Empty(t, stored.Seconds)
}

func TestValidateBasicTaskRequestReturnsDCMediaErrorCode(t *testing.T) {
	gin.SetMode(gin.TestMode)
	request := httptest.NewRequest(http.MethodPost, "/v1/video/generations", strings.NewReader(`{
		"model":"example-video",
		"prompt":"animate",
		"image":"https://example.com/first.png",
		"metadata":{"reference_images":["https://example.com/ref.png"]}
	}`))
	request.Header.Set("Content-Type", "application/json")
	context, _ := gin.CreateTestContext(httptest.NewRecorder())
	context.Request = request

	taskErr := ValidateBasicTaskRequest(context, &RelayInfo{TaskRelayInfo: &TaskRelayInfo{}}, constant.TaskActionGenerate)
	require.NotNil(t, taskErr)
	assert.Equal(t, "conflicting_media_inputs", taskErr.Code)
}
