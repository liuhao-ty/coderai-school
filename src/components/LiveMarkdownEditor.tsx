import { Input, Typography } from "antd";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";


const { Text } = Typography;

export function LiveMarkdownEditor({
  value,
  onChange,
  placeholder = "# Markdown 内容",
  maxLength = 500_000,
  ariaLabel = "Markdown 编辑器",
  components,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  maxLength?: number;
  ariaLabel?: string;
  components?: Components;
}) {
  return (
    <div className="liveMarkdownEditor">
      <section className="liveMarkdownPane">
        <Text strong>Markdown 源码</Text>
        <Input.TextArea
          className="markdownEditor liveMarkdownSource"
          aria-label={ariaLabel}
          value={value}
          maxLength={maxLength}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
        />
      </section>
      <section className="liveMarkdownPane">
        <Text strong>实时预览</Text>
        <article className="markdownPreview liveMarkdownPreview">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} urlTransform={(url) => url}>
            {value || "输入 Markdown 后会在这里立即显示预览。"}
          </ReactMarkdown>
        </article>
      </section>
    </div>
  );
}
