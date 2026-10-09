package fal

const (
	ChannelName                   = "fal.ai"
	ModelIndexTTS2                = "index-tts-2"
	ModelIndexTTS2FalID           = "fal-ai/index-tts-2/text-to-speech"
	ModelElevenLabsTTSElevenV3    = "elevenlabs-tts-eleven-v3"
	ModelElevenLabsTTSElevenV3ID  = "fal-ai/elevenlabs/tts/eleven-v3"
	ModelElevenLabsMusic          = "elevenlabs-music"
	ModelElevenLabsMusicID        = "fal-ai/elevenlabs/music"
	ModelGPTImage2                = "gpt-image-2"
	ModelGPTImage2Text            = "gpt-image-2-text"
	ModelGPTImage2Edit            = "gpt-image-2-edit"
	ModelGPTImage2TextID          = "openai/gpt-image-2"
	ModelGPTImage2EditID          = "openai/gpt-image-2/edit"
	ModelNanoBanana2              = "nano-banana-2"
	ModelNanoBanana2Text          = "nano-banana-2-text"
	ModelNanoBanana2Edit          = "nano-banana-2-edit"
	ModelNanoBanana2TextID        = "fal-ai/nano-banana-2"
	ModelNanoBanana2EditID        = "fal-ai/nano-banana-2/edit"
	ModelSeedance20               = "seedance-2.0"
	ModelSeedance20Text           = "seedance-2.0-text"
	ModelSeedance20Image          = "seedance-2.0-image"
	ModelSeedance20Ref            = "seedance-2.0-reference"
	ModelSeedance20TextID         = "bytedance/seedance-2.0/text-to-video"
	ModelSeedance20ImageID        = "bytedance/seedance-2.0/image-to-video"
	ModelSeedance20RefID          = "bytedance/seedance-2.0/reference-to-video"
	ModelSeedance20Fast           = "seedance-2.0-fast"
	ModelSeedance20FastText       = "seedance-2.0-fast-text"
	ModelSeedance20FastImage      = "seedance-2.0-fast-image"
	ModelSeedance20FastRef        = "seedance-2.0-fast-reference"
	ModelSeedance20FastTextID     = "bytedance/seedance-2.0/fast/text-to-video"
	ModelSeedance20FastImageID    = "bytedance/seedance-2.0/fast/image-to-video"
	ModelSeedance20FastRefID      = "bytedance/seedance-2.0/fast/reference-to-video"
	ModelSeedanceV1ProFast        = "seedance-v1-pro-fast"
	ModelSeedanceV1ProFastText    = "seedance-v1-pro-fast-text"
	ModelSeedanceV1ProFastImage   = "seedance-v1-pro-fast-image"
	ModelSeedanceV1ProFastTextID  = "fal-ai/bytedance/seedance/v1/pro/fast/text-to-video"
	ModelSeedanceV1ProFastImageID = "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video"
	defaultResponseFormat         = "mp3"
)

var ModelList = []string{
	ModelIndexTTS2,
	ModelIndexTTS2FalID,
	ModelElevenLabsTTSElevenV3,
	ModelElevenLabsTTSElevenV3ID,
	ModelElevenLabsMusic,
	ModelElevenLabsMusicID,
	ModelGPTImage2,
	ModelGPTImage2Text,
	ModelGPTImage2Edit,
	ModelGPTImage2TextID,
	ModelGPTImage2EditID,
	ModelNanoBanana2,
	ModelNanoBanana2Text,
	ModelNanoBanana2Edit,
	ModelNanoBanana2TextID,
	ModelNanoBanana2EditID,
	ModelSeedance20,
	ModelSeedance20Text,
	ModelSeedance20Image,
	ModelSeedance20Ref,
	ModelSeedance20TextID,
	ModelSeedance20ImageID,
	ModelSeedance20RefID,
	ModelSeedance20Fast,
	ModelSeedance20FastText,
	ModelSeedance20FastImage,
	ModelSeedance20FastRef,
	ModelSeedance20FastTextID,
	ModelSeedance20FastImageID,
	ModelSeedance20FastRefID,
	ModelSeedanceV1ProFast,
	ModelSeedanceV1ProFastText,
	ModelSeedanceV1ProFastImage,
	ModelSeedanceV1ProFastTextID,
	ModelSeedanceV1ProFastImageID,
}
