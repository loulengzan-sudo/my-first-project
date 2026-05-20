#!/usr/bin/env python3
"""AIGC 检测 Web 界面

简易内网测试界面：上传 .docx 文件，逐段检测 AI 率，展示结果。

启动方式:
    PYTHONPATH=src python3 web/app.py
    PYTHONPATH=src python3 web/app.py --port 8080
"""

import argparse
import io
import json
import os
import sys
import tempfile
from datetime import datetime

# 在 import transformers 之前设置环境变量
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('TRANSFORMERS_NO_ADVISORY_WARNINGS', '1')
os.environ['MULTIPROCESSING_RESOURCE_TRACKER'] = '0'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'

from flask import Flask, request, jsonify, render_template_string, send_file

# 确保能导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from humanize_cn.check_pkg.api import check
from report_builder import build_report_pdf

app = Flask(__name__)

# ─── HTML 模板 ───
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AIGC 文本检测</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fira+Sans:wght@300;400;500;600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #020617; --surface: #0F172A; --surface2: #1E293B;
    --border: #334155; --text: #F8FAFC; --muted: #94A3B8;
    --green: #22C55E; --red: #EF4444; --yellow: #EAB308;
    --orange: #F97316; --blue: #3B82F6;
  }
  body {
    font-family: 'Fira Sans', system-ui, sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh; line-height: 1.6;
  }
  .container { max-width: 960px; margin: 0 auto; padding: 24px 20px; }

  /* Header */
  .header { text-align: center; padding: 40px 0 32px; }
  .header h1 { font-size: 28px; font-weight: 700; margin-bottom: 8px; }
  .header p { color: var(--muted); font-size: 14px; }

  /* Upload Area */
  .upload-area {
    border: 2px dashed var(--border); border-radius: 12px;
    padding: 48px 24px; text-align: center;
    cursor: pointer; transition: all 0.2s;
    background: var(--surface); margin-bottom: 24px;
  }
  .upload-area:hover, .upload-area.dragover {
    border-color: var(--blue); background: var(--surface2);
  }
  .upload-area svg { width: 48px; height: 48px; color: var(--muted); margin-bottom: 12px; }
  .upload-area .label { font-size: 16px; font-weight: 500; margin-bottom: 4px; }
  .upload-area .hint { font-size: 13px; color: var(--muted); }
  .upload-area input { display: none; }

  /* Progress */
  .progress-wrap { display: none; margin-bottom: 24px; }
  .progress-wrap.active { display: block; }
  .progress-bar-bg {
    height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%; background: var(--blue); border-radius: 3px;
    transition: width 0.3s; width: 0%;
  }
  .progress-text { font-size: 13px; color: var(--muted); margin-top: 6px; }

  /* Summary */
  .summary {
    display: none; background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 24px; margin-bottom: 24px;
  }
  .summary.active { display: block; }
  .summary-grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 16px;
  }
  .summary-card {
    background: var(--surface2); border-radius: 8px; padding: 16px; text-align: center;
  }
  .summary-card .value { font-size: 28px; font-weight: 700; font-family: 'Fira Code', monospace; }
  .summary-card .label { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .summary-card.green .value { color: var(--green); }
  .summary-card.red .value { color: var(--red); }
  .summary-card.yellow .value { color: var(--yellow); }
  .summary-card.orange .value { color: var(--orange); }

  /* Results */
  .results { display: none; }
  .results.active { display: block; }
  .result-item {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px 20px; margin-bottom: 10px;
    transition: border-color 0.2s;
  }
  .result-item:hover { border-color: var(--muted); }
  .result-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 8px;
  }
  .result-index { font-size: 12px; color: var(--muted); font-family: 'Fira Code', monospace; }
  .result-score {
    font-size: 14px; font-weight: 600; font-family: 'Fira Code', monospace;
    padding: 2px 10px; border-radius: 4px;
  }
  .score-very-high { background: rgba(239,68,68,0.15); color: var(--red); }
  .score-high { background: rgba(249,115,22,0.15); color: var(--orange); }
  .score-medium { background: rgba(234,179,8,0.15); color: var(--yellow); }
  .score-low { background: rgba(34,197,94,0.15); color: var(--green); }
  .result-bar {
    height: 4px; background: var(--surface2); border-radius: 2px;
    overflow: hidden; margin-bottom: 8px;
  }
  .result-bar-fill { height: 100%; border-radius: 2px; transition: width 0.5s; }
  .bar-very-high { background: var(--red); }
  .bar-high { background: var(--orange); }
  .bar-medium { background: var(--yellow); }
  .bar-low { background: var(--green); }
  .result-text {
    font-size: 13px; color: var(--muted); line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .result-meta {
    display: flex; gap: 12px; margin-top: 8px; font-size: 11px; color: var(--muted);
    font-family: 'Fira Code', monospace;
  }
  .result-meta span { opacity: 0.7; }

  /* Report Button */
  .report-btn {
    display: none; margin-top: 16px; padding: 10px 24px;
    background: var(--blue); color: white; border: none; border-radius: 8px;
    font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s;
    font-family: 'Fira Sans', system-ui, sans-serif;
  }
  .report-btn:hover { background: #2563EB; }
  .report-btn.active { display: inline-block; }
  .report-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  /* Responsive */
  @media (max-width: 640px) {
    .summary-grid { grid-template-columns: repeat(2, 1fr); }
    .header h1 { font-size: 22px; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>AIGC 文本检测</h1>
    <p>上传 .docx 文档，逐段检测 AI 生成概率</p>
  </div>

  <div class="upload-area" id="uploadArea">
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    </svg>
    <div class="label">点击或拖拽上传 .docx 文件</div>
    <div class="hint">支持 Word 文档（.docx），自动提取段落文本进行检测</div>
    <input type="file" id="fileInput" accept=".docx">
  </div>

  <div class="progress-wrap" id="progressWrap">
    <div class="progress-bar-bg"><div class="progress-bar-fill" id="progressBar"></div></div>
    <div class="progress-text" id="progressText">正在检测...</div>
  </div>

  <div class="summary" id="summary">
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <div>
        <div style="font-size:16px;font-weight:600;" id="fileName">-</div>
        <div style="font-size:13px;color:var(--muted);margin-top:2px;" id="fileInfo">-</div>
      </div>
      <div style="display:flex;align-items:center;gap:16px;">
        <div style="font-size:36px;font-weight:700;font-family:'Fira Code',monospace;" id="avgScore">-</div>
      </div>
    </div>
    <div class="summary-grid">
      <div class="summary-card red">
        <div class="value" id="countVeryHigh">0</div>
        <div class="label">VERY HIGH (&ge;75)</div>
      </div>
      <div class="summary-card orange">
        <div class="value" id="countHigh">0</div>
        <div class="label">HIGH (50-74)</div>
      </div>
      <div class="summary-card yellow">
        <div class="value" id="countMedium">0</div>
        <div class="label">MEDIUM (25-49)</div>
      </div>
      <div class="summary-card green">
        <div class="value" id="countLow">0</div>
        <div class="label">LOW (&lt;25)</div>
      </div>
    </div>
    <div style="text-align:center;">
      <button class="report-btn" id="reportBtn" onclick="generateReport()">📄 生成PDF检测报告</button>
    </div>
  </div>

  <div class="results" id="results"></div>
</div>

<script>
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const progressWrap = document.getElementById('progressWrap');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const summaryEl = document.getElementById('summary');
const resultsEl = document.getElementById('results');
let lastResults = [];  // 保存最近一次检测结果

uploadArea.addEventListener('click', () => fileInput.click());
uploadArea.addEventListener('dragover', e => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', e => {
  e.preventDefault();
  uploadArea.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file && file.name.endsWith('.docx')) uploadFile(file);
});
fileInput.addEventListener('change', e => { if (e.target.files[0]) uploadFile(e.target.files[0]); });

function scoreClass(score) {
  if (score >= 75) return 'very-high';
  if (score >= 50) return 'high';
  if (score >= 25) return 'medium';
  return 'low';
}

function scoreLabel(score) {
  if (score >= 75) return 'VERY HIGH';
  if (score >= 50) return 'HIGH';
  if (score >= 25) return 'MEDIUM';
  return 'LOW';
}

function uploadFile(file) {
  const formData = new FormData();
  formData.append('file', file);

  progressWrap.classList.add('active');
  summaryEl.classList.remove('active');
  resultsEl.classList.remove('active');
  resultsEl.innerHTML = '';
  lastResults = [];
  document.getElementById('reportBtn').classList.remove('active');
  progressBar.style.width = '0%';
  progressText.textContent = '正在上传...';

  fetch('/api/detect', { method: 'POST', body: formData })
    .then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || '检测失败'); });
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      function read() {
        reader.read().then(({ done, value }) => {
          if (done) return;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === 'progress') {
                const pct = Math.round(data.current / data.total * 100);
                progressBar.style.width = pct + '%';
                progressText.textContent = `检测中 ${data.current}/${data.total} 段 (${pct}%)`;
              } else if (data.type === 'result') {
                appendResult(data);
              } else if (data.type === 'done') {
                progressWrap.classList.remove('active');
                showSummary(data.summary);
              } else if (data.type === 'error') {
                progressWrap.classList.remove('active');
                alert(data.message);
              }
            } catch (e) {}
          }
          read();
        });
      }
      read();
    })
    .catch(err => {
      progressWrap.classList.remove('active');
      alert(err.message);
    });
}

