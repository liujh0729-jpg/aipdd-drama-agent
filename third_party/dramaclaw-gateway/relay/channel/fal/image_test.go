package fal

import (
	"bytes"
	"encoding/base64"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestBuildFalImageRequestRoutesGenericTextModel(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2,
		},
	}

	got, err := buildFalImageRequest(c, info, dto.ImageRequest{
		Model:  ModelGPTImage2,
		Prompt: "draw a cat",
	})
	if err != nil {
		t.Fatalf("buildFalImageRequest returned error: %v", err)
	}
	if info.UpstreamModelName != ModelGPTImage2TextID {
		t.Fatalf("UpstreamModelName = %q, want %q", info.UpstreamModelName, ModelGPTImage2TextID)
	}
	body := got.(map[string]any)
	if body["prompt"] != "draw a cat" {
		t.Fatalf("prompt = %#v", body["prompt"])
	}
	if _, ok := body["image_urls"]; ok {
		t.Fatalf("image_urls should be absent for text model: %#v", body)
	}
}

func TestBuildFalImageRequestUsesSyncModeForBase64Response(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2,
		},
	}

	got, err := buildFalImageRequest(c, info, dto.ImageRequest{
		Model:          ModelGPTImage2,
		Prompt:         "draw a cat",
		ResponseFormat: "b64_json",
	})
	if err != nil {
		t.Fatalf("buildFalImageRequest returned error: %v", err)
	}
	body := got.(map[string]any)
	if body["sync_mode"] != true {
		t.Fatalf("sync_mode = %#v, want true", body["sync_mode"])
	}
}

func TestBuildFalImageRequestRoutesGenericEditModel(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	imageRaw, _ := common.Marshal([]string{"https://example.com/ref.png"})
	extra, _ := common.Marshal(map[string]any{
		"aspect_ratio": "2:1",
		"resolution":   "2k",
		"image_size":   "2K",
		"quality":      "medium",
	})
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2,
		},
	}

	got, err := buildFalImageRequest(c, info, dto.ImageRequest{
		Model:       ModelGPTImage2,
		Prompt:      "edit this image",
		Images:      imageRaw,
		ExtraFields: extra,
	})
	if err != nil {
		t.Fatalf("buildFalImageRequest returned error: %v", err)
	}
	if info.UpstreamModelName != ModelGPTImage2EditID {
		t.Fatalf("UpstreamModelName = %q, want %q", info.UpstreamModelName, ModelGPTImage2EditID)
	}
	body := got.(map[string]any)
	imageURLs, ok := body["image_urls"].([]string)
	if !ok || len(imageURLs) != 1 || imageURLs[0] != "https://example.com/ref.png" {
		t.Fatalf("image_urls = %#v", body["image_urls"])
	}
	size, ok := body["image_size"].(falImageSize)
	if !ok {
		t.Fatalf("image_size = %#v, want falImageSize", body["image_size"])
	}
	if size.Width != 2048 || size.Height != 1024 {
		t.Fatalf("image_size = %#v, want 2048x1024", size)
	}
	if body["quality"] != "medium" {
		t.Fatalf("quality = %#v", body["quality"])
	}
	if _, ok := body["aspect_ratio"]; ok {
		t.Fatalf("aspect_ratio should not be forwarded: %#v", body)
	}
	if _, ok := body["resolution"]; ok {
		t.Fatalf("resolution should not be forwarded: %#v", body)
	}
}

func TestFalConvertImageEditsRoutesToEditEndpoint(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	imageRaw, err := common.Marshal("https://example.com/ref.png")
	require.NoError(t, err)
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesEdits,
		OriginModelName: ModelGPTImage2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2,
		},
	}

	got, err := (&Adaptor{}).ConvertImageRequest(c, info, dto.ImageRequest{
		Model:  ModelGPTImage2,
		Prompt: "edit this image",
		Image:  imageRaw,
	})

	require.NoError(t, err)
	require.Equal(t, ModelGPTImage2EditID, info.UpstreamModelName)
	body, ok := got.(map[string]any)
	require.True(t, ok)
	require.Equal(t, []string{"https://example.com/ref.png"}, body["image_urls"])
}

func TestFalConvertImageEditsRequiresImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesEdits,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2,
		},
	}

	_, err := (&Adaptor{}).ConvertImageRequest(c, info, dto.ImageRequest{
		Model:  ModelGPTImage2,
		Prompt: "edit this image",
	})

	require.ErrorContains(t, err, "image is required")
}

