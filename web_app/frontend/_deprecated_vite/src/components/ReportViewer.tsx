interface ReportViewerProps {
  type: string;
  content: string;
  onClose: () => void;
}

const reportTitles: Record<string, string> = {
  bull: '🐂 看多分析报告',
  bear: '🐻 看空分析报告',
  risk: '⚠️ 风险分析报告',
  trading: '📊 交易分析报告',
  decision: '🎯 决策结论',
};

export default function ReportViewer({ type, content, onClose }: ReportViewerProps) {
  // Parse markdown sections for better display
  const sections = parseMarkdownSections(content);

  return (
    <div className="glass-panel flex-1 flex flex-col min-h-[300px] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between">
        <h3 className="text-sm font-medium text-white">
          {reportTitles[type] || type}
        </h3>
        <button
          onClick={onClose}
          className="text-white/40 hover:text-white transition-colors text-lg leading-none"
        >
          ✕
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-5 space-y-4">
        {sections.length > 0 ? (
          sections.map((section, i) => (
            <div key={i} className="bg-white/5 rounded-lg p-4">
              <h4 className="text-amber-400 font-medium text-sm mb-2">{section.title}</h4>
              <div className="text-sm text-white/70 leading-relaxed whitespace-pre-wrap">
                {section.content}
              </div>
            </div>
          ))
        ) : (
          <div className="text-sm text-white/70 leading-relaxed whitespace-pre-wrap">
            {content}
          </div>
        )}

        {!content && (
          <div className="text-center text-white/30 py-12">
            <p>报告内容为空</p>
            <p className="text-xs mt-1">请等待圆桌讨论完成</p>
          </div>
        )}
      </div>
    </div>
  );
}

function parseMarkdownSections(text: string): { title: string; content: string }[] {
  const sections: { title: string; content: string }[] = [];
  const lines = text.split('\n');
  let currentTitle = '';
  let currentContent: string[] = [];

  for (const line of lines) {
    if (line.match(/^#{1,3}\s/)) {
      if (currentTitle && currentContent.length > 0) {
        sections.push({ title: currentTitle, content: currentContent.join('\n').trim() });
      }
      currentTitle = line.replace(/^#{1,3}\s/, '').trim();
      currentContent = [];
    } else {
      currentContent.push(line);
    }
  }

  if (currentTitle) {
    sections.push({ title: currentTitle, content: currentContent.join('\n').trim() });
  }

  return sections;
}
