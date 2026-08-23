from typing import Any


PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "id": "openai_compatible",
        "name": "OpenAI Compatible",
        "provider_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "text_model": "gpt-4o-mini",
        "image_model": "gpt-image-1",
        "video_model": "",
        "capabilities": ["text", "image", "video"],
        "models": {
            "text": [
                {"id": "gpt-4o-mini", "name": "GPT-4o mini"},
                {"id": "gpt-4o", "name": "GPT-4o"},
                {"id": "gpt-4.1-nano", "name": "GPT-4.1 nano"},
                {"id": "gpt-4.1-mini", "name": "GPT-4.1 mini"},
                {"id": "gpt-4.1", "name": "GPT-4.1"},
                {"id": "o3-mini", "name": "o3-mini"},
                {"id": "o3", "name": "o3"},
                {"id": "o4-mini", "name": "o4-mini"},
            ],
            "image": [
                {"id": "gpt-image-1", "name": "GPT Image 1"},
                {"id": "dall-e-3", "name": "DALL-E 3"},
                {"id": "dall-e-2", "name": "DALL-E 2"},
            ],
            "video": [],
        },
        "description": "通用 OpenAI 兼容接口，适合接入自定义中转或私有网关。",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "text_model": "deepseek-v4-flash",
        "image_model": "",
        "video_model": "",
        "capabilities": ["text"],
        "models": {
            "text": [
                {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
                {"id": "deepseek-chat", "name": "DeepSeek Chat"},
                {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner"},
            ],
            "image": [],
            "video": [],
        },
        "description": "DeepSeek 官方 OpenAI 兼容接口，主要用于文本生成、代码解释和推理任务。",
    },
    {
        "id": "local_openai_compatible",
        "name": "本地 / 私有模型",
        "provider_type": "local_openai_compatible",
        "base_url": "http://host.docker.internal:11434/v1",
        "text_model": "qwen2.5-coder:7b",
        "image_model": "",
        "video_model": "",
        "capabilities": ["text"],
        "requires_api_key": False,
        "models": {
            "text": [
                {"id": "qwen2.5-coder:7b", "name": "Qwen 2.5 Coder 7B"},
                {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B"},
                {"id": "llama3.2:3b", "name": "Llama 3.2 3B"},
                {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B"},
            ],
            "image": [],
            "video": [],
        },
        "description": "连接 API 服务器可访问的 Ollama、LM Studio 或机构私有 OpenAI 兼容推理服务；API Key 可留空。",
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "provider_type": "minimax",
        "base_url": "https://api.minimax.io/v1",
        "text_model": "MiniMax-M3",
        "image_model": "image-01",
        "video_model": "video-01",
        "capabilities": ["text", "image", "video"],
        "models": {
            "text": [
                {"id": "MiniMax-M3", "name": "MiniMax M3"},
                {"id": "MiniMax-M2.1", "name": "MiniMax M2.1"},
                {"id": "MiniMax-Text-01", "name": "MiniMax Text 01"},
            ],
            "image": [
                {"id": "image-01", "name": "MiniMax Image 01"},
            ],
            "video": [
                {"id": "video-01", "name": "MiniMax Video 01"},
                {"id": "T2V-01-Director", "name": "T2V 01 Director"},
                {"id": "I2V-01-Director", "name": "I2V 01 Director"},
            ],
        },
        "description": "MiniMax 国内多模态能力入口，文本使用 OpenAI 兼容接口，图片和视频使用 MiniMax 标准生成接口。",
    },
    {
        "id": "volcengine_jimeng",
        "name": "火山引擎 / 即梦",
        "provider_type": "volcengine_jimeng",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "text_model": "doubao-seed-1-6",
        "image_model": "doubao-seedream-3-0-t2i-250415",
        "video_model": "doubao-seedance-1-0-lite-t2v-250428",
        "capabilities": ["text", "image", "video"],
        "models": {
            "text": [
                {"id": "doubao-seed-1-6", "name": "豆包 Seed 1.6"},
                {"id": "doubao-1-5-pro-32k-250115", "name": "豆包 1.5 Pro 32K"},
                {"id": "doubao-1-5-lite-32k-250115", "name": "豆包 1.5 Lite 32K"},
            ],
            "image": [
                {"id": "doubao-seedream-3-0-t2i-250415", "name": "Seedream 3.0"},
                {"id": "doubao-seedream-4-0-250828", "name": "Seedream 4.0"},
            ],
            "video": [
                {
                    "id": "doubao-seedance-1-0-lite-t2v-250428",
                    "name": "Seedance 1.0 Lite",
                    "durations": [5, 10],
                    "supports_image": True,
                },
            ],
        },
        "description": "火山方舟文本、Seedream 图片与 Seedance 异步视频生成入口。",
    },
    {
        "id": "qwen",
        "name": "通义千问 / 阿里云百炼",
        "provider_type": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "text_model": "qwen-plus",
        "image_model": "wanx2.1-t2i-turbo",
        "video_model": "",
        "capabilities": ["text", "image"],
        "models": {
            "text": [
                {"id": "qwen-plus", "name": "Qwen Plus"},
                {"id": "qwen-turbo", "name": "Qwen Turbo"},
                {"id": "qwen-max", "name": "Qwen Max"},
                {"id": "qwen3-max", "name": "Qwen3 Max"},
                {"id": "qwen3-coder-plus", "name": "Qwen3 Coder Plus"},
                {"id": "qwen3-vl-plus", "name": "Qwen3 VL Plus"},
            ],
            "image": [
                {"id": "wanx2.1-t2i-turbo", "name": "通义万相 2.1 Turbo"},
                {"id": "wanx2.1-t2i-plus", "name": "通义万相 2.1 Plus"},
            ],
            "video": [],
        },
        "description": "阿里云百炼 OpenAI 兼容文本入口，并通过通义万相异步任务接口生成课堂图片。",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "provider_type": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "text_model": "glm-4-flash",
        "image_model": "cogview-3-flash",
        "video_model": "",
        "capabilities": ["text", "image"],
        "models": {
            "text": [
                {"id": "glm-4-flash", "name": "GLM-4 Flash"},
                {"id": "glm-4-air", "name": "GLM-4 Air"},
                {"id": "glm-4-plus", "name": "GLM-4 Plus"},
                {"id": "glm-4-long", "name": "GLM-4 Long"},
                {"id": "glm-4.5", "name": "GLM-4.5"},
                {"id": "glm-z1-flash", "name": "GLM-Z1 Flash"},
            ],
            "image": [
                {"id": "cogview-3-flash", "name": "CogView-3 Flash"},
                {"id": "cogview-4", "name": "CogView-4"},
            ],
            "video": [],
        },
        "description": "智谱开放平台 OpenAI 兼容入口，适合文本和课堂图片生成。",
    },
]


def get_provider_preset(provider_type: str) -> dict[str, Any]:
    return next(
        (preset for preset in PROVIDER_PRESETS if preset["provider_type"] == provider_type or preset["id"] == provider_type),
        PROVIDER_PRESETS[0],
    )


def list_provider_presets() -> list[dict[str, Any]]:
    return PROVIDER_PRESETS


def provider_capabilities(provider_type: str) -> list[str]:
    return get_provider_preset(provider_type).get("capabilities", [])


def provider_requires_api_key(provider_type: str) -> bool:
    return bool(get_provider_preset(provider_type).get("requires_api_key", True))
