# New Life v0.6.1 验收记录

日期：2026-10-04。本次仅放大主播放列表头像，从28×28改为40×40逻辑像素；名称与播放提示放在头像右侧。按钮仍为57像素高，播放列与右侧图标保持对齐；原头像裁剪配置继续使用，迷你条头像保持28像素。

完整360项测试通过（65.44秒）。冻结EXE验证16组检查通过，覆盖中英文、深浅色、100%／150%／200%离屏渲染、媒体识别、图标和整包搬迁后便携配置恢复。已查看实际EXE中文深色主界面，头像、文字和播放图标没有重叠。公开的脱敏汇总见[验证摘要](verification-v0.6.1.json)；完整本机报告位于 `artifacts/release-verification/f59f6f60/results.json`，不上传日志和设备信息。

本轮未开启真实麦克风或更改系统设备。虚拟设备通话的真实听感仍按前版要求由用户配合测试。

## 交付

- `artifacts/release-v0.6.1/NewLife/NewLife.exe`
- [Windows 运行包](https://github.com/whathappenaaa/NewLife/releases/tag/v0.6.1)：`NewLife-v0.6.1-win-x64.zip`，156,085,569字节，336项。
- SHA256：`5b4295ea803015812820cb963d5c9ce8fec638eb264e59bd46483b55cf7f69f5`。

GitHub 开源发布时仅补入项目 MIT `LICENSE`，EXE 与依赖保持已验证的版本；重新检查 ZIP CRC、许可证内容、EXE哈希及无私人录音和数据库。上述360项测试和16组EXE检查为补入许可证前完成的结果，本轮没有重复运行音频测试。旧压缩包保存在本机 `artifacts/github-publication/NewLife-v0.6.1-win-x64-before-open-source.zip`，原 SHA256 为 `9601f52ffe8236c2a25a00979d3aa8b81d306aa0a8023eed6ce8f33965beacc5`。v0.6.0及更早运行包保留，旧音频和配置未迁移、未重设。

开发说明补充了显示尺寸与图片保存尺寸的区别，见[开发进度](开发进度-v0.6.md)。完整v0.6功能验收见[前版记录](validation-v0.6.md)。