func TestBuildFalImageRequestRejectsTextModelWithImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	imageRaw, _ := common.Marshal("https://example.com/ref.png")

	_, err := buildFalImageRequest(c, &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2Text,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2Text,
		},
	}, dto.ImageRequest{
		Model:  ModelGPTImage2Text,
		Prompt: "draw a cat",
		Image:  imageRaw,
	})
	if err == nil {
		t.Fatalf("buildFalImageRequest expected text-with-image error")
	}
}

func TestBuildFalImageRequestRejectsEditModelWithoutImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())

	_, err := buildFalImageRequest(c, &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2Edit,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2Edit,
		},
	}, dto.ImageRequest{
		Model:  ModelGPTImage2Edit,
		Prompt: "edit this image",
	})
	if err == nil {
		t.Fatalf("buildFalImageRequest expected edit-without-image error")
	}
}

func TestBuildFalImageRequestRejectsInvalidNumImages(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	n := uint(0)

	_, err := buildFalImageRequest(c, &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2,
		},
	}, dto.ImageRequest{
		Model:  ModelGPTImage2,
		Prompt: "draw a cat",
		N:      &n,
	})
	if err == nil {
		t.Fatalf("buildFalImageRequest expected invalid num_images error")
	}
}

func TestBuildFalImageRequestRoutesGenericEditFromExtraFields(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	extra, _ := common.Marshal(map[string]any{
		"image_urls": []string{"https://example.com/ref.png"},
		"mask_url":   "https://example.com/mask.png",
	})
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelGPTImage2,
		},
	}

	got, err := buildFalImageRequest(c, info, dto.ImageRequest{
		Model:       ModelGPTImage2,
		Prompt:      "edit this image",
		ExtraFields: extra,
	})
	if err != nil {
		t.Fatalf("buildFalImageRequest returned error: %v", err)
	}
	if info.UpstreamModelName != ModelGPTImage2EditID {
		t.Fatalf("UpstreamModelName = %q, want %q", info.UpstreamModelName, ModelGPTImage2EditID)
	}
	body := got.(map[string]any)
	imageURLs, ok := body["image_urls"].([]string)
	if !ok || len(imageURLs) != 1 || imageURLs[0] != "https://example.com/ref.png" {
		t.Fatalf("image_urls = %#v", body["image_urls"])
	}
	if body["mask_url"] != "https://example.com/mask.png" {
		t.Fatalf("mask_url = %#v", body["mask_url"])
	}
}

func TestBuildFalImageRequestRoutesNanoBananaTextModel(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	extra, _ := common.Marshal(map[string]any{
		"aspect_ratio":      "3:4",
		"resolution":        "2k",
		"quality":           "high",
		"safety_tolerance":  "5",
		"limit_generations": true,
	})
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelNanoBanana2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelNanoBanana2,
		},
	}
	n := uint(2)

	got, err := buildFalImageRequest(c, info, dto.ImageRequest{
		Model:       ModelNanoBanana2,
		Prompt:      "draw a cat",
		N:           &n,
		ExtraFields: extra,
	})
	if err != nil {
		t.Fatalf("buildFalImageRequest returned error: %v", err)
	}
	if info.UpstreamModelName != ModelNanoBanana2TextID {
		t.Fatalf("UpstreamModelName = %q, want %q", info.UpstreamModelName, ModelNanoBanana2TextID)
	}
	body := got.(map[string]any)
	if body["aspect_ratio"] != "3:4" {
		t.Fatalf("aspect_ratio = %#v", body["aspect_ratio"])
	}
	if body["resolution"] != "2K" {
		t.Fatalf("resolution = %#v", body["resolution"])
	}
	if body["num_images"] != 2 {
		t.Fatalf("num_images = %#v", body["num_images"])
	}
	if body["safety_tolerance"] != "5" {
		t.Fatalf("safety_tolerance = %#v", body["safety_tolerance"])
	}
	if body["limit_generations"] != true {
		t.Fatalf("limit_generations = %#v", body["limit_generations"])
	}
	if _, ok := body["quality"]; ok {
		t.Fatalf("quality should not be forwarded for nano-banana-2: %#v", body)
	}
	if _, ok := body["image_size"]; ok {
		t.Fatalf("image_size should not be forwarded for nano-banana-2: %#v", body)
	}
}

