package relay

import (
	"strconv"

	"github.com/dramaclaw/localapi/constant"
	"github.com/dramaclaw/localapi/relay/channel"
	"github.com/dramaclaw/localapi/relay/channel/advancedcustom"
	"github.com/dramaclaw/localapi/relay/channel/ali"
	"github.com/dramaclaw/localapi/relay/channel/aws"
	"github.com/dramaclaw/localapi/relay/channel/baidu"
	"github.com/dramaclaw/localapi/relay/channel/baidu_v2"
	"github.com/dramaclaw/localapi/relay/channel/claude"
	"github.com/dramaclaw/localapi/relay/channel/cloudflare"
	"github.com/dramaclaw/localapi/relay/channel/codex"
	"github.com/dramaclaw/localapi/relay/channel/cohere"
	"github.com/dramaclaw/localapi/relay/channel/coze"
	"github.com/dramaclaw/localapi/relay/channel/deepseek"
	"github.com/dramaclaw/localapi/relay/channel/dify"
	"github.com/dramaclaw/localapi/relay/channel/doubao_audio"
	"github.com/dramaclaw/localapi/relay/channel/fal"
	"github.com/dramaclaw/localapi/relay/channel/gemini"
	"github.com/dramaclaw/localapi/relay/channel/jimeng"
	"github.com/dramaclaw/localapi/relay/channel/jina"
	"github.com/dramaclaw/localapi/relay/channel/minimax"
	"github.com/dramaclaw/localapi/relay/channel/mistral"
	"github.com/dramaclaw/localapi/relay/channel/mokaai"
	"github.com/dramaclaw/localapi/relay/channel/moonshot"
	"github.com/dramaclaw/localapi/relay/channel/newapi"
	"github.com/dramaclaw/localapi/relay/channel/ollama"
	"github.com/dramaclaw/localapi/relay/channel/openai"
	"github.com/dramaclaw/localapi/relay/channel/palm"
	"github.com/dramaclaw/localapi/relay/channel/perplexity"
	"github.com/dramaclaw/localapi/relay/channel/replicate"
	"github.com/dramaclaw/localapi/relay/channel/siliconflow"
	"github.com/dramaclaw/localapi/relay/channel/sub2api"
	"github.com/dramaclaw/localapi/relay/channel/submodel"
	taskali "github.com/dramaclaw/localapi/relay/channel/task/ali"
	taskcomfyui "github.com/dramaclaw/localapi/relay/channel/task/comfyui"
	taskdoubao "github.com/dramaclaw/localapi/relay/channel/task/doubao"
	taskfal "github.com/dramaclaw/localapi/relay/channel/task/fal"
	taskGemini "github.com/dramaclaw/localapi/relay/channel/task/gemini"
	"github.com/dramaclaw/localapi/relay/channel/task/hailuo"
	taskjimeng "github.com/dramaclaw/localapi/relay/channel/task/jimeng"
	"github.com/dramaclaw/localapi/relay/channel/task/kling"
	tasksora "github.com/dramaclaw/localapi/relay/channel/task/sora"
	"github.com/dramaclaw/localapi/relay/channel/task/suno"
	taskvertex "github.com/dramaclaw/localapi/relay/channel/task/vertex"
	taskVidu "github.com/dramaclaw/localapi/relay/channel/task/vidu"
	"github.com/dramaclaw/localapi/relay/channel/tencent"
	"github.com/dramaclaw/localapi/relay/channel/vertex"
	"github.com/dramaclaw/localapi/relay/channel/volcengine"
	"github.com/dramaclaw/localapi/relay/channel/xai"
	"github.com/dramaclaw/localapi/relay/channel/xunfei"
	"github.com/dramaclaw/localapi/relay/channel/zhipu"
	"github.com/dramaclaw/localapi/relay/channel/zhipu_4v"
	"github.com/gin-gonic/gin"
)

