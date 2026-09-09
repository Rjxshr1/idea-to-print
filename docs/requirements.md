# 安装与运行要求

本项目支持上传参考图、云端生成初始几何、Blender 建模与检查、Bambu Studio 切片，以及可选的打印机状态读取。文字生成参考图由宿主的图像工具提供；已有图片可以直接使用。

## 按功能安装

Python 脚本要求 Python 3.11+。在仓库根目录创建虚拟环境后，根据需要安装依赖：

```sh
python -m pip install -r requirements.txt
python -m pip install -r requirements-hunyuan31.txt
python -m pip install -r requirements-modeling.txt
```

| 功能 | 工具与依赖 | 账号或配置 | 计算位置 |
|---|---|---|---|
| 上传图片、整理参考图 | `requirements.txt` 中的 Pillow | 本地图片和作业目录 | 本机 CPU；图片接收步骤仅验证和复制文件 |
| 官方 Hunyuan3D 3.1 图生模型 | `hunyuan31_api.py`；`requirements-hunyuan31.txt` | 腾讯云 API 凭据、服务权限和可用额度 | 云端 |
| 公共 Hunyuan3D 2.1 图生模型 | `hunyuan_shape.py`；`requirements.txt` 中的 `gradio_client` | 公共服务可访问；脚本采用匿名连接 | 云端 |
| Hunyuan3D Studio 网页生成 | 官方网页；宿主浏览器工具或手动操作 | 用户登录账号和可用额度 | 云端 |
| 几何处理与检查 | 单独安装 Blender；`requirements-modeling.txt` | 模型尺寸、方向和处理配置 | 本机 CPU/RAM；渲染可用 CPU |
| 切片与预览 | 单独安装 Bambu Studio | 机器、材料和工艺配置 | 本机 CPU/RAM；图形预览需要桌面环境 |
| 发送打印任务 | 官方打印界面；可选宿主桌面控制 | 已连接的打印机及对应访问权限 | 本机或云端连接打印机 |
| 读取打印机状态与相机 | `bambu_read.py`；Python 标准库 | 私网设备地址、序列号和 Studio LAN 访问码 | 本机网络访问 |

`requirements-modeling.txt` 提供 NumPy、SciPy、trimesh、manifold3d 和 rtree。Blender 及其 Python 环境需要单独配置。具体命令见[模型处理管线](../skills/printable-modeling/references/model-pipeline.md)和[修形与验收](../skills/printable-modeling/references/refinement-and-validation.md)。

## 官方 3.1 API 配置

设置 `TENCENTCLOUD_SECRET_ID` 和 `TENCENTCLOUD_SECRET_KEY`；临时凭据另需 `TENCENTCLOUD_TOKEN`。也可用 `--credentials-file` 指定私有 JSON 文件，包含 `secret_id`、`secret_key` 和可选 `token`。凭据保存在环境或私有配置中，不放入作业资料或 Git。

```sh
python skills/printable-modeling/scripts/hunyuan31_api.py config-check
```

`config-check` 检查 SDK 和配置，不提交生成任务。适配器提供提交、查询、下载和恢复命令，记录远端 JobId 与本地尝试状态。默认使用 `Model=3.1`、几何模式、1,500,000 面目标和 `ap-guangzhou` 区域；补充视图需要绑定图片哈希的一致性检查。初始化作业及完整参数见[官方 API 使用说明](../skills/printable-modeling/references/hunyuan31-api.md)。

公共 2.1 服务和 Studio 网页使用各自的接口与账号方式，不共用腾讯云 API 凭据。云端生成会将选定参考图上传到相应服务；仅接收图片或检查本地配置不会提交生成。各路线命令见[图片生成模型](../skills/printable-modeling/references/image-to-3d.md)。

## 本机资源与本地部署

云端图生模型不需要本机 CUDA、模型权重或推理显卡。本地网格处理、切片和预览主要消耗 CPU 与 RAM；所需内存随模型面数、几何操作和渲染分辨率增长。处理大型模型时保留高模原件，使用轻量代理预览，再按细节要求设置简化和渲染参数。

Blender 管线使用 CPU 渲染。白色 FDM 模型可直接使用几何生成结果，无需纹理生成。

完全离线的图生模型需要另行部署本地推理服务。本仓库提供公共 2.1 服务与官方 3.1 云端 API 适配器；本地模型的权重、CUDA/PyTorch 和显存要求按对应上游项目配置。

## 平台与软件配置

- Python 工具支持 Linux 和 Windows，CI 覆盖 Python 3.11/3.12。WSL 可用于 Windows 上的 Linux 建模环境。
- Blender 管线的运行参数和依赖配置见[模型处理管线](../skills/printable-modeling/references/model-pipeline.md)。
- `make_slice_only.py` 接受 `BambuStudio-02.07.01.62` 生成的指定单盘容器布局；其他布局使用 Studio 官方导出，或先适配转换器。
- 打印机状态与相机配置见[Bambu LAN 使用说明](../skills/3d-print-workflow/references/bambu-lan.md)。机器参数和访问码放在本地配置中。
- 宿主图像生成、浏览器和桌面控制均为可选能力；对应功能需要宿主提供工具，或由用户在相应应用中完成。
