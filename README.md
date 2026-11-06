# 态势地面站

在 Firefly ITX-3588J（RK3588）上运行的民用灾害态势地面站。浏览器打开本机页面，按无人机云台姿态把视频贴到地图上，用来和灾前影像对照。云台垂直向下时，画面贴在飞机正下方；斜视时只标出真实视场。

页面：`http://127.0.0.1:8090/`  
设置：`http://127.0.0.1:8090/settings.html`  
图传预览：`http://127.0.0.1:8889/live/`

视频解码使用瑞芯微 MPP，贴地投影在 CPU 上完成。直播 HUD OCR 使用本机 Tesseract，不使用 NPU。

## 运行

依赖：Python 3、OpenCV、NumPy、GStreamer（含 `mppvideodec`）、ffmpeg、`tesseract-ocr`、`pytesseract`。

```bash
cp config.example.json config.json
# 在 config.json 中填写百度浏览器 AK。该文件已忽略，不要提交。
./run.sh
```

`run.sh` 会启动 MediaMTX、样例推流和页面服务。页面在 `8090`，RTMP 在 `1935`，RTSP 在 `8554`，WebRTC 预览在 `8889`。

## 设置页

打开「设置」可分模块改配置，保存后热生效：

- **视频流**：只读展示推流地址（给外部录屏软件），可编辑站内 `rtsp_url` 与图传预览页
- **MQTT**：开关与主题
- **机型与 OCR**：选择机型（首版大疆 M30T）、开关 HUD OCR、默认视场角

## 录屏 OCR 贴地（M30T）

1. 外部软件把遥控器界面推到设置页里的推流地址（默认 `rtmp://板子IP:1935/live`）
2. 态势页片源选「RTMP 直播」，型号选「大疆 M30T · 广角 HUD」
3. OCR 可读飞机经纬度、俯角、方位角；**离地高度请在「手动补齐」里锁定**（画面上的 H 不可用）
4. 姿态优先级：手动锁定 > OCR > MQTT

扩展机型：在 `app/aircraft/` 新增适配器并在 `registry.py` 登记。

## 片源

右侧「片源」下拉由 `samples/catalog.json` 定义。

| 片源 | 说明 |
| --- | --- |
| 合成样例 · 西湖 · 先垂直再斜看 | 程序生成。前半段俯仰 -90°，后半段约 -50° |
| 杭州小牙坞 · 真实斜视 | 精灵 4 RTK，俯仰约 -18° |
| 肯尼亚草原 · 真实垂直向下 | Mini 4 Pro，云台 -90°，约 60 米 |
| 肯尼亚草原 · 真实斜视 | 同一区域，云台约 -46° |
| RTMP 直播 | 视频来自 RTMP；姿态可由 OCR / MQTT / 手动补齐 |

把自己的录像放进 `samples`，在 `samples/catalog.json` 增加 `id`、`label`、`video`、`srt` 后刷新页面。字幕需要经纬度、高度、云台俯仰和航向。缺少俯仰时，程序按垂直向下处理。

切换离线片源时，图传预览会改为推送当前录像。选择「RTMP 直播」会停止这段循环，把地址留给外部直播。

底图可在平面图、百度卫星图和离线卫星之间切换。离线卫星使用 Esri 影像，按视野缓存到 `cache/tiles`。肯尼亚草原区域没有百度卫星影像。

## 实时视频

两种接法都可以贴地：

1. **录屏 HUD OCR**（见上）：推遥控器整屏，站内识别姿态并手动补高度  
2. **行业机双通道**：视频 RTMP + MQTT OSD  

MQTT JSON 需要 `latitude`、`longitude`、`elevation`、`gimbal_pitch`、`gimbal_yaw`、`gimbal_roll`。程序只订阅 MQTT，不自带 broker。

## 目录

- `app/`：解码、姿态解析、贴地几何、页面服务、机型 OCR、设置存储
- `app/aircraft/`：可插拔机型适配（首版 `dji_m30t_hud`）
- `web/`：态势页与设置页
- `samples/`：可播放代理、字幕、片源目录、灾前样例图
- `vendor/mediamtx`：图传服务（v1.21.2）
- `tests/test_core.py`：坐标、视场、字幕、OCR 文本与片源时刻

## 限制

贴地使用单帧平面投影，没有和卫星图做图像配准。大疆 OcuSync / O3 图传在 Linux 上不能解码。整段录像会解码进内存，4K 原片需要先转成约 720p、每秒 8 帧的 H.264。

超过 GitHub 100MB 限制的原始片没有纳入版本库：`samples/hangzhou/DJI_0291.MOV`、`samples/kenya-oblique/source.lrf`。对应的可播放代理已包含在内。
