# New Life：GitHub 上传与更新教程

项目仓库：[whathappenaaa/NewLife](https://github.com/whathappenaaa/NewLife)  
软件下载：[Releases](https://github.com/whathappenaaa/NewLife/releases)  
作者署名：**Bilibili那年松江**

本次发布流程是：检查要公开的文件 → 添加 MIT 许可证和说明 → 创建公开仓库 → 提交源码 → 上传 Windows 运行包。本页也讲解以后如何自己更新。

## 1. 先分清三个位置

| 位置 | 保存什么 | 谁使用 |
|---|---|---|
| 电脑里的项目文件夹 | 源码、测试、开发环境与本地生成文件 | 你在 VS Code 中开发 |
| GitHub 仓库的 Code 页面 | 已提交的源码、文档、许可证和可分发资源 | 开发者阅读、修改或克隆 |
| GitHub 的 Releases 页面 | 每个版本的 Windows ZIP 运行包及更新说明 | 用户下载后解压运行 |

**保存文件**只修改电脑文件；**提交 Commit**在电脑记录一个版本；**推送 Push**把提交上传到 GitHub。**同步 Sync Changes**会先拉取远端变化，再推送本地提交。[VS Code 官方入门](https://code.visualstudio.com/docs/sourcecontrol/quickstart)

GitHub 的 Code → Download ZIP 以及 Releases 自动生成的 Source code 都是源码。没有安装 Python 的用户应下载 `NewLife-v版本号-win-x64.zip`，完整解压后运行 `NewLife.exe`。

## 2. 哪些文件应该公开

上传 `src`、`tests`、`assets`、`docs`、脚本、依赖列表、README、LICENSE、VS Code 配置及 `.gitignore`。项目根目录 `MUSIC` 中的内容也会公开，只放你准备随软件分发且拥有相应权限的音频、图片和 `.newlife` 配置。

`.venv` 是你电脑上的 Python 环境，`artifacts` 是生成的运行包与验证文件，`vendor/ffmpeg` 是下载的组件。这些不进入源码仓库，由 `.gitignore` 排除。不要公开个人录音、账号密码、访问令牌、本机数据库、日志或尚未准备发布的文件。

提交前看一遍 VS Code 的“更改”和“暂存的更改”：确认里面没有上述个人文件。**`.gitignore` 只阻止未被追踪的文件加入，不会自动移除已经提交的文件。**

## 3. 首次导入：VS Code 图形操作

这部分用于以后建立其他项目。本次 New Life 若已经显示 `origin` 远端和已有提交，直接看第 4 节，不要重复创建同名仓库。

1. 用 VS Code 打开项目文件夹或 `New Life.code-workspace`。
2. 按 `Ctrl+Shift+G` 打开“源代码管理”。若未建立 Git 仓库，点击“初始化仓库”。此时只是建立本地版本管理，还没上传。
3. 点击文件查看内容，确认后点击旁边的 `+` 暂存。全部文件都审核过以后才使用“暂存所有更改”。
4. 在消息框输入 `发布 New Life 初始源码`，点击“提交”。暂存相当于选择这一版要带哪些文件，提交相当于给这一版存档。
5. 按 `Ctrl+Shift+P`，运行 `Publish to GitHub`（发布到 GitHub）。按浏览器引导登录 GitHub，再回到 VS Code。
6. 输入仓库名，选择 **Publish to GitHub public repository**（发布为公开仓库）。你希望开源时选择 public；private 仅向获授权的人开放。
7. 完成后打开 GitHub 页面，检查 README、源码和 LICENSE 都存在，且没有私人录音或开发环境。[VS Code 官方入门](https://code.visualstudio.com/docs/sourcecontrol/quickstart)

首次提交若提示没有作者名称和邮箱，可在终端只为当前项目设置：

```powershell
git config user.name "你的公开署名"
git config user.email "从 GitHub Settings → Emails 复制你的 noreply 邮箱"
```

提交的作者信息会进入公开记录。填写这些信息不能替代 GitHub 登录，也无需把密码写进配置。

如果改用 GitHub 网页创建仓库，选择 **New repository → 名称 → Public → Create repository**。导入已有本地项目时，不在网页勾选自动创建 README、`.gitignore` 或 LICENSE，因为本地已有这些文件，重复初始化会增加合并步骤。[GitHub 创建仓库说明](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)

## 4. 以后每次修改：检查 → 暂存 → 提交 → 推送

1. 修改并保存代码；运行相应测试，实际打开软件检查本次改变。
2. 在“源代码管理”点击改过的文件，核对差异。只暂存本次需要发布的文件。
3. 输入能说明改变的提交消息，例如 `增大播放列表头像并保持标题对齐`。
4. 点击“提交”。此时 GitHub 尚未收到更新。
5. 点击“同步更改”，或“更多操作 → 推送”。随后在网页查看最新提交，确认上传成功。

你可以在 VS Code 终端查看状态：

```powershell
git status
git remote -v
git log -3 --oneline
```

`origin` 应指向 `https://github.com/whathappenaaa/NewLife.git`。终端版本的常规更新为：

```powershell
git add README.md
git commit -m "完善使用说明"
git push
```

把示例中的 `README.md` 换成本次实际改过、检查过的文件。推送提示远端有新提交时，先获取并处理差异；有冲突时逐个确认保留什么内容，再提交。不要用强制推送绕过问题。

新电脑开始开发时，不用手动搬动 `.venv`：克隆仓库后运行 `setup.ps1`，重新建立环境。详细步骤见 [README](../README.md)。

## 5. 发布用户能运行的 Windows 软件

只更新 GitHub 源码不会自动更新用户的 EXE。准备一个新版本时，先统一修改项目版本与打包脚本中的版本，再运行测试和 `build.ps1`，得到 `artifacts/NewLife-v新版本-win-x64.zip`。查看压缩包内容，并用解压后的 EXE 验证。

在 GitHub 仓库中：

1. 打开 **Releases → Draft a new release**（新建发布草稿）。
2. 选择版本标签，例如 `v0.6.2`；若是新标签，选择对应的源码分支或提交。
3. 写标题与更新说明，说明新功能、修复、适用系统和仍需验证的问题。
4. 把 Windows ZIP 拖入附件区。等待上传结束，核对文件名；可同时上传 SHA256 校验文件。
5. 附件齐全后点击 **Publish release**。打开公开页面，实际下载一次并验证。[GitHub 发布说明](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)

版本标签记录这一版对应的源码，ZIP 提供这一版的可运行程序。两者应保持一致。不要把 100 多 MB 的运行包加入普通源码提交；保留旧版发布，方便用户回退。

目前 `MUSIC` 已包含六份音频及配置。你之后录好要自带的声音，先放进根目录 `MUSIC`，在软件中设置头像、背景和播放范围，再连同 `.newlife` 包提交并重新打包。这里只放准备公开的资源。

## 6. 开源与免费分别是什么

New Life 的发布包免费提供。本项目自行编写的源码采用 MIT，允许别人使用、修改和再分发，包括商业使用；再分发时保留版权与许可证文字。[MIT 说明](https://choosealicense.com/licenses/mit/)

第三方组件遵循各自的许可证，详情见 [THIRD-PARTY-NOTICES.txt](../THIRD-PARTY-NOTICES.txt)。发布包保留对应许可和 FFmpeg 源码／构建资料。VB-CABLE 是第三方驱动，单独从 [官网](https://vb-audio.com/Cable/) 获取并遵守其条款；New Life 开源不改变驱动的许可，也不把它变成本项目的一部分。

## 7. 一次小练习

在你确认本次仓库已经发布后，给 README 增加一句真实的使用说明，保存，查看差异，暂存，提交，推送，再到 GitHub 验证。掌握这一步后，代码修改也遵循同样流程：先把功能做对，再给版本存档，最后上传。

## 8. 本次把自带音频加入 v0.6.2

原先音频在本机的旧发布目录中；`artifacts` 被忽略，因此只放在那里不会进入 GitHub 源码或以后生成的运行包。本次把六份 WAV 和同名 `.newlife` 原样复制到项目根目录 `MUSIC`，旧目录保持不变。

先用独立的空配置目录验证：播放器只凭这十二个文件，就能恢复名字、头像、背景、裁剪和播放范围。这个步骤可以发现“自己的电脑能用，换电脑却丢配置”的问题。

然后更新版本号、说明，运行测试与打包，将 `MUSIC` 连同源码修改提交并推送，最后发布 v0.6.2 和校验文件。完整软件包内附这些文件；单独的 `NewLife-MUSIC-v0.6.2.zip` 供已有播放器的用户使用。

以后你再添加自带音频：

1. 把媒体文件和同名 `.newlife` 一起放进项目根目录 `MUSIC`。
2. 在 VS Code 源代码管理检查新增文件，暂存、提交并同步到 GitHub。
3. 要让“直接下载软件”的用户也收到新增音频，还需要重新打包并发布新版到 Releases。仅推送源码，不会自动改变已发布的 ZIP。

