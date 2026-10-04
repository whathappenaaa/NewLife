# New Life v0.6.2 验收记录

日期：2026-10-04。此次新增六份自带音频及对应 `.newlife` 配置，复制到根目录 MUSIC，并随 Windows 运行包分发。音频、配置和旧发布包保留；除版本信息及打包目录外，没有修改播放或录音逻辑。

## 验证结果

- 六份媒体为48 kHz双声道PCM WAV，配置版本、文件名、大小和SHA256全部匹配；范围有效且至少1秒。
- 使用独立空配置目录扫描，六项名称、头像、背景、裁剪与播放范围精确恢复。源目录与复制后的十二个文件哈希相同，验证未修改它们。[音频与配置报告](docs/bundled-music-v0.6.2.json)
- 完整360项测试通过，耗时57.12秒。发布EXE一次通过16组验证：中英文、深浅色、100%／150%／200%缩放、主窗口／迷你条／帮助／快捷键、图标及整包搬迁后的默认目录与便携配置恢复。
- 从与EXE不同的工作目录启动发布版，不指定文件夹、使用全新profile，默认选中EXE旁的MUSIC。六套配置和24张图片全部正确加载，日志版本为0.6.2且无错误；未播放实体声音或修改Windows设置。[发布版资源加载报告](docs/frozen-bundled-music-v0.6.2.json)
- 完整软件包和单独MUSIC资源包均通过ZIP CRC校验；内附十二个文件的字节与原文件一致，使用说明及项目许可证内容正确；不含本机数据库、日志和删除恢复目录。[脱敏发布摘要](docs/verification-v0.6.2.json)

完整本机EXE报告位于 `artifacts/release-verification/865bf3f1/results.json`，不公开本机路径与设备记录。真实麦克风质量、长期混合录音与双方微信听感沿用前版的真实设备验收要求。

## 下载

[v0.6.2 Releases](https://github.com/whathappenaaa/NewLife/releases/tag/v0.6.2) 提供：

- `NewLife-v0.6.2-win-x64.zip`：198,131,830字节，348项；内含六份音频及配置。
- `NewLife-MUSIC-v0.6.2.zip`：41,366,094字节，13个文件；仅资源，适合已有播放器的用户。
- `SHA256SUMS.txt`：两个ZIP的校验值。

完整软件包SHA256：`5576a441b8bdced485657e30b5665be36de7e8147183dc4a4728ce14d59d3810`。

MUSIC资源包SHA256：`22796118d3c437bf0a0c181a0738758c72acd289155e56489dc5aed2a0af0de7`。

前版记录：[v0.6.1](docs/validation-v0.6.1.md)、[v0.6](docs/validation-v0.6.md)。
