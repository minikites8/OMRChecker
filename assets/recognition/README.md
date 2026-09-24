# 随仓库交付的定位资源

此目录存放运行时必需的固定参考资源。Git 和 Docker 构建应完整包含此目录。

| 资源 | 用途 |
| --- | --- |
| `software-16th-abc.pdf` | 第十六届 A/B/C 通用两页答题卡的特征配准参考 |
| `software-15th-a.pdf` | 第十五届 A 卷两页答题卡参考及生成器输入 |
| `omr_marker.jpg` | 四角定位模板匹配 |
| `phone_scan/` | 原有通用扫描入口的配置、评分示例和空白参考图片 |
| `manifest.json` | 7 个固定文件的字节大小与 SHA-256 清单 |

资源由现有本地文件原样复制，保留原始像素、定位几何参数和扫描配置。`.gitattributes` 固定资源字节，保证 Windows 与 Linux 检出时 SHA-256 一致。`phone_scan` 保留原有混合题型示例；两届正式答题卡通过各自参考 PDF 和 `exam_review.py` 布局参数识别。

## 部署检查

在项目根目录执行：

```powershell
python -m recognition_assets
```

成功时输出 `ok: true`、文件数 `7`，以及两套参考 PDF 各 `2` 页。资源缺失、校验值变化或页数异常时输出中文错误并以状态码 `1` 退出。Docker 镜像构建、独立 WebUI 启动和 FastAPI 启动均执行此检查。

持久化数据仍位于 `OMR_DATA_ROOT`，模板注册表和复制模板继续写入该目录下的 `inputs/scan_templates/`。启动会迁移已知旧 `output/pdf/...` 默认引用及内置 `inputs/phone_scan` 路径，保留当前选中模板、自定义参考文件和其他布局参数。

## 更新固定资源

1. 将新资源与对应布局代码一起更新，保留匹配的页数和坐标系。
2. 更新 `manifest.json` 中对应文件的 `bytes` 与 `sha256`。
3. 运行部署检查，以及 `src/tests/test_recognition_assets.py` 的隔离、透视变换和页序回归测试。
4. 将本目录、`recognition_assets.py`、路径迁移代码和测试一起提交，重新构建后端镜像。

生成的答题卡继续保存在 `output/pdf/`；运行中的识别服务使用此目录内的版本化参考资源。
