package relay

import (
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/relay/channel"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

const ChannelTypeStatusEnabled = 1

type ChannelTypeMetadata struct {
	Type                    int      `json:"type"`
	Provider                string   `json:"provider"`
	Name                    string   `json:"name"`
	Description             string   `json:"description,omitempty"`
	Icon                    string   `json:"icon,omitempty"`
	DefaultBaseURL          string   `json:"default_base_url"`
	Status                  int      `json:"status"`
	Capabilities            []string `json:"capabilities,omitempty"`
	RequiresBaseURL         bool     `json:"requires_base_url"`
	SupportsBaseURLOverride bool     `json:"supports_base_url_override"`
}

var (
	channelTypeMetadataOnce sync.Once
	channelTypeMetadata     []ChannelTypeMetadata
)

// GetRegisteredChannelTypes discovers types from the same adaptor factories
// used by relay execution. Types without a concrete adaptor are omitted.
func GetRegisteredChannelTypes() []ChannelTypeMetadata {
	channelTypeMetadataOnce.Do(func() {
		items := make([]ChannelTypeMetadata, 0, constant.ChannelTypeDummy)
		for channelType := 1; channelType <= constant.ChannelTypeDummy; channelType++ {
			item, ok := buildChannelTypeMetadata(channelType)
			if ok {
				items = append(items, item)
			}
		}
		sort.Slice(items, func(i, j int) bool { return items[i].Type < items[j].Type })
		channelTypeMetadata = items
	})
	items := make([]ChannelTypeMetadata, len(channelTypeMetadata))
	for i, item := range channelTypeMetadata {
		items[i] = item
		items[i].Capabilities = append([]string(nil), item.Capabilities...)
	}
	return items
}

func buildChannelTypeMetadata(channelType int) (ChannelTypeMetadata, bool) {
	meta := &relaycommon.RelayInfo{ChannelMeta: &relaycommon.ChannelMeta{ChannelType: channelType}}
	syncModels := make([]string, 0)
	taskModels := make([]string, 0)
	declaredCapabilities := make([]string, 0)
	hasDeclaredSyncCapabilities := false
	hasDeclaredTaskCapabilities := false
	provider := ""
	var requiresBaseURLOverride *bool
	var supportsBaseURLOverride *bool
	hasSyncAdaptor := false
	if apiType, ok := common.ChannelType2APIType(channelType); ok {
		if adaptor := GetAdaptor(apiType); adaptor != nil {
			adaptor.Init(meta)
			syncModels = append(syncModels, adaptor.GetModelList()...)
			provider = normalizeProviderID(adaptor.GetChannelName())
			if metadata, ok := adaptor.(channel.CapabilityMetadataProvider); ok {
				capabilities := metadata.GetCapabilities()
				declaredCapabilities = append(declaredCapabilities, capabilities...)
				hasDeclaredSyncCapabilities = len(capabilities) > 0
			}
			if metadata, ok := adaptor.(channel.BaseURLMetadataProvider); ok {
				requires, supportsOverride := metadata.GetBaseURLPolicy()
				requiresBaseURLOverride = &requires
				supportsBaseURLOverride = &supportsOverride
			}
			hasSyncAdaptor = true
		}
	}
	hasTaskAdaptor := false
	if adaptor := GetTaskAdaptor(constant.TaskPlatform(strconv.Itoa(channelType))); adaptor != nil {
		adaptor.Init(meta)
		taskModels = append(taskModels, adaptor.GetModelList()...)
		if provider == "" || (provider == "openai" && channelType != constant.ChannelTypeOpenAI) {
			provider = normalizeProviderID(adaptor.GetChannelName())
		}
		if metadata, ok := adaptor.(channel.CapabilityMetadataProvider); ok {
			declaredCapabilities = append(declaredCapabilities, metadata.GetCapabilities()...)
			hasDeclaredTaskCapabilities = true
		}
		hasTaskAdaptor = true
	}
	if !hasSyncAdaptor && !hasTaskAdaptor {
		return ChannelTypeMetadata{}, false
	}

	name := constant.GetChannelTypeName(channelType)
	baseURL := ""
	if channelType >= 0 && channelType < len(constant.ChannelBaseURLs) {
		baseURL = constant.ChannelBaseURLs[channelType]
	}
	requiresBaseURL := strings.TrimSpace(baseURL) == ""
	supportsOverride := true
	if requiresBaseURLOverride != nil {
		requiresBaseURL = *requiresBaseURLOverride
	}
	if supportsBaseURLOverride != nil {
		supportsOverride = *supportsBaseURLOverride
	}
	return ChannelTypeMetadata{
		Type:           channelType,
		Provider:       provider,
		Name:           name,
		Description:    name + " 渠道",
		Icon:           name,
		DefaultBaseURL: baseURL,
		Status:         ChannelTypeStatusEnabled,
		Capabilities: inferChannelCapabilities(
			syncModels, taskModels, declaredCapabilities,
			hasSyncAdaptor, hasTaskAdaptor, hasDeclaredSyncCapabilities,
			hasDeclaredTaskCapabilities,
		),
		RequiresBaseURL:         requiresBaseURL,
		SupportsBaseURLOverride: supportsOverride,
	}, true
}

func normalizeProviderID(value string) string {
	var b strings.Builder
	separator := false
	for _, r := range strings.ToLower(strings.TrimSpace(value)) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			separator = false
		} else if b.Len() > 0 && !separator {
			b.WriteByte('_')
			separator = true
		}
	}
	return strings.Trim(b.String(), "_")
}

