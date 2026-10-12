# 态势地面站

在 Firefly ITX-3588J（RK3588）上运行的民用灾害态势地面站。浏览器打开本机页面，按无人机云台姿态把视频贴到地图上，用来和灾前影像对照。云台垂直向下时，画面贴在飞机正下方；斜视时只标出真实视场。

页面：`http://127.0.0.1:8090/`  
图传预览：`http://127.0.0.1:8889/live/`

视频解码使用瑞芯微 MPP，贴地投影在 CPU 上完成，不使用 NPU。

## 运行

依赖：Python 3、OpenCV、NumPy、GStreamer（含 `mppvideodec`）、ffmpeg。

```bash
cp config.example.json config.json
# 在 config.json 中填写百度浏览器 AK。该文件已忽略，不要提交。
./run.sh
```

`run.sh` 会启动 MediaMTX、样例推流和页面服务。页面在 `8090`，RTMP 在 `1935`，RTSP 在 `8554`，WebRTC 预览在 `8889`。

## 片源

右侧「片源」下拉由 `samples/catalog.json` 定义。

| 片源 | 说明 |
| --- | --- |
| 合成样例 · 西湖 · 先垂直再斜看 | 程序生成。前半段俯仰 -90°，后半段约 -50° |
| 杭州小牙坞 · 真实斜视 | 精灵 4 RTK，俯仰约 -18° |
| 肯尼亚草原 · 真实垂直向下 | Mini 4 Pro，云台 -90°，约 60 米 |
| 肯尼亚草原 · 真实斜视 | 同一区域，云台约 -46° |
| RTMP 直播 | 视频来自 RTMP，姿态另由 MQTT 提供 |

把自己的录像放进 `samples`，在 `samples/catalog.json` 增加 `id`、`label`、`video`、`srt` 后刷新页面。字幕需要经纬度、高度、云台俯仰和航向。缺少俯仰时，程序按垂直向下处理。

切换离线片源时，图传预览会改为推送当前录像。选择「RTMP 直播」会停止这段循环，把地址留给外部直播。

底图可在平面图、百度卫星图和离线卫星之间切换。离线卫星使用 Esri 影像，按视野缓存到 `cache/tiles`。肯尼亚草原区域没有百度卫星影像。

## 实时视频

只推视频不能贴到地图上。需要两路同时到达：

- 视频推到 `rtmp://板子IP:1935/live`，页面选择「RTMP 直播」
- 姿态发到 MQTT `127.0.0.1:1883`，主题 `thing/product/{序列号}/osd`

JSON 中需要 `latitude`、`longitude`、`elevation`、`gimbal_pitch`、`gimbal_yaw`、`gimbal_roll`。这是大疆上云 API 的 OSD，不是视频编码里的标准字段。行业机可通过 Pilot 2 同时推 RTMP 和 OSD。消费机 DJI Fly 没有这条官方出口，落地后使用存储卡上的 SRT 回放。

程序只订阅 MQTT，不自带 MQTT 服务。遥控器画面上的经纬度、高度和云台角度是软件叠加的，不会随 RTMP 视频一起传来。超过大约 2 秒没有姿态时，画面仍在，但不再贴到地图上。

## 目录

- `app/`：解码、姿态解析、贴地几何、页面服务、片源切换、预览推流
- `web/`：浏览器页面
- `samples/`：可播放代理、字幕、片源目录、灾前样例图
- `vendor/mediamtx`：图传服务（v1.21.2）
- `tests/test_core.py`：坐标、视场、字幕和片源时刻

## 限制

贴地使用单帧平面投影，没有和卫星图做图像配准。大疆 OcuSync / O3 图传在 Linux 上不能解码。整段录像会解码进内存，4K 原片需要先转成约 720p、每秒 8 帧的 H.264。

超过 GitHub 100MB 限制的原始片没有纳入版本库：`samples/hangzhou/DJI_0291.MOV`、`samples/kenya-oblique/source.lrf`。对应的可播放代理已包含在内。