func GetAdaptor(apiType int) channel.Adaptor {
	switch apiType {
	case constant.APITypeAli:
		return &ali.Adaptor{}
	case constant.APITypeAnthropic:
		return &claude.Adaptor{}
	case constant.APITypeBaidu:
		return &baidu.Adaptor{}
	case constant.APITypeGemini:
		return &gemini.Adaptor{}
	case constant.APITypeOpenAI:
		return &openai.Adaptor{}
	case constant.APITypePaLM:
		return &palm.Adaptor{}
	case constant.APITypeTencent:
		return &tencent.DispatchAdaptor{}
	case constant.APITypeXunfei:
		return &xunfei.Adaptor{}
	case constant.APITypeZhipu:
		return &zhipu.Adaptor{}
	case constant.APITypeZhipuV4:
		return &zhipu_4v.Adaptor{}
	case constant.APITypeOllama:
		return &ollama.Adaptor{}
	case constant.APITypePerplexity:
		return &perplexity.Adaptor{}
	case constant.APITypeAws:
		return &aws.Adaptor{}
	case constant.APITypeCohere:
		return &cohere.Adaptor{}
	case constant.APITypeDify:
		return &dify.Adaptor{}
	case constant.APITypeJina:
		return &jina.Adaptor{}
	case constant.APITypeCloudflare:
		return &cloudflare.Adaptor{}
	case constant.APITypeSiliconFlow:
		return &siliconflow.Adaptor{}
	case constant.APITypeVertexAi:
		return &vertex.Adaptor{}
	case constant.APITypeMistral:
		return &mistral.Adaptor{}
	case constant.APITypeDeepSeek:
		return &deepseek.Adaptor{}
	case constant.APITypeMokaAI:
		return &mokaai.Adaptor{}
	case constant.APITypeVolcEngine:
		return &volcengine.Adaptor{}
	case constant.APITypeDoubaoAudio:
		return &doubao_audio.Adaptor{}
	case constant.APITypeBaiduV2:
		return &baidu_v2.Adaptor{}
	case constant.APITypeOpenRouter:
		return &openai.Adaptor{}
	case constant.APITypeXinference:
		return &openai.Adaptor{}
	case constant.APITypeXai:
		return &xai.Adaptor{}
	case constant.APITypeCoze:
		return &coze.Adaptor{}
	case constant.APITypeJimeng:
		return &jimeng.Adaptor{}
	case constant.APITypeMoonshot:
		return &moonshot.Adaptor{} // Moonshot uses Claude API
	case constant.APITypeSubmodel:
		return &submodel.Adaptor{}
	case constant.APITypeMiniMax:
		return &minimax.Adaptor{}
	case constant.APITypeReplicate:
		return &replicate.Adaptor{}
	case constant.APITypeCodex:
		return &codex.Adaptor{}
	case constant.APITypeFal:
		return &fal.Adaptor{}
	case constant.APITypeAdvancedCustom:
		return &advancedcustom.Adaptor{}
	case constant.APITypeSub2API:
		return &sub2api.Adaptor{}
	case constant.APITypeNewAPI:
		return &newapi.Adaptor{}
	}
	return nil
}

func GetTaskPlatform(c *gin.Context) constant.TaskPlatform {
	channelType := c.GetInt("channel_type")
	if channelType > 0 {
		return constant.TaskPlatform(strconv.Itoa(channelType))
	}
	return constant.TaskPlatform(c.GetString("platform"))
}

func GetTaskAdaptor(platform constant.TaskPlatform) channel.TaskAdaptor {
	switch platform {
	//case constant.APITypeAIProxyLibrary:
	//	return &aiproxy.Adaptor{}
	case constant.TaskPlatformSuno:
		return &suno.TaskAdaptor{}
	}
	if channelType, err := strconv.ParseInt(string(platform), 10, 64); err == nil {
		switch channelType {
		case constant.ChannelTypeAli:
			return &taskali.TaskAdaptor{}
		case constant.ChannelTypeKling:
			return &kling.TaskAdaptor{}
		case constant.ChannelTypeJimeng:
			return &taskjimeng.TaskAdaptor{}
		case constant.ChannelTypeVertexAi:
			return &taskvertex.TaskAdaptor{}
		case constant.ChannelTypeVidu:
			return &taskVidu.TaskAdaptor{}
		case constant.ChannelTypeDoubaoVideo, constant.ChannelTypeVolcEngine:
			return &taskdoubao.TaskAdaptor{}
		case constant.ChannelTypeSora, constant.ChannelTypeOpenAI:
			return &tasksora.TaskAdaptor{}
		case constant.ChannelTypeGemini:
			return &taskGemini.TaskAdaptor{}
		case constant.ChannelTypeMiniMax:
			return &hailuo.TaskAdaptor{}
		case constant.ChannelTypeFal:
			return &taskfal.TaskAdaptor{}
		case constant.ChannelTypeComfyUI:
			return &taskcomfyui.TaskAdaptor{}
		}
	}
	return nil
}