func inferChannelCapabilities(
	syncModels, taskModels, declaredCapabilities []string,
	hasSyncAdaptor, hasTaskAdaptor, hasDeclaredSyncCapabilities,
	hasDeclaredTaskCapabilities bool,
) []string {
	capabilities := map[string]bool{}
	for _, capability := range declaredCapabilities {
		capabilities[strings.ToLower(strings.TrimSpace(capability))] = true
	}
	inferModelCapabilities(syncModels, capabilities)
	if hasSyncAdaptor && !hasDeclaredSyncCapabilities && hasTextCapability(syncModels) {
		capabilities["text"] = true
	}
	taskCapabilities := map[string]bool{}
	inferModelCapabilities(taskModels, taskCapabilities)
	if hasTaskAdaptor && !hasDeclaredTaskCapabilities && len(taskCapabilities) == 0 {
		taskCapabilities["video"] = true
	}
	for capability := range taskCapabilities {
		capabilities[capability] = true
	}
	order := []string{"text", "vision", "embedding", "rerank", "image", "video", "audio"}
	result := make([]string, 0, len(capabilities))
	for _, capability := range order {
		if capabilities[capability] {
			result = append(result, capability)
		}
	}
	return result
}

func inferModelCapabilities(models []string, capabilities map[string]bool) {
	for _, model := range models {
		lower := strings.ToLower(model)
		if common.IsImageGenerationModel(lower) {
			capabilities["image"] = true
		}
		if isVideoGenerationModel(lower) {
			capabilities["video"] = true
		}
		if isAudioSpeechModel(lower) {
			capabilities["audio"] = true
		}
		if strings.Contains(lower, "embed") {
			capabilities["embedding"] = true
		}
		if strings.Contains(lower, "rerank") {
			capabilities["rerank"] = true
		}
		if strings.Contains(lower, "vision") || strings.Contains(lower, "multimodal") ||
			strings.Contains(lower, "-vl") || strings.Contains(lower, "vl-") {
			capabilities["vision"] = true
		}
	}
}

func hasTextCapability(models []string) bool {
	if len(models) == 0 {
		return true
	}
	for _, model := range models {
		lower := strings.ToLower(model)
		if !common.IsImageGenerationModel(lower) &&
			!isVideoGenerationModel(lower) &&
			!isAudioSpeechModel(lower) &&
			!strings.Contains(lower, "embed") &&
			!strings.Contains(lower, "rerank") {
			return true
		}
	}
	return false
}

func isVideoGenerationModel(model string) bool {
	markers := []string{
		"video", "text-to-video", "image-to-video", "reference-to-video",
		"seedance", "sora", "kling", "veo", "hailuo", "vidu", "wan",
	}
	for _, marker := range markers {
		if strings.Contains(model, marker) {
			return true
		}
	}
	return false
}

func isAudioSpeechModel(model string) bool {
	markers := []string{
		"audio/speech", "text-to-speech", "tts", "index-tts",
		"elevenlabs-tts", "eleven-music", "elevenlabs-music", "/music",
	}
	for _, marker := range markers {
		if strings.Contains(model, marker) {
			return true
		}
	}
	return false
}