func TestBuildFalImageRequestRoutesNanoBananaEditModel(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	imageRaw, _ := common.Marshal([]string{"https://example.com/ref.png"})
	extra, _ := common.Marshal(map[string]any{
		"aspect_ratio": "auto",
		"image_size":   "4k",
		"mask_url":     "https://example.com/mask.png",
	})
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelNanoBanana2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelNanoBanana2,
		},
	}

	got, err := buildFalImageRequest(c, info, dto.ImageRequest{
		Model:       ModelNanoBanana2,
		Prompt:      "edit this image",
		Images:      imageRaw,
		ExtraFields: extra,
	})
	if err != nil {
		t.Fatalf("buildFalImageRequest returned error: %v", err)
	}
	if info.UpstreamModelName != ModelNanoBanana2EditID {
		t.Fatalf("UpstreamModelName = %q, want %q", info.UpstreamModelName, ModelNanoBanana2EditID)
	}
	body := got.(map[string]any)
	imageURLs, ok := body["image_urls"].([]string)
	if !ok || len(imageURLs) != 1 || imageURLs[0] != "https://example.com/ref.png" {
		t.Fatalf("image_urls = %#v", body["image_urls"])
	}
	if body["aspect_ratio"] != "auto" {
		t.Fatalf("aspect_ratio = %#v", body["aspect_ratio"])
	}
	if body["resolution"] != "4K" {
		t.Fatalf("resolution = %#v", body["resolution"])
	}
	if _, ok := body["mask_url"]; ok {
		t.Fatalf("mask_url should not be forwarded for nano-banana-2: %#v", body)
	}
}

func TestBuildFalImageRequestUsesStandardDimensionsAndMetadata(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	var request dto.ImageRequest
	require.NoError(t, common.Unmarshal([]byte(`{
		"model":"gpt-image-2",
		"prompt":"draw a cinematic landscape",
		"width":2048,
		"height":1152,
		"metadata":{"resolution":"2k"}
	}`), &request))
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelGPTImage2,
		ChannelMeta:     &relaycommon.ChannelMeta{UpstreamModelName: ModelGPTImage2},
	}

	got, err := buildFalImageRequest(c, info, request)

	require.NoError(t, err)
	body := got.(map[string]any)
	require.Equal(t, falImageSize{Width: 2048, Height: 1152}, body["image_size"])
	require.NotContains(t, body, "width")
	require.NotContains(t, body, "height")
	require.NotContains(t, body, "metadata")
}

func TestBuildFalNanoBananaRequestUsesAutoMetadata(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	var request dto.ImageRequest
	require.NoError(t, common.Unmarshal([]byte(`{
		"model":"nano-banana-2",
		"prompt":"follow the reference composition",
		"image":["https://example.com/reference.png"],
		"metadata":{"ratio":"auto","resolution":"2k"}
	}`), &request))
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesEdits,
		OriginModelName: ModelNanoBanana2,
		ChannelMeta:     &relaycommon.ChannelMeta{UpstreamModelName: ModelNanoBanana2},
	}

	got, err := buildFalImageRequest(c, info, request)

	require.NoError(t, err)
	body := got.(map[string]any)
	require.Equal(t, "auto", body["aspect_ratio"])
	require.Equal(t, "2K", body["resolution"])
	require.Equal(t, []string{"https://example.com/reference.png"}, body["image_urls"])
	require.NotContains(t, body, "metadata")
}

func TestBuildFalImageRequestRejectsNanoBananaTextWithImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	imageRaw, _ := common.Marshal("https://example.com/ref.png")

	_, err := buildFalImageRequest(c, &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelNanoBanana2Text,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelNanoBanana2Text,
		},
	}, dto.ImageRequest{
		Model:  ModelNanoBanana2Text,
		Prompt: "draw a cat",
		Image:  imageRaw,
	})
	if err == nil {
		t.Fatalf("buildFalImageRequest expected nano text-with-image error")
	}
}

func TestBuildFalImageRequestRejectsNanoBananaEditWithoutImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())

	_, err := buildFalImageRequest(c, &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelNanoBanana2Edit,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelNanoBanana2Edit,
		},
	}, dto.ImageRequest{
		Model:  ModelNanoBanana2Edit,
		Prompt: "edit this image",
	})
	if err == nil {
		t.Fatalf("buildFalImageRequest expected nano edit-without-image error")
	}
}