function appendResult(data) {
  const cls = scoreClass(data.score);
  resultsEl.classList.add('active');
  lastResults.push(data);  // 保存结果
  const div = document.createElement('div');
  div.className = 'result-item';
  div.innerHTML = `
    <div class="result-header">
      <span class="result-index">#${data.index}</span>
      <span class="result-score score-${cls}">${data.score}/100 ${scoreLabel(data.score)}</span>
    </div>
    <div class="result-bar"><div class="result-bar-fill bar-${cls}" style="width:${data.score}%"></div></div>
    <div class="result-text">${escapeHtml(data.text)}</div>
    <div class="result-meta">
      <span>方法: ${data.method}</span>
      ${data.perplexity ? `<span>ppl: ${Number(data.perplexity).toFixed(0)}</span>` : ''}
    </div>
  `;
  resultsEl.appendChild(div);
}

function showSummary(s) {
  document.getElementById('fileName').textContent = s.filename;
  document.getElementById('fileInfo').textContent = `${s.total} 段已检测`;
  document.getElementById('avgScore').textContent = s.avg_score;
  document.getElementById('avgScore').style.color =
    s.avg_score >= 75 ? 'var(--red)' : s.avg_score >= 50 ? 'var(--orange)' :
    s.avg_score >= 25 ? 'var(--yellow)' : 'var(--green)';
  document.getElementById('countVeryHigh').textContent = s.very_high;
  document.getElementById('countHigh').textContent = s.high;
  document.getElementById('countMedium').textContent = s.medium;
  document.getElementById('countLow').textContent = s.low;
  summaryEl.classList.add('active');
  document.getElementById('reportBtn').classList.add('active');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function generateReport() {
  if (!lastResults.length) { alert('没有检测结果'); return; }

  const btn = document.getElementById('reportBtn');
  btn.disabled = true;
  btn.textContent = '⏳ 正在生成报告...';

  // 构建报告数据（发送完整文本用于词级分析）
  const reportData = {
    title: document.getElementById('fileName').textContent || '未命名论文',
    results: lastResults.map(r => ({
      text: r.text || '',
      score: r.score || 0,
    })),
  };

  fetch('/api/report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(reportData),
  })
    .then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || '生成失败'); });
      return r.blob();
    })
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = blob.name || 'AIGC检测报告.pdf';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    })
    .catch(err => alert('生成报告失败: ' + err.message))
    .finally(() => {
      btn.disabled = false;
      btn.textContent = '📄 生成PDF检测报告';
    });
}
</script>
</body>
</html>
"""


# 需要跳过的类别（非正文内容）
SKIP_CATEGORIES = {
    'abstract_chinese_title', 'keywords_chinese', 'keywords_english',
    'heading_level_1', 'heading_level_2', 'heading_level_3',
    'caption_figure', 'caption_table',
    'references_title', 'acknowledgements_title',
    'other',
}


def extract_paragraphs(docx_path):
    """用 wordformat 提取文档结构，返回正文段落列表。"""
    from wordformat.set_tag import set_tag_main

    config_path = os.path.join(os.path.dirname(__file__), 'detect_config.yaml')
    data = set_tag_main(docx_path=docx_path, configpath=config_path)
    paragraphs = []
    for i, item in enumerate(data):
        cat = item.get('category', '')
        text = item.get('paragraph', '').strip()
        if cat in SKIP_CATEGORIES:
            continue
        if len(text) >= 20:
            paragraphs.append({'index': i + 1, 'category': cat, 'text': text})
    return paragraphs


def count_chinese(text):
    return sum(1 for c in text if '\u4e00' <= c <= '\u9fff')


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/detect', methods=['POST'])
def detect():
    """上传 docx，逐段检测，SSE 流式返回结果。"""
    if 'file' not in request.files:
        return jsonify({'error': '请上传文件'}), 400

    file = request.files['file']
    if not file.filename.endswith('.docx'):
        return jsonify({'error': '仅支持 .docx 文件'}), 400

    # 保存到临时文件（wordformat 需要文件路径）
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        paragraphs = extract_paragraphs(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not paragraphs:
        return jsonify({'error': '文档中没有可检测的段落（至少 20 字）'}), 400

    def generate():
        total = len(paragraphs)
        results = []

        for i, para in enumerate(paragraphs):
            text = para['text']

            # 跳过中文太少的段落
            if count_chinese(text) < 20:
                continue

            try:
                r = check(text)
                result = {
                    'index': para['index'],
                    'text': text,  # 保存完整文本用于词级分析
                    'score': r['ai_score'],
                    'method': r['ai_method'],
                    'level': r['ai_level'],
                    'perplexity': r.get('perplexity'),
                }
                results.append(result)
            except Exception as e:
                results.append({
                    'index': para['index'],
                    'text': text[:300],
                    'score': 0,
                    'method': 'error',
                    'level': 'ERROR',
                    'perplexity': None,
                })

            # 发送进度
            yield f"data: {json.dumps({'type': 'progress', 'current': i + 1, 'total': total})}\n\n"
            # 发送单条结果
            yield f"data: {json.dumps({'type': 'result', **results[-1]})}\n\n"

        # 汇总
        scores = [r['score'] for r in results]
        avg = sum(scores) / len(scores) if scores else 0
        summary = {
            'filename': file.filename,
            'total': len(results),
            'avg_score': round(avg, 1),
            'very_high': sum(1 for s in scores if s >= 75),
            'high': sum(1 for s in scores if 50 <= s < 75),
            'medium': sum(1 for s in scores if 25 <= s < 50),
            'low': sum(1 for s in scores if s < 25),
        }
        yield f"data: {json.dumps({'type': 'done', 'summary': summary})}\n\n"

    return app.response_class(generate(), mimetype='text/event-stream')


@app.route('/api/report', methods=['POST'])
def report():
    """接收检测结果 JSON，自动进行词级分析，生成 PDF 报告并返回下载。

    请求体 JSON 格式:
    {
        "title": "论文题目",
        "results": [
            {"text": "段落全文...", "score": 75},
            ...
        ],
    }
    也可传入完整的 report_data 格式（含 chapters, original_text 等）。
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': '请提供 JSON 数据'}), 400

    try:
        # 如果传入的是简化格式（含 results 数组），自动转换为完整格式
        if 'results' in data and 'original_text' not in data:
            results = data['results']
            scores = [r.get('score', 0) for r in results]
            aigc_rate = round(sum(scores) / len(scores), 2) if scores else 0

            # 构建章节（按段落）
            total_chars = sum(len(r.get('text', '')) for r in results)
            aigc_chars = sum(
                len(r.get('text', '')) for r in results if r.get('score', 0) >= 50
            )
            chapters = [{'name': '全文', 'human_chars': total_chars - aigc_chars,
                         'total_chars': total_chars}]

            report_data = {
                'title': data.get('title', '未命名论文'),
                'author': data.get('author', ''),
                'institution': data.get('institution', ''),
                'char_count': total_chars,
                'page_count': data.get('page_count', 0),
                'table_count': data.get('table_count', 0),
                'image_count': data.get('image_count', 0),
                'aigc_rate': aigc_rate,
                'human_rate': round(100 - aigc_rate, 2),
                'chapters': chapters,
            }
        else:
            report_data = data

        pdf_bytes = build_report_pdf(report_data)
        filename = report_data.get('title', 'AIGC检测报告')
        safe_name = ''.join(c for c in filename if c not in r'\/:*?"<>|')
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        download_name = f'{safe_name}_AIGC检测报告_{ts}.pdf'

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        from loguru import logger
        logger.exception("生成报告失败")
        return jsonify({'error': f'生成报告失败: {str(e)}'}), 500


def main():
    parser = argparse.ArgumentParser(description='AIGC 检测 Web 界面')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址')
    parser.add_argument('--port', type=int, default=5000, help='监听端口')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    args = parser.parse_args()

    print(f'\n  AIGC 检测 Web 界面')
    print(f'  http://{args.host}:{args.port}\n')
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
