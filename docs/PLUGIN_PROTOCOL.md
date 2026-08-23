# CoderAI 学堂插件协议 v1

## 能力范围

当前可安装的第三方插件属于声明式教学工具，只能调用系统已经配置的文字模型：

- `text.generate`：按模板生成文字。
- `projects.write`：将文字结果保存为学生作品。
- 插件可以组合多个文字工具，但不能增加新的 React 页面、后端接口或模型服务商协议。

以下能力不属于普通安装包：

- 本地模型适配器由后端受信任 Provider Adapter 提供，当前支持连接 API 服务器可访问的 Ollama、LM Studio 或私有 OpenAI 兼容服务。
- MiniMax 视频生成由内置受信任适配器提供，支持任务提交、查询、重试和结果保存。
- 新图片或视频服务商必须在后端实现专用适配器并经过发布测试，不能通过 v1 插件取得网络或 API Key 权限。

插件 v2 可以在保持声明式沙箱的前提下扩展图片、视频、工作流节点和课程模板动作；在协议、权限和审核规则发布前，不接受包含这些声明的安装包。

## 安全边界

- 插件安装包是扩展名建议为 `.coderai-plugin` 的 ZIP 文件，必须由受信任发布者使用 Ed25519 签名。
- v1 只解释声明式 `plugin.json`，不会执行 Python、JavaScript、EXE、Shell、动态库或安装脚本。
- v1 权限只有 `ai.text` 和 `projects.write`。插件没有任意文件、网络、进程、环境变量或数据库访问能力。
- 学生运行插件时仍执行账号隔离、课堂工具权限、监护人授权、学龄分类、输入审核和输出审核。
- 安装和每次运行都会重新检查发布者信任、签名、应用版本、文件清单、大小和 SHA-256。
- 安装包拒绝路径穿越、绝对路径、Windows 保留名、符号链接、重复文件、未签名文件、异常压缩率和超限内容。

## 安装包结构

```text
manifest.json
plugin.json
```

`manifest.json` 示例：

```json
{
  "schema_version": 1,
  "id": "scratch-story-helper",
  "name": "Scratch 故事助手",
  "description": "把学生创意整理为 Scratch 场景和角色步骤。",
  "category": "ai_tool",
  "version": "1.0.0",
  "publisher": { "key_id": "example-school", "name": "示例机构" },
  "compatibility": { "min_app_version": "0.1.0", "max_app_version": "0.1.999" },
  "permissions": ["ai.text", "projects.write"],
  "entry": { "type": "declarative", "path": "plugin.json" },
  "files": [{ "path": "plugin.json", "size": 320, "sha256": "..." }],
  "signature": { "algorithm": "Ed25519", "value": "..." }
}
```

`plugin.json` 示例：

```json
{
  "protocol_version": 1,
  "tools": [
    {
      "id": "story-plan",
      "name": "故事分镜规划",
      "description": "生成适合 Scratch 的角色、场景和步骤。",
      "action": "text.generate",
      "prompt_template": "请把下面的学生创意整理成 Scratch 分镜和编程步骤：\n{{prompt}}",
      "mode": "story",
      "save_project": true,
      "project_title": "Scratch 故事规划"
    }
  ]
}
```

提示词模板必须且只能包含一次 `{{prompt}}`。v1 支持的模式为 `story`、`polish`、`code_explain` 和 `prompt_refine`。

## 发布者密钥

插件发布私钥必须保存在项目和 `workspace_data` 之外：

```powershell
$env:CODERAI_PLUGIN_KEY_PASSWORD = "使用密码管理器生成的强密码"
python tools\plugin_packager.py keygen `
  --private-key D:\CoderAI-Plugin-Secrets\publisher-private.pem `
  --public-key D:\CoderAI-Plugin-Secrets\publisher-public.txt
```

教师在“扩展与授权 → 信任发布者”登记 Key ID、发布者名称和公钥。公钥指纹会进入教师审计日志，私钥绝不能导入教学软件。

## 构建插件包

```powershell
$env:CODERAI_PLUGIN_KEY_PASSWORD = "使用密码管理器生成的强密码"
python tools\plugin_packager.py build `
  --private-key D:\CoderAI-Plugin-Secrets\publisher-private.pem `
  --key-id example-school `
  --publisher "示例机构" `
  --plugin-id scratch-story-helper `
  --name "Scratch 故事助手" `
  --description "把创意整理为 Scratch 分镜" `
  --version 1.0.0 `
  --permission ai.text `
  --permission projects.write `
  --runtime D:\CoderAI-Plugin-Secrets\scratch-story-helper.json `
  --output D:\CoderAI-Plugin-Secrets\scratch-story-helper-1.0.0.coderai-plugin
```

升级包版本必须高于已安装版本。教师可以停用、重新启用或卸载自定义插件；移除发布者信任后，该发布者的已安装插件会立即停止验证和运行。许可证失效时会禁止安装和重新启用，但停用、卸载和移除发布者信任始终允许，确保教师仍可完成安全清理。

## 设计检查清单

1. 插件 ID 使用小写字母、数字和连字符，发布后保持不变。
2. 每项工具只申请实际需要的 `ai.text`、`projects.write` 权限。
3. 提示词模板必须且只能包含一次 `{{prompt}}`，不得伪装系统消息或要求输出密钥。
4. 使用 SemVer 提升版本，并正确设置应用最低、最高兼容版本。
5. 私钥保存在项目、安装包和 `workspace_data` 之外，只分发公钥。
6. 构建后在测试机构完成安装、停用、升级、卸载、权限拒绝和内容审核测试。
