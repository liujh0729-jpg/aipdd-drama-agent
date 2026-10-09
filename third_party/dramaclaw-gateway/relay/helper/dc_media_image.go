package helper

import (
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/relaykit/dto"
)

func normalizeDCMediaImageRequest(request *dto.ImageRequest) error {
	if request == nil {
		return fmt.Errorf("image request is required")
	}
	if request.Width < 0 || request.Height < 0 || (request.Width == 0) != (request.Height == 0) {
		return fmt.Errorf("width and height must be positive and provided together")
	}
	ratio := imageMetadataString(request.Metadata, "ratio")
	if strings.EqualFold(ratio, "adaptive") {
		ratio = "auto"
	}
	if ratio == "auto" && (request.Width > 0 || strings.TrimSpace(request.Size) != "") {
		return fmt.Errorf("fixed dimensions cannot be combined with metadata.ratio=auto")
	}
	if request.Width > 0 {
		if ratio != "" && !imageDimensionsMatchRatio(request.Width, request.Height, ratio) {
			return fmt.Errorf("width and height do not match metadata.ratio")
		}
		request.Size = fmt.Sprintf("%dx%d", request.Width, request.Height)
	}
	return nil
}

func imageMetadataString(metadata map[string]any, key string) string {
	value, _ := metadata[key].(string)
	return strings.ToLower(strings.TrimSpace(value))
}

func imageDimensionsMatchRatio(width, height int, ratio string) bool {
	parts := strings.Split(ratio, ":")
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