func TestBuildFalImageRequestDerivesNanoBananaAspectRatioFromSize(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	info := &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelNanoBanana2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelNanoBanana2,
		},
	}

	got, err := buildFalImageRequest(c, info, dto.ImageRequest{
		Model:  ModelNanoBanana2,
		Prompt: "draw a portrait",
		Size:   "768x1024",
	})
	if err != nil {
		t.Fatalf("buildFalImageRequest returned error: %v", err)
	}
	body := got.(map[string]any)
	if body["aspect_ratio"] != "3:4" {
		t.Fatalf("aspect_ratio = %#v", body["aspect_ratio"])
	}
}

func TestBuildFalImageRequestRejectsUnsupportedNanoBananaAspectRatio(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	extra, _ := common.Marshal(map[string]any{
		"aspect_ratio": "2:1",
	})

	_, err := buildFalImageRequest(c, &relaycommon.RelayInfo{
		RelayMode:       relayconstant.RelayModeImagesGenerations,
		OriginModelName: ModelNanoBanana2,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelNanoBanana2,
		},
	}, dto.ImageRequest{
		Model:       ModelNanoBanana2,
		Prompt:      "draw a panorama",
		ExtraFields: extra,
	})
	if err == nil {
		t.Fatalf("buildFalImageRequest expected unsupported nano aspect_ratio error")
	}
}

func TestHandleFalImageResponseURL(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/generations", nil)

	body, _ := common.Marshal(map[string]any{
		"images": []map[string]any{
			{
				"url":          "https://example.com/out.png",
				"width":        1024,
				"height":       1024,
				"content_type": "image/png",
				"file_name":    "out.png",
			},
		},
	})
	_, apiErr := handleFalImageResponse(c, &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(bytes.NewReader(body)),
	}, &relaycommon.RelayInfo{})
	if apiErr != nil {
		t.Fatalf("handleFalImageResponse returned error: %v", apiErr)
	}
	var got dto.ImageResponse
	if err := common.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	if len(got.Data) != 1 || got.Data[0].Url != "https://example.com/out.png" {
		t.Fatalf("image response data = %#v", got.Data)
	}
	if len(got.Metadata) == 0 {
		t.Fatalf("metadata should include fal image details")
	}
}

func TestHandleFalImageResponseDataURIToBase64(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/generations", nil)
	c.Set(contextKeyImageResponseFormat, "b64_json")

	payload := base64.StdEncoding.EncodeToString([]byte("image"))
	body, _ := common.Marshal(map[string]any{
		"images": []map[string]any{
			{"url": "data:image/png;base64," + payload},
		},
	})
	_, apiErr := handleFalImageResponse(c, &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(bytes.NewReader(body)),
	}, &relaycommon.RelayInfo{})
	if apiErr != nil {
		t.Fatalf("handleFalImageResponse returned error: %v", apiErr)
	}
	var got dto.ImageResponse
	if err := common.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	if len(got.Data) != 1 || got.Data[0].B64Json != payload {
		t.Fatalf("image response data = %#v", got.Data)
	}
}

func TestHandleFalImageResponseBase64KeepsURLForArchive(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/generations", nil)
	c.Set(contextKeyImageResponseFormat, "b64_json")

	sourceURL := "https://example.com/out.png"
	originalBase64Func := falImageSourceToBase64Func
	falImageSourceToBase64Func = func(source string) (string, error) {
		if source != sourceURL {
			t.Fatalf("base64 source = %q, want %q", source, sourceURL)
		}
		return base64.StdEncoding.EncodeToString([]byte("image")), nil
	}
	defer func() {
		falImageSourceToBase64Func = originalBase64Func
	}()

	body, _ := common.Marshal(map[string]any{
		"images": []map[string]any{
			{"url": sourceURL},
		},
	})
	_, apiErr := handleFalImageResponse(c, &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(bytes.NewReader(body)),
	}, &relaycommon.RelayInfo{})
	if apiErr != nil {
		t.Fatalf("handleFalImageResponse returned error: %v", apiErr)
	}

	var clientResp dto.ImageResponse
	if err := common.Unmarshal(recorder.Body.Bytes(), &clientResp); err != nil {
		t.Fatalf("unmarshal client response: %v", err)
	}
	if len(clientResp.Data) != 1 || clientResp.Data[0].B64Json == "" || clientResp.Data[0].Url != "" {
		t.Fatalf("client image response data = %#v", clientResp.Data)
	}

}
