# 手写答案 AI 判分配置

在启动扫描台的 PowerShell 窗口中设置以下变量：

```powershell
$env:HANDWRITING_AI_API_KEY = "你的接口密钥"
$env:HANDWRITING_AI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
$env:HANDWRITING_AI_MODEL = "gpt-4o-mini"
D:\Programs\OMRChecker\run_scan_ui.ps1
```

兼容 OpenAI Chat Completions 格式的接口可以直接使用。系统按结构化试卷的每道大题（如 1.、2.、3.）各发起一次请求，把该大题下所有填空位或小问的题干、参考答案、OCR 结果和手写区域图片一起提交给 AI，再为每个小问分别返回 AI 通过、AI 不通过或 AI 需复核，并在 WebUI 中显示图像识别内容与判断理由。

接口密钥只从当前进程环境变量读取，项目文件不会保存密钥。


判分规则：46—60 题必须同时识别到正确行号和改错内容，缺少行号直接判为不通过。64 题按开放算法题处理，参考答案允许为空，AI 根据题干、手写思路、代码正确性、边界条件和复杂度自主返回通过、不通过或需复核。


AI 大题请求支持并发执行：

```powershell
$env:HANDWRITING_AI_CONCURRENCY = "3"
```

系统按大题分组，每组独立请求 AI；并发数支持 1—8，默认 3。单组超时会进入需复核状态，其他大题继续处理。WebUI 的 `/api/health` 会返回当前 AI 并发数。


## 原图优先判题（2026-09-24）

- 每道大题继续并发处理；改错题大题先进行一次原图独立转录，再进行一次答案判断。
- 独立转录请求只含题号与原始扫描图片。题干、参考答案、OCR结果在独立转录完成后进入判题阶段，改错题判题阶段使用独立视觉证据。
- 系统保留OCR参考文字，AI最终校验使用视觉转录及其置信度。`6/b`、`1/l`、`0/O`等字形歧义交给人工复核。
- 图像清晰时，改错题仍要求正确的手写数字行号及改错内容；空白、确认缺少行号或行号错误按错误处理。
- 视觉转录为空、图片缺失或置信度偏低时转人工复核。印刷标签由图片中的字体和排版确定。
- 未确认成绩的历史改错AI结论标记为待重新读图。人工结论和已确认成绩保持现状。重启扫描台后点击“AI判断手写内容”更新结果。

独立读图阶段的可复用提示词：

```text
只依据本题原始扫描图片逐字转录实际手写内容，保留手写数字行号和代码符号。
相似字形6/b、1/l、0/O通过笔画区分，字形存在歧义时标记uncertain/ambiguous。
印刷标签由字体、位置和笔迹风格确定；行号清晰缺失时标记missing。
返回question、visual_text、confidence、image_status、line_number_status和图像证据reason。
判分由后续阶段结合参考答案完成。
```


## 仅 AI 识别

在“批量批改”或“基础扫描”的“文字识别方式”中选择 **仅 AI 识别（关闭本地 OCR）**。浏览器保存该选择，单份和批量任务都会携带 `local_ocr_enabled: false`。每份复核记录保存独立模式，考生管理的姓名重识别沿用该记录的模式。

仅 AI 模式通过现有 AI 接口逐字段转录姓名、手写学号和答案，再执行答案审核。字段转录请求只携带原始手写图片和字段编号。页面定位、裁切、学号填涂和客观题填涂检测继续由 OMR 流程处理。AI 异常会显示识别错误或进入待复核状态。

服务端默认模式可以在启动窗口设置：

```powershell
$env:OMR_LOCAL_OCR_ENABLED = "0"
D:\Programs\OMRChecker\run_scan_ui.ps1
```

`0` 表示仅 AI 识别，`1` 表示本地 OCR；任务中明确指定的模式优先于环境默认值。仅 AI 模式使用现有 `HANDWRITING_AI_API_KEY`、`HANDWRITING_AI_ENDPOINT` 和 `HANDWRITING_AI_MODEL` 配置。健康检查的 `recognition` 字段显示服务端默认模式。
