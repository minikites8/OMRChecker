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
