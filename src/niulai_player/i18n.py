"""Small live Chinese/English catalog. User data and device identifiers stay untouched."""
from __future__ import annotations

import re
import weakref
from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel, QLineEdit, QComboBox, QTabWidget, QDoubleSpinBox, QSystemTrayIcon, QWidget

_language = "zh_CN"
_saved = weakref.WeakKeyDictionary()
_filter = None

_EN = {
    "系统": "System",
    "置顶": "Pin mini",
    "录音": "Record",
    "播放列表": "Playlist",
    "播放范围": "Playback range",
    "结束并保存": "Finish & save",
    "撤销删除": "Undo delete",
    "查看详情": "Details",
    "导入结果": "Import results",
    "恢复默认头像": "Reset avatar",
    "恢复默认背景": "Reset background",
    "恢复完整播放范围": "Reset playback range",
    "片段至少保留 1 秒": "Keep at least 1 second",
    "点击输入准确时间": "Click to enter precise times",
    "双击精确编辑播放范围": "Double-click to edit precise times",
    "调整起点": "Adjust start",
    "调整终点": "Adjust end",
    "拖动播放位置": "Drag to seek",
    "先点击头像或名称播放，再拖动播放位置": "Click the avatar or name to play, then drag to seek",
    "拖动左右把手调整范围，松手保存；至少 1 秒。波形区域不会播放。": "Drag the end handles to adjust the range; release to save. Keep at least 1 second. The waveform does not start playback.",
    "连接并分享片段": "Connect & share clips",
    "完全断开通话（包括麦克风）": "Disconnect call and microphone",
    "片段分享已关闭，麦克风仍保持通话。": "Clip sharing is off; your microphone remains connected.",
    "片段分享已关闭，麦克风仍保持通话": "Clip sharing is off; your microphone remains connected",
    "已请求测试声音；发送电平仅表示本机输出，请让对方确认是否听到": "Test sound requested. The meter shows local output only; ask the other person to confirm they can hear it.",
    "发现上次未完成的删除记录，可点击撤销恢复音频和配置": "An unfinished deletion was found. Use Undo to restore the audio and configuration.",
    "撤销时间已结束，删除的音频和配置已进入系统回收站": "The undo period ended. Deleted audio and configurations are now in the Recycle Bin.",
    "头像已恢复默认": "Avatar reset",
    "背景已恢复默认": "Background reset",
    "删除恢复目录无效": "Invalid deletion recovery folder",
    "删除恢复记录无效": "Invalid deletion recovery record",
    "删除恢复记录的音频位置无效": "Invalid audio location in the deletion recovery record",
    "删除恢复目录不能是链接": "The deletion recovery folder cannot be a link",
    "删除暂存目录不能是链接": "The deletion staging folder cannot be a link",
    "只能删除当前文件夹内的真实音频文件": "Only actual audio files in the current folder can be deleted",
    "配置包不能是链接": "The configuration package cannot be a link",
    "删除暂存文件不能是链接": "Staged deletion files cannot be links",
    "原位置已有同名文件，未覆盖；请移开该文件后再次撤销": "A file with the same name exists and was not overwritten. Move it elsewhere, then retry Undo.",
    "删除暂存文件缺失，请检查回收站或暂存目录": "A staged file is missing. Check the Recycle Bin or staging folder.",
    "删除暂存文件已被修改，已保留供手动恢复": "A staged file changed and has been kept for manual recovery",
    "文件操作尚未完成，请稍后再切换文件夹": "A file operation is still running. Wait before changing folders.",
    "文件操作尚未完成，请稍后重试": "A file operation is still running. Try again shortly.",
    "界面配色：系统、深色或浅色，立即生效": "Appearance: system, dark or light; changes apply immediately",
    "通话安装、输入选择与测试；再点收起": "Call setup, microphone selection and test; click again to close",
    "设置后台快捷键；再点收起，未保存输入会保留": "Set global shortcuts; click again to close. Unsaved input is kept.",
    "复制音频或视频到当前文件夹；有导入结果时再点收起": "Copy audio or video into this folder; click again to close import results",
    "录音中点两次标记起点与终点，生成可立即播放的片段": "While recording, mark the start and end to create a clip you can play immediately",
    "麦克风：你本人讲话的真实设备": "Microphone: the physical device you speak into",
    "耳机／扬声器：你听声音的设备": "Headphones / speakers: the device you hear sounds through",
    "自动跟随系统；更换会影响其他软件，退出后保留。": "Follows Windows; device changes affect other apps and are kept after exit.",
    "删除选中的音频，可撤销": "Delete selected sounds; Undo is available",
    "先勾选音频，再批量删除": "Select sounds first to delete them together",
    "按 Esc 取消选择；删除后可撤销": "Press Esc to clear the selection; deletion can be undone",
    "发送电平只能证明音频已送入虚拟设备，请在通话软件中确认输入并让对方试听": "The meter only shows audio sent to the virtual device. Check your calling app's input and ask the other person to listen.",
    "再次点击导入按钮可收起此面板": "Click Import again to close this panel",
    "导入新文件": "Import new files",
    "再次点击此音频的快捷键按钮可收起，未保存输入会保留": "Click this sound's shortcut button again to close; unsaved input is kept",
    "检测虚拟音频设备": "Check virtual audio device",
    "让对方同时听见片段和你的讲话，需要安装 VB-CABLE。已安装时无需重复安装。": "Install VB-CABLE so the other person can hear clips and your voice together. If it is already installed, do not install it again.",
    "在通话软件里选择麦克风": "Choose the microphone in your calling app",
    "打开微信、QQ、Discord 或游戏的音频设置，将输入设备改为下方 CABLE Output。播放器不能替你更改软件内部的选择。": "Open audio settings in WeChat, QQ, Discord or your game and select the CABLE Output device shown below as the input. The player cannot change that app's internal selection.",
    "连接并试听测试声音": "Connect and send a test sound",
    "连接后播放测试声音，请对方确认是否听见。发送电平只证明音频已送入虚拟设备。": "After connecting, send a test sound and ask the other person to confirm they hear it. The meter only shows audio sent to the virtual device.",
    "片段分享关闭时，麦克风仍保持通话；完全断开请使用上方按钮。": "Your microphone remains connected when clip sharing is off. Use the button above to disconnect both.",
    "若音效被过滤，可调整通话软件的降噪与自动增益。本人声音有回声时，检查 Windows“侦听此设备”和声卡监听是否开启。": "If sound effects are filtered, adjust noise suppression and automatic gain in your calling app. For microphone echo, check Windows Listen to this device and sound card monitoring.",
    "将真实麦克风和片段送入虚拟设备；通话软件仍需选择 CABLE Output": "Send your physical microphone and clips to the virtual device; select CABLE Output in your calling app too",
    "先安装 VB-CABLE 并重新检测设备": "Install VB-CABLE, then detect devices again",
    "耳机": "Headphones",
    "配置包不可用": "Configuration package unavailable",
    '通话帮助': 'Call help',
    '收起通话帮助': 'Hide call help',
    '收起快捷键': 'Hide shortcuts',
    '耳机／扬声器': 'Headphones / speakers',
    '主题': 'Theme',
    '迷你置顶': 'Mini on top',
    '保存快捷键': 'Save shortcuts',
    '已保存的设备当前不可用': 'Saved device unavailable',
    '更换会影响其他软件，退出后保留。': 'Changes affect other apps and are kept after exit.',
    '按安装提示重启电脑，再点击重新检测；标准 CABLE Input 会自动识别。': 'Restart as instructed by the installer, then detect again. Standard CABLE Input is detected automatically.',
    '按安装提示重启电脑，再重新检测；播放器会自动检测标准 CABLE 设备。': 'Restart as instructed by the installer, then detect again. Standard CABLE devices are detected automatically.',
    '拖动头像或名称移动迷你条': 'Drag the avatar or name to move the mini player',
    '选择声音\n拖动头像或名称移动迷你条': 'Select a sound\nDrag the avatar or name to move the mini player',
    '未选择文件夹\n当前文件夹的声音；可拖入音频或视频添加': 'No folder selected\nSounds in this folder; drop audio or video files to add',
    '上次使用的音频文件夹不存在或无法访问，已切换到程序旁的 MUSIC，请重新选择文件夹': 'The previous audio folder is unavailable. Switched to MUSIC beside the application; choose another folder if needed.',
    '启动指定的音频文件夹不存在或无法访问，已切换到程序旁的 MUSIC，请重新选择文件夹': 'The requested audio folder is unavailable. Switched to MUSIC beside the application; choose another folder if needed.',
    '当前音频文件夹不可写，录音和配置保存不可用，请选择可写文件夹': 'This audio folder is read-only. Recording and configuration saving are unavailable; choose a writable folder.',
    '无法创建程序旁的 MUSIC 文件夹，请选择可写的音频文件夹': 'Could not create MUSIC beside the application. Choose a writable audio folder.',
    '选择文件夹': 'Choose folder',
    '未选择文件夹': 'No folder selected',
    '点击打开当前文件夹': 'Open this folder',
    '分享给通话对方': 'Share sounds to call',
    '正在连接…': 'Connecting…',
    '正在分享 · 点击关闭': 'Sharing · Click to turn off',
    '界面配色': 'Appearance',
    '跟随系统': 'Follow system',
    '深色': 'Dark',
    '浅色': 'Light',
    '我说话的麦克风': 'Microphone I speak into',
    '我听声音的耳机／扬声器': 'Headphones / speakers I listen through',
    '打开 Windows 声音设置': 'Open Windows sound settings',
    '退出后保留系统设置': 'System settings are kept after exit',
    '设置通话麦克风并连接': 'Set call microphone and connect',
    '自动跟随 Windows。你可以在这里或系统声音设置中更换设备；更换会影响其他软件，退出后保留。': 'Follows Windows automatically. Change devices here or in Windows sound settings. Changes affect other apps and are kept after exit.',
    '耳机／扬声器：你听播放器声音的设备。\n麦克风：你本人讲话的真实设备，不要选 CABLE Output。\n电脑录音自动录制当前扬声器的声音；通话虚拟设备自动检测。': 'Headphones / speakers: where you hear sounds.\nMicrophone: your physical microphone, not CABLE Output.\nComputer recording follows your speakers; the virtual call device is detected automatically.',
    '已更改系统声音设备，其他软件也会跟随；退出后保留': 'System sound device changed. Other apps follow it; the change is kept after exit.',
    '设备更换可能短暂中断声音，录音会继续保存。': 'Changing devices may briefly interrupt sound; recording continues.',
    '系统当前选择了虚拟线；播放器保留真实耳机／麦克风，避免声音回路。': 'Windows selected a virtual cable. The player keeps physical speakers / microphone to avoid a feedback loop.',
    '暂时无法跟随系统设备，请停止录音和通话后重新检测，或打开 Windows 声音设置检查。': 'Cannot follow this system device yet. Stop recording and disconnect the call, then refresh, or check Windows sound settings.',
    '将 Windows 默认通信输入设为 CABLE Output；断开或退出后保留。通话软件若指定了其他麦克风，仍需手动选择下方设备。': 'Set the Windows communications microphone to CABLE Output; keep it after disconnect or exit. Calling apps with a fixed microphone still need the device below selected.',
    '系统设备设置保持不变': 'System sound settings are kept.',
    '已将通话麦克风设为 CABLE Output；退出后保留。通话软件仍需选择该设备。': 'Communications microphone set to CABLE Output; kept after exit. Select this device in your calling app too.',
    '系统设备修改会保留，退出不会恢复': 'System device changes are kept after exit.',

    "正在缓冲音频…": "Buffering audio…",
    "头像\n裁剪": "Avatar\ncrop", "应用后的预览": "Applied preview", "⚠ 配置": "⚠ Config",
    "当前文件夹的声音；可拖入音频或视频添加": "Sounds in this folder; drop audio or video files to add",
    "松开以复制到当前播放文件夹": "Drop to copy into the current playlist folder",
    "将图片拖到头像或片段背景，可调整显示区域": "Drop an image onto an avatar or row background to adjust its framing",
    "松开以裁剪背景": "Drop to crop the background",
    "还没有声音\n选择文件夹，或拖入音频／视频（只播放声音）": "No sounds yet\nChoose a folder or drop audio / video files (audio only)",
    "导入音频或视频到当前文件夹": "Import audio or video into the current folder", "媒体文件": "Media files",
    "音轨": "Audio track", "未标注语言": "Unspecified language", "重新裁剪背景…": "Re-crop background…",
    "正在连接通话…": "Connecting call…", "外观已保存": "Appearance saved", "背景已保存": "Background saved",
    "音轨已保存": "Audio track saved", "快捷键已保存": "Shortcut saved", "配置保存中": "Saving configuration",
    "配置已保存": "Configuration saved",
    "本地播放与录音仍可使用\n主窗口开启“分享片段到通话”，对方才能听到片段。\n若音效被过滤，可调整通话软件的降噪与自动增益。": "Local playback and recording remain available.\nEnable “Share sounds to call” in the main window to send clips.\nIf sound effects are filtered, adjust noise suppression and automatic gain in your call app.",
    "请检查音频设备与通话软件设置，\n然后在主窗口开启“分享片段到通话”。": "Check the audio devices and call app settings,\nthen enable “Share sounds to call” in the main window.",
    "配置包操作已取消，原文件保持不变": "Configuration package operation cancelled; original files preserved",
    "配置包中的图片裁剪范围无效": "Invalid image crop in the configuration package",
    "配置包版本不受支持，已保留原包": "Unsupported configuration package version; original package preserved",
    "配置包内容无效，已保留原包": "Invalid configuration package; original package preserved",
    "配置包文件列表无效": "Invalid configuration package file list",
    "配置包解压大小超出限制": "Configuration package extraction exceeds the size limit",
    "配置包含有不安全的文件条目": "The configuration package contains unsafe file entries",
    "配置包说明文件过大": "Configuration manifest exceeds the size limit",
    "配置包图片缺失或含未知文件": "Configuration package images are missing or unknown files are present",
    "配置包损坏，已保留原包": "Damaged configuration package; original package preserved",
    "音频与配置包不匹配，已保留原包": "Media does not match the configuration package; original package preserved",
    "配置包图片无效或超过 4000 万像素": "Configuration image is invalid or exceeds 40 million pixels",
    "配置包已被其他操作修改，请刷新后重试": "Configuration package changed elsewhere; refresh and try again",
    "配置包大小超出限制": "Configuration package exceeds the size limit",
    "配置包图片必须非空且不超过 25 MiB": "Configuration images must not be empty or exceed 25 MiB",
    "配置包校验失败": "Configuration package verification failed",
    "媒体在保存配置期间发生变化，请刷新后重试": "Media changed while saving its configuration; refresh and try again",
    "媒体解码组件缺失，请使用包含 FFmpeg 的完整发布包": "Media decoder missing; use the complete release containing FFmpeg",
    "媒体信息不完整": "Incomplete media information", "此文件没有可播放的音轨": "This file has no playable audio track",
    "无法确定音轨时长，请检查文件是否完整": "Could not determine audio duration; check that the file is complete",
    "选定音轨已不存在，请重新选择": "The selected audio track no longer exists; select another track",
    "音轨编号无效": "Invalid audio track index", "媒体处理已取消": "Media processing cancelled",
    "媒体解码超时": "Media decoding timed out", "解码器返回了不完整的音频帧": "The decoder returned an incomplete audio frame",
    "波形采样点数无效": "Invalid waveform sample count",
    "裁剪背景": "Crop background", "选择此音频，可批量删除": "Select this sound for batch deletion", "全选当前列表": "Select all listed sounds",
    "删除所选…": "Delete selected…", "取消选择": "Clear selection", "请将一张图片拖到目标头像或片段行": "Drop one image onto the target avatar or sound row",
    "未连接通话": "Call disconnected", "分享片段到通话": "Share sounds to call", "仅麦克风": "Microphone only", "麦克风＋片段": "Microphone + sounds",
    "开启后连接麦克风并分享片段；关闭仅停止分享片段，本人讲话继续。": "Enable to connect the microphone and share sounds. Disable to stop sharing sounds while keeping your voice connected.",
    "拖动序号调整顺序": "Drag the number to reorder",
    "设置这个声音的全局快捷键": "Set a global shortcut for this sound",
    "当前文件夹的声音": "Sounds in this folder", "播放模式": "Playback mode",
    "迷你模式": "Mini mode", "文件夹": "Folder", "选择保存与播放声音的文件夹": "Choose a folder for sounds and recordings",
    "搜索音频": "Search sounds", "导入": "Import", "上一段": "Previous", "下一段": "Next",
    "展开主窗口": "Open main window", "隐藏到托盘": "Hide to tray", "隐藏到托盘，保持运行": "Hide to tray; keep running",
    "选择声音": "Select a sound", "当前文件夹": "Current folder", "设置": "Settings", "最小化": "Minimize",
    "序号": "No.", "播放列表\n点击头像／名称播放": "Sounds\nClick avatar / name to play",
    "播放范围\n拖动两端调整": "Playback range\nDrag the handles", "时长": "Length", "快捷键": "Shortcuts",
    "波形区域不触发播放 · 最短 1 秒 · 右键查看更多操作": "Waveform edits only · Minimum 1 second · Right-click for more",
    "还没有声音\n选择文件夹，或导入 WAV / MP3 / FLAC / OGG": "No sounds yet\nChoose a folder or import WAV / MP3 / FLAC / OGG",
    "没有匹配的声音": "No matching sounds", "未设置": "Not set", "不可播放": "Unavailable", " 秒": " s",
    "录音来源：": "Record:", "电脑": "Computer", "麦克风": "Microphone", "都录制": "Both",
    "标记起点": "Mark start", "标记终点": "Mark end", "开始录音": "Record", "停止录音": "Stop recording",
    "保存到当前文件夹": "Saved to the current folder", "播放／暂停": "Play / pause",
    "立即停止片段，麦克风通话继续": "Stop sound; keep the microphone connected",
    "自己听": "Only me", "自己＋通话": "Me + call", "片段去向": "Send sound to",
    "配置通话": "Set up call", "连接通话": "Connect call", "断开通话": "Disconnect",
    "关闭通话输出并断开真实麦克风通路；恢复临时通信输入配置。": "Close call output and disconnect the physical microphone route; restore the temporary communications input setting.",
    "将真实麦克风持续送入虚拟设备；片段按所选去向播放。": "Continuously route the physical microphone to the virtual device. Sounds use the selected destination.",
    "选择通话音频设备，并查看虚拟麦克风安装指引。": "Choose call audio devices and view the virtual microphone installation guide.",
    "通话未配置": "Set up call", "通话已连接": "Call connected",
    "停止录音后才可收起为迷你条": "Stop recording before switching to mini mode",
    "收起为单行迷你条": "Switch to the single-row mini player", "录音期间不能切换文件夹": "Cannot change folders while recording",
    "文件夹就是播放列表": "The folder is your playlist", "未选择文件夹": "No folder selected",
    "请先选择文件夹": "Choose a folder first", "就绪 · 点击头像或名称播放，再点停止": "Ready · Click avatar or name to play; click again to stop",
    "选择播放与录音文件夹": "Choose a sound and recording folder", "导入音频到当前文件夹": "Import audio into the current folder",
    "音频文件 (*.wav *.mp3 *.flac *.ogg)": "Audio files (*.wav *.mp3 *.flac *.ogg)",
    "停止片段": "Stop sound", "退出 New Life": "Quit New Life", "打开 New Life": "Open New Life",
    "录音、通话与快捷键继续。右键托盘图标可退出。": "Recording, calls and shortcuts continue. Right-click the tray icon to quit.",
    "清空搜索后，可以拖动序号调整列表顺序": "Clear the search before dragging numbers to reorder",
    "保存": "Save", "取消": "Cancel", "关闭": "Close", "应用": "Apply", "重置": "Reset",
    "精确设置播放范围…": "Set exact playback range…", "另存当前范围为新 WAV": "Save this range as a new WAV",
    "重命名…": "Rename…", "更换头像图片…": "Choose avatar image…", "重新裁剪头像…": "Adjust avatar crop…",
    "设置头像表情…": "Set avatar emoji…", "背景颜色…": "Background color…", "背景图片…": "Background image…",
    "设置快捷键…": "Set shortcut…", "清除背景图片": "Remove background image", "打开所在文件夹": "Open containing folder",
    "删除音频…": "Delete audio…", "起点": "Start", "终点": "End", "至少选择 1 秒；保存后更新播放范围": "Select at least 1 second. Saving updates the range.",
    "音频不足 1 秒，无法设置片段": "Audio is shorter than 1 second and cannot be used as a clip",
    "范围不足 1 秒，请调整起点或终点": "The range must be at least 1 second. Adjust either endpoint.",
    "重命名": "Rename", "音频名称（扩展名自动保留）": "Audio name (extension is kept)", "片段背景颜色": "Sound background color",
    "选择头像图片": "Choose avatar image", "选择背景图片": "Choose background image",
    "图片 (*.png *.jpg *.jpeg *.bmp *.webp)": "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
    "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)": "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
    "头像表情": "Avatar emoji", "输入一个表情，例如 🐮、👏、🎵": "Enter an emoji, e.g. 🐮, 👏 or 🎵", "删除音频": "Delete audio",
    "音频设备": "Audio devices", "通话设置": "Call setup", "常规": "General", "保存设置": "Save settings",
    "选择实际使用的设备。断开后不会自动切到其他设备。": "Choose your devices. Disconnected devices are not replaced automatically.",
    "真实麦克风": "Physical microphone", "电脑声音来源": "Computer audio source", "本地播放设备（建议耳机）": "Local playback device (headphones recommended)",
    "发送到通话的虚拟设备（CABLE Input）": "Virtual call output (CABLE Input)", "重新检测设备": "Refresh devices",
    "请选择设备": "Select a device", "已保存的设备当前不可用": "Saved device is currently unavailable",
    "!  尚未检测到 VB-CABLE": "!  VB-CABLE was not detected",
    "让对方同时听见片段和你的讲话，\n需要先安装虚拟音频设备。": "To send sounds together with your voice,\ninstall a virtual audio device first.",
    "下载并安装": "Download and install", "从 VB-Audio 官网下载，以管理员身份运行安装程序。": "Download from VB-Audio and run the installer as administrator.",
    "下载并安装 VB-CABLE": "Download and install VB-CABLE",
    "前往官网下载 VB-CABLE": "Download VB-CABLE from its official site", "重启后重新检测": "Restart, then check again",
    "按安装提示重启电脑，再重新检测，并在音频设备中选择 CABLE Input。": "Restart if requested, refresh devices, then select CABLE Input under Audio devices.",
    "已安装，重新检测": "Already installed? Check again", "设置通话软件": "Set up your calling app",
    "在微信 / QQ / Discord 或游戏中，将麦克风选择为：": "In WeChat, QQ, Discord or your game, select this microphone:", "复制": "Copy",
    "本地播放与录音仍可使用\n连接通话后，选择“自己＋通话”才会发送片段。\n若音效被过滤，可调整通话软件的降噪与自动增益。": "Local playback and recording still work.\nSelect Me + call to send sounds after connecting.\nIf sounds are filtered, adjust noise reduction and automatic gain in your calling app.",
    "发送片段音量": "Sound send volume", "麦克风音量": "Microphone volume",
    "音量调整立即生效；停止片段不会关闭麦克风。": "Levels apply immediately. Stopping a sound keeps your microphone connected.",
    "点击输入框，按下一个快捷键组合": "Click the field and press a key combination",
    "后台可用；按住不重复触发。留空可取消绑定。": "Works in the background. Holding a key does not repeat. Leave empty to clear.",
    "点击输入框，再按下组合键。快捷键后台生效，按住不会重复触发。留空即可取消。": "Click a field and press a key combination. Shortcuts work in the background without repeating when held. Leave empty to clear.",
    "编辑快捷键时会暂停本软件的全局快捷键。保存时会检查重复与系统占用。": "This app's shortcuts are suspended while editing. Saving checks duplicates and system conflicts.",
    "开始／停止录音": "Start / stop recording", "标记录音起点／终点": "Mark recording start / end",
    "迷你条保持在其他窗口上方": "Keep the mini player on top", "文件夹就是播放列表；录音与另存片段写入当前文件夹。": "The folder is your playlist. Recordings and exported clips go into that folder.",
    "关闭窗口后留在系统托盘，录音和通话可以继续。": "Hide to tray to keep recording and calls running. × quits the app.",
    "隐藏到托盘会保留录音与通话；× 按钮会真正退出软件。": "Hide to tray keeps recording and calls running. × quits the app.",
    "录音期间不能切换文件夹或收起为迷你条。": "You cannot change folders or enter mini mode while recording.",
    "头像／名称点击播放，再点停止；波形只用来调整范围。": "Click the avatar or name to play, click again to stop. The waveform only edits the range.",
    "正在检测音频设备…": "Checking audio devices…", "请先停止录音并断开通话，才能更换设备。": "Stop recording and disconnect the call before changing devices.",
    "通话音频已连接": "Call audio connected", "麦克风正在送入虚拟设备。\n通话软件需选择 CABLE Output。": "Your microphone is routed into the virtual device.\nSelect CABLE Output in the calling app.",
    "已检测到虚拟音频设备": "Virtual audio device detected",
    "请检查音频设备与通话软件设置，\n然后在主窗口点击“连接通话”。": "Check the device and calling app settings,\nthen click Connect call in the main window.",
    "暂无声音，请展开导入": "No sounds. Open the main window to import.",
    "拖动左右把手调整范围，松手保存；至少 1 秒。波形区域不会播放。": "Drag either handle to edit; release to save. Minimum 1 second. The waveform does not trigger playback.",
    "片段播放范围": "Clip playback range", "点击头像或名称播放，再点停止": "Click avatar or name to play; click again to stop",
    "再点停止": "Click to stop", "点击播放": "Click to play", "音量": "Volume", "音量 · 点击展开，滚轮调节": "Volume · Click to expand; scroll to adjust",
    "裁剪头像": "Crop avatar", "拖动图片调整位置，滚轮或滑块缩放": "Drag to reposition; scroll or use the slider to zoom",
    "缩放": "Zoom", "图片无法读取，请选择其他图片。": "Cannot read this image. Choose another image.",
    "图片过大，请选择尺寸更小的图片。": "This image is too large. Choose a smaller image.",
    "一键临时配置并连接": "Set up temporarily and connect", "退出时恢复原设备配置": "Restore the previous device configuration when exiting",
    "测试通话声音": "Test call sound", "先连接通话，再播放测试声音。": "Connect the call before playing the test sound.",
    "应选的通话麦克风": "Microphone to select in the calling app", "真实麦克风电平": "Microphone level", "通话发送电平": "Call send level",
    "本地播放电平": "Local output level", "诊断信息": "Diagnostics", "等待音频状态": "Waiting for audio status",
    "临时配置仅作用于本软件，通话软件仍需选择下方麦克风。": "Temporary setup applies to this app. Select the microphone below in your calling app.",
    "范围已保存": "Range saved", "设置已保存": "Settings saved", "请先停止录音，再切换文件夹": "Stop recording before changing folders",
    "音频文件夹不存在或无法访问，请重新选择": "The audio folder is missing or unavailable. Choose another folder.",
    "当前文件夹无法访问，已停止片段播放，请重新选择文件夹": "This folder is unavailable. Playback stopped; choose another folder.",
    "片段已选择发送到通话，请先连接通话音频设备": "Sound sending is enabled. Connect the call audio device first.",
    "尚未检测到 VB-CABLE。请在设置 → 通话设置中查看安装指引": "VB-CABLE was not detected. See Settings → Call setup for installation instructions.",
    "音频不足 1 秒，无法播放片段": "Audio must be at least 1 second long",
    "请先停止录音并断开通话，再修改音频设备": "Stop recording and disconnect the call before changing audio devices",
    "界面预览模式：未开启音频设备": "Interface preview: no audio devices are open",
    "退出前请注意": "Before New Life closes",
    "软件即将退出，但以下操作未完成，请按提示检查：": "New Life will close, but these operations need your attention:",
    "临时将 Windows 默认通信输入设为 CABLE Output；断开或退出时恢复。通话软件若指定了其他麦克风，仍需手动选择下方设备。": "Temporarily set the Windows default communications input to CABLE Output; restore it on disconnect or exit. If your calling app uses a specific microphone, select the device below manually.",
    "先安装 VB-CABLE 并重新检测设备，再使用一键配置。": "Install VB-CABLE and refresh devices before using temporary setup.",
    "片段和麦克风送入通话": "Sounds and microphone are routed to the call",
    "仅麦克风送入通话": "Only the microphone is routed to the call",
    "请选择标准 CABLE Input；一键配置不支持其他虚拟线": "Choose the standard CABLE Input. Temporary setup does not support other virtual cables.",
    "未检测到完整的 CABLE Input／CABLE Output 配对": "A complete CABLE Input / CABLE Output pair was not found",
    "通话软件需要选择 CABLE Output": "Select CABLE Output as the microphone in your calling app",
    "临时系统配置不可用，请在 Windows 和通话软件中手动选择 CABLE Output": "Temporary setup is unavailable. Select CABLE Output manually in Windows and your calling app.",
    "尚未确认标准虚拟线配对，请刷新设备后重试，或手动选择 CABLE Output": "The standard virtual cable pair is not confirmed. Refresh devices and retry, or select CABLE Output manually.",
    "请先开启通话输出，再播放测试声音": "Connect call output before playing the test sound",
    "测试声音正在送入虚拟麦克风，请让对方确认是否听到": "The test sound is being sent to the virtual microphone. Ask the other person to confirm they can hear it.",
    "当前预览模式不提供测试声音": "Test sound is unavailable in preview mode",
    "头像已保存": "Avatar saved", "系统设置恢复失败": "Could not restore system settings",
    "尚未临时配置通信输入": "Temporary communications input is not configured",
    "系统设置服务已关闭": "The system settings service is closed",
    "恢复记录无效，未修改系统；请手动检查默认通信输入": "The recovery record is invalid. No system change was made; check the default communications input manually.",
    "另一份 New Life 正在管理通信输入，请先退出另一份软件": "Another New Life instance manages the communications input. Close that instance first.",
    "CABLE Output 当前不可用，未修改系统": "CABLE Output is unavailable. No system change was made.",
    "临时通信输入已配置，原设备记录保持不变": "Temporary input is configured; the original device record is preserved",
    "通信输入原本就是 CABLE Output，无需恢复": "The input was already CABLE Output. No restoration is needed.",
    "原通信输入无法确认，未修改系统": "The original input could not be confirmed. No system change was made.",
    "通信输入已被其他操作更改，本次未接管": "The communications input was changed elsewhere. New Life did not take control.",
    "Windows 未确认通信输入切换": "Windows did not confirm the communications input change",
    "已临时配置 CABLE Output；断开或退出时恢复": "CABLE Output is temporarily configured; the original input will be restored on disconnect or exit",
    "没有待恢复的通信设置": "No communications settings need restoration",
    "通信输入已改变，保留当前选择": "The communications input was changed elsewhere; keeping the current choice",
    "原麦克风当前不可用，已保留恢复记录": "The original microphone is unavailable. The recovery record has been kept.",
    "Windows 未确认恢复原通信输入": "Windows did not confirm restoration of the original communications input",
    "已恢复原默认通信输入": "The original default communications input has been restored",
    "通信设置由另一份软件管理": "Another app instance manages the communications settings",
    "临时通话设备配置仅支持 Windows": "Temporary call device setup requires Windows",
    "目标输入设备当前不可用，未修改 Windows 设置": "The target input device is unavailable. Windows settings were not changed.",
    "Windows 未确认通信输入切换，已保留恢复信息": "Windows did not confirm the input change. Recovery information has been kept.",
    "支持临时配置默认通信输入": "Temporary default communications input setup is supported",
    "创建 Windows 音频接口": "Create Windows audio interface", "初始化 Windows 音频接口": "Initialize Windows audio interface",
    "读取设备标识": "Read device ID", "读取设备状态": "Read device state", "读取设备名称": "Read device name",
    "枚举音频设备": "List audio devices", "读取默认通信输入": "Read default communications input",
    "切换默认通信输入": "Switch default communications input", "读取设备数量": "Read device count", "读取音频设备": "Read audio device",
    "同名音频过多，请使用另一个名称": "Too many sounds have this name. Choose another name.",
    "此媒体库由更新版本创建，请使用匹配的播放器版本": "This library was created by a newer release. Use the matching player version.",
    "媒体库已经关闭": "The media library is closed", "仅支持 WAV、MP3、FLAC 和 OGG Vorbis 音频": "Supported formats: WAV, MP3, FLAC and OGG Vorbis",
    "音频没有可读取的声音数据": "This file has no readable audio data", "音频范围必须是有效数值": "Audio endpoints must be valid numbers",
    "背景色必须为有效的十六进制颜色": "Enter a valid hexadecimal background color", "播放顺序不能包含重复音频": "Playback order cannot contain duplicate sounds",
    "不能混合排列不同文件夹中的音频": "Sounds in different folders cannot be reordered together", "目标音频文件夹不可用": "The destination audio folder is unavailable",
    "片段范围无效": "Invalid clip range", "音源已改变或片段不足 1 秒，请刷新并重新标记范围": "The audio changed or the range is shorter than 1 second. Refresh and select the range again.",
    "文件名不能为空，不能包含路径、特殊字符或末尾句点": "Names cannot be empty or contain paths, special characters or a trailing period",
    "不能使用 Windows 保留的设备名称": "Windows reserved device names cannot be used",
    "图片支持 PNG、JPG、BMP、GIF 和 WebP": "Supported images: PNG, JPG, BMP, GIF and WebP",
    "图片必须非空且不超过 25 MiB": "Images must not be empty or larger than 25 MiB",
    "头像裁切范围必须是整数像素 (x, y, 宽, 高)": "Avatar crop coordinates must be integer pixels (x, y, width, height)",
    "头像裁切必须是图片内的正方形": "The avatar crop must be a square within the image", "头像裁切范围超出原图": "The avatar crop extends beyond the original image",
    "无法读取图片，或图片超过 4000 万像素": "The image cannot be read or exceeds 40 million pixels",
    "无法保存裁切头像，请检查磁盘空间及文件夹权限": "Cannot save the cropped avatar. Check disk space and folder permissions.",
    "波形采样点数必须为 1 至 16384": "Waveform samples must be between 1 and 16384",
    "音频在生成波形时发生变化，请重试": "The audio changed while generating the waveform. Retry.",
    "图片资源必须通过图片选择器复制到媒体库": "Select images through the image picker to copy them into the library",
    "裁切坐标必须关联保留的头像原图": "Crop coordinates must refer to the preserved original avatar image",
    "音频不在媒体库中，请先刷新文件夹": "This audio is no longer in the library. Refresh the folder.", "音频不在媒体库中，请先刷新": "This audio is no longer in the library. Refresh first.",
    "头像原图必须来自所选图片或已保存的图片资源": "The avatar original must be a selected image or saved image asset",
    "音频列表已改变，请刷新后重试": "The audio list changed. Refresh and retry.", "音频在生成波形时被截断，请刷新后重试": "The audio was truncated while generating the waveform. Refresh and retry.",
    "数据库备份校验失败，已取消升级": "The database backup could not be verified. Upgrade was cancelled.", "音源在导出期间被截断，已取消保存": "The source audio was truncated during export. Saving was cancelled.",
    "标记片段必须至少 1 秒，且在已经录制的范围内": "Marked clips must be at least 1 second and within the recorded audio",
    "音频文件没有可播放内容": "The audio file has no playable content", "所选音频设备已断开或改变，请重新选择": "The selected audio device disconnected or changed. Select it again.",
    "本地监听请选择耳机或扬声器，不能选择虚拟通话线": "Choose headphones or speakers for local output, not a virtual cable",
    "播放范围无效": "Invalid playback range", "真实麦克风不能选择虚拟线，否则会形成声音回路": "Use a physical microphone. Selecting a virtual cable would cause feedback.",
    "请先断开通话，再更换通话设备": "Disconnect the call before changing its audio device",
    "通话输出必须选择虚拟音频线 CABLE Input，不会回退到普通扬声器": "Call output requires the virtual CABLE Input device. It will not fall back to speakers.",
    "未知录音来源": "Unknown recording source", "已经在录音": "Recording is already running", "开始录音后才能标记片段": "Start recording before marking a clip",
    "请先连接通话设备，再发送测试音": "Connect the call device before sending a test sound", "请先停止片段播放，再发送测试音": "Stop clip playback before sending a test sound",
    "测试时长无效": "Invalid test duration", "所选音频设备不存在，请重新选择": "The selected audio device was not found. Select another device.",
    "音频设备已改变，请重新选择": "The audio device changed. Select it again.", "音频引擎已关闭": "The audio engine is closed",
    "不能用虚拟通话线作为本地监听": "A virtual call cable cannot be used for local output",
    "录音与通话必须使用同一个真实麦克风；请先结束当前使用再更换": "Recording and calls must use the same physical microphone. Stop the active connection before changing it.",
    "片段不足 1 秒，起点仍保留，请稍后再标记终点": "The clip is shorter than 1 second. The start mark is kept; mark the end a little later.",
    "电脑声音请选择真实输出设备的 loopback，不能选择通话线": "Choose a physical output's loopback for computer audio, not the virtual call cable",
    "采集缓冲暂时溢出，缺失时段将保留为空白，请降低系统负载": "The capture buffer overflowed. Missing audio will remain silent; reduce system load.",
    "单个 WAV 已接近 4 GB，录音已安全保存；请开始新的录音": "This WAV is close to 4 GB. The recording was saved safely; start a new recording.",
    "录音数据不完整，原始文件仍然保留": "Recording data is incomplete. The original file has been preserved.",
    "快捷键不能为空": "A shortcut cannot be empty", "请至少搭配 Ctrl、Alt 或 Shift，避免影响日常输入": "Include Ctrl, Alt or Shift to avoid interfering with typing",
    "请设置一个组合键，例如 Ctrl+Alt+1": "Set a key combination, such as Ctrl+Alt+1", "暂不支持这个按键，请使用字母、数字、F1～F24 或常见控制键": "Use a letter, number, F1–F24 or common control key",
    "快捷键已占用": "The shortcut is already in use", "输出设备": "Output device", "电脑声音设备": "Computer audio device",
    "操作失败": "Operation failed", "New Life 启动失败": "New Life could not start",
    "无法建立单实例连接。请先关闭另一份 New Life，再重新启动。": "Could not establish the single-instance connection. Close other New Life instances and restart.",
    "详细信息已保存到日志。": "Details have been saved to the log.",
    "通话设备已改变，本次未修改系统输入，请重新连接通话": "The call device changed. The system input was not modified; reconnect the call.",
    "音频处理失败": "Audio processing failed", "音频和录音": "Audio and recording",
    "当前为无音频预览模式，录音请使用正常启动方式": "This is a silent preview. Start normally to record audio.",
    "当前为无音频预览模式，无法连接通话": "Calls are unavailable in silent preview mode",
    "音频不足 1 秒，无法创建播放片段": "The audio is shorter than 1 second and cannot be made into a clip",
    "请先选择真实麦克风": "Select a physical microphone first", "请先选择输出设备": "Select an output device first", "请先选择电脑声音设备": "Select a computer audio device first",
    "请先停止当前片段，再播放测试声音": "Stop the current sound before playing the test sound",
    "已请求测试声音，请让对方确认是否听到": "Test sound requested. Ask the other person to confirm they can hear it.",
    "查看音频诊断": "Show audio diagnostics", "收起音频诊断": "Hide audio diagnostics",
}

_TEMPLATES = {
    "删除选中的 {count} 个音频，可撤销": "Delete {count} selected sounds; Undo is available",
    "撤销最近删除的 {count} 项": "Undo deletion of {count} items",
    "成功添加 {added} 个，跳过 {skipped} 个，失败 {failed} 个": "Added {added}, skipped {skipped}, failed {failed}",
    "已添加：{name}": "Added: {name}",
    "已跳过：{name}": "Skipped: {name}",
    "添加失败：{name} · {reason}": "Failed: {name} · {reason}",
    "已导入 {imported} 项，跳过 {skipped} 项，失败 {failed} 项": "Imported {imported}, skipped {skipped}, failed {failed}",
    "已导入 {imported} 项，跳过 {skipped} 项，失败 {failed} 项；详情中可查看原因": "Imported {imported}, skipped {skipped}, failed {failed}. See Details for reasons.",
    "已删除 {count} 项，可在 30 秒内撤销": "Deleted {count} items. Undo is available for 30 seconds.",
    "已删除 {count} 项，可在 30 秒内撤销；失败 {failed} 项\n{detail}": "Deleted {count} items; Undo is available for 30 seconds. Failed: {failed}\n{detail}",
    "已恢复 {count} 项音频及配置": "Restored {count} audio files and configurations",
    "已恢复 {count} 项音频及配置；仍可重试撤销\n{detail}": "Restored {count} audio files and configurations. Retry Undo for the remaining items.\n{detail}",
    "已恢复 {count} 项音频及配置；音频及配置已恢复，暂存目录清理未完成：{error}": "Restored {count} audio files and configurations. The staging folder could not be cleaned up: {error}",
    "导入失败：{error}": "Import failed: {error}",
    "删除未完成，原文件或删除暂存记录已保留：{error}": "Deletion incomplete. Original files or recovery records have been kept: {error}",
    "撤销未完成，删除暂存文件已保留：{error}": "Undo incomplete. Staged files have been kept: {error}",
    "回收失败，音频和配置仍保存在删除暂存目录，可撤销恢复：{error}": "Recycling failed. Audio and configurations remain in staging and can be restored with Undo: {error}",
    "删除文件未能送入回收站，暂存文件和恢复记录已保留：{error}": "Deleted files could not be recycled. Staged files and recovery records have been kept: {error}",
    "删除恢复记录检查失败，暂存文件已保留：{error}": "Could not check deletion recovery records. Staged files have been kept: {error}",
    "恢复默认失败：{error}": "Reset failed: {error}",
    "{folder}：删除恢复记录不可用，暂存文件已保留：{error}": "{folder}: Deletion recovery record unavailable; staged files have been kept: {error}",
    "{folder}：无法检查删除恢复记录，文件已保留：{error}": "{folder}: Could not check deletion recovery records; files have been kept: {error}",
    "● 正在录制{record_source} · {time}": "● Recording {record_source} · {time}",
    "文件夹选择保存失败：{error}": "Could not save the folder selection: {error}",
    "{name}\n点击播放\n拖动头像或名称移动迷你条": "{name}\nClick to play\nDrag the avatar or name to move the mini player",
    "{name}\n再点停止\n拖动头像或名称移动迷你条": "{name}\nClick again to stop\nDrag the avatar or name to move the mini player",
    "目录：{path}\n当前文件夹的声音；可拖入音频或视频添加": "Folder: {path}\nSounds in this folder; drop audio or video files to add",
    "媒体及配置共 {size} MiB；配置包一同移入回收站。": "Media and configuration: {size} MiB; configuration packages are also moved to the Recycle Bin.",
    "另外 {count} 项": "{count} more items",
    "音轨 {number} · {track_language} · {codec} · {channels} 声道": "Track {number} · {track_language} · {codec} · {channels} channels",
    "配置保存未完成：\n{error}": "Configuration save incomplete:\n{error}",
    "配置包不可用：{error}": "Configuration package unavailable: {error}",
    "部分快捷键暂未启用，原配置已保留：\n{error}": "Some shortcuts were not enabled; their saved settings were preserved:\n{error}",
    "媒体解码失败：{error}": "Media decoding failed: {error}",
    "已选 {count} 项": "{count} selected", "将选中的 {count} 个音频移到回收站？": "Move the {count} selected sounds to the Recycle Bin?",
    "本地音量 {value}%": "Local volume {value}%",
    "本地音量 {value}% · 滚轮调节": "Local volume {value}% · Scroll to adjust",
    "本地音量 {value}% · 点击展开，滚轮调节": "Local volume {value}% · Click to expand; scroll to adjust",
    "{name}\n再点停止": "{name}\nClick again to stop", "{name}\n点击播放": "{name}\nClick to play",
    "播放模式：{mode}": "Playback mode: {mode}", "目录：{path}": "Folder: {path}",
    "保存到当前文件夹：{path}": "Save to: {path}", "片段快捷键 · {name}": "Sound shortcut · {name}",
    "播放范围 · {name}": "Playback range · {name}", "设置 · New Life": "Settings · New Life",
    "New Life · 迷你": "New Life · Mini", "New Life · 双击打开": "New Life · Double-click to open",
    "New Life 仍在运行": "New Life is still running", "{value} 秒": "{value} s",
    "将“{name}”移到回收站？\n该文件对应的播放范围和外观设置也会移除。": "Move “{name}” to the Recycle Bin?\nIts playback range and appearance settings will also be removed.",
    "已导入 {count} 个音频": "Imported {count} audio files", "已保存：{path}": "Saved: {path}", "已另存：{path}": "Exported: {path}",
    "读取音频设备失败：{error}": "Could not read audio devices: {error}", "读取文件夹失败：{error}": "Could not read folder: {error}",
    "设置保存失败：{error}": "Could not save settings: {error}", "快捷键 {key} 已被系统或其他程序占用": "Shortcut {key} is used by the system or another app",
    "快捷键 {key} 重复，请为每个操作设置不同的按键": "Shortcut {key} is duplicated. Use a different key for each action.",
    "配置失败，恢复尚未完成：{error}；{detail}": "Setup failed and restoration is incomplete: {error}; {detail}",
    "恢复未完成：{error}；可在 Windows 声音设置中手动恢复": "Restoration is incomplete: {error}; restore it manually in Windows Sound settings",
    "临时配置失败：{error}": "Temporary setup failed: {error}",
    "{action}失败（0x{code}），可在 Windows 声音设置中手动选择 CABLE Output": "{action} failed (0x{code}). Select CABLE Output manually in Windows Sound settings.",
    "无法检查通话输入设备：{error}": "Could not check call input: {error}",
    "系统设置恢复失败，请检查 Windows 默认通信麦克风：{error}": "System settings could not be restored. Check the Windows default communications microphone: {error}",
    "临时系统配置不可用：{error}": "Temporary system setup is unavailable: {error}",
    "临时系统配置失败，请手动选择 CABLE Output：{error}": "Temporary system setup failed. Select CABLE Output manually: {error}",
    "{component}关闭失败：{error}": "Could not close {component}: {error}",
    "系统配置清理失败：{error}": "Could not clean up system configuration: {error}", "配置保存失败：{error}": "Could not save configuration: {error}",
    "文件扩展名与实际音频格式不符：{format}": "The filename extension does not match the audio format: {format}",
    "仅支持 OGG Vorbis，不支持 OGG {format}": "Only OGG Vorbis is supported, not OGG {format}",
    "无法读取头像图片：{error}": "Could not read avatar image: {error}", "音频暂不可用：{error}": "Audio is unavailable: {error}",
    "设备无法按其原生格式打开：{error}": "Could not open the device in its native format: {error}", "请先选择{device}": "Select {device} first",
    "设备已断开：{device}；请重新选择后手动恢复": "Device disconnected: {device}. Select the device and resume manually.",
    "部分录音无法继续写入，正在保留已有内容：{error}": "Recording writes failed. Preserving the recorded audio: {error}",
    "录音保留在临时文件中，可下次恢复：{error}": "Recording kept in a temporary file for recovery on next launch: {error}",
    "已保留临时录音供下次恢复：{error}": "Temporary recording preserved for recovery on next launch: {error}",
    "请查看日志：\n{path}": "See the log:\n{path}",
    "{error}\n\n详细信息已保存到日志。": "{error}\n\nDetails have been saved to the log.",
    "采集：丢失 {dropped} 帧 · 溢出 {overflows} 次": "Capture: {dropped} frames lost · {overflows} overflows",
    "输出：丢失 {dropped} 帧 · 欠载 {underflows} 次（{frames} 帧）": "Output: {dropped} frames lost · {underflows} underruns ({frames} frames)",
    "当前缓冲：本地 {local} ms · 通话 {call} ms": "Buffer: local {local} ms · call {call} ms",
}
_NATIVE_BUTTONS = {"&OK": ("确定", "OK"), "OK": ("确定", "OK"), "&Cancel": ("取消", "Cancel"), "Cancel": ("取消", "Cancel"), "&Yes": ("是", "Yes"), "&No": ("否", "No"), "&Save": ("保存", "Save"), "&Close": ("关闭", "Close"), "&Open": ("打开", "Open"), "&Reset": ("重置", "Reset")}
_patterns = []
for source, target in _TEMPLATES.items():
    names = re.findall(r"\{(\w+)\}", source)
    regex = re.escape(source)
    for name in names:
        regex = regex.replace(re.escape("{" + name + "}"), "(?P<" + name + ">.*?)", 1)
    _patterns.append((re.compile("^" + regex + "$", re.DOTALL), target))


def tr(source: str, language: str | None = None, **values) -> str:
    lang = language or _language
    if source in _NATIVE_BUTTONS:
        return _NATIVE_BUTTONS[source][1 if lang in ("en", "en_US") else 0]
    if lang not in ("en", "en_US"):
        return source.format(**values) if values else source
    values = {key: tr(value, lang) if key in ("mode", "error", "detail", "action", "component", "record_source", "reason") and isinstance(value, str) else value for key, value in values.items()}
    if len(source) > 1 and source[0] == source[-1] and source[0] in ("'", '"'):
        inner = tr(source[1:-1], lang)
        if inner != source[1:-1]:
            return source[0] + inner + source[-1]
    if source.startswith("!  ") and source not in _EN:
        return "!  " + tr(source[3:], lang, **values)
    if source in _EN:
        text = _EN[source]
    elif source in _TEMPLATES:
        text = _TEMPLATES[source]
    else:
        text = source
        for pattern, target in _patterns:
            match = pattern.fullmatch(source)
            if match:
                data = match.groupdict()
                for key in ("mode", "error", "detail", "action", "component", "record_source", "reason"):
                    if key in data:
                        data[key] = tr(data[key], lang)
                text = target.format(**data)
                break
    return text.format(**values) if values else text


translate = tr
_EN.update(dict(zip(("单次播放", "单段循环", "顺序播放", "列表循环"), ("Play once", "Repeat one", "In order", "Repeat all"))))


def language() -> str:
    return _language


def set_language(code: str):
    global _language
    _language = "en" if code in ("en", "en_US") else "zh_CN"
    app = QApplication.instance()
    if app:
        for widget in app.topLevelWidgets():
            retranslate(widget)
            widget.update()


def _text(obj, key, getter, setter):
    current = getter()
    if not isinstance(current, str):
        return
    states = _saved.setdefault(obj, {})
    previous = states.get(key)
    source = previous[0] if previous and current == previous[1] else current
    translated = tr(source)
    states[key] = (source, translated)
    if current != translated:
        setter(translated)


def retranslate(root: QObject):
    """Update display strings in place; never replace controls or editable values."""
    for obj in [root, *root.findChildren(QObject)]:
        try:
            if obj.property("i18n_skip"):
                continue
            if isinstance(obj, QWidget):
                _text(obj, "tip", obj.toolTip, obj.setToolTip)
                _text(obj, "access", obj.accessibleName, obj.setAccessibleName)
                _text(obj, "window", obj.windowTitle, obj.setWindowTitle)
            if isinstance(obj, (QLabel, QAbstractButton, QAction)):
                _text(obj, "text", obj.text, obj.setText)
            if isinstance(obj, QLineEdit):
                _text(obj, "placeholder", obj.placeholderText, obj.setPlaceholderText)
            if isinstance(obj, QComboBox):
                blocked = obj.blockSignals(True)
                for index in range(obj.count()):
                    if obj.property("i18n_device_names") and obj.itemText(index) not in ("请选择设备", "已保存的设备当前不可用", "Select a device", "Saved device is currently unavailable"):
                        continue
                    _text(obj, f"item{index}", lambda i=index: obj.itemText(i), lambda text, i=index: obj.setItemText(i, text))
                obj.blockSignals(blocked)
            if isinstance(obj, QTabWidget):
                for index in range(obj.count()):
                    _text(obj, f"tab{index}", lambda i=index: obj.tabText(i), lambda text, i=index: obj.setTabText(i, text))
            if isinstance(obj, QDoubleSpinBox):
                _text(obj, "suffix", obj.suffix, obj.setSuffix)
            if isinstance(obj, QSystemTrayIcon):
                _text(obj, "tip", obj.toolTip, obj.setToolTip)
        except RuntimeError:
            continue  # A deferred-delete dialog may still be in Qt's child list.


class _TranslationFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.ToolTip) and isinstance(watched, QWidget):
            retranslate(watched)
        return False


def install_live_translation():
    global _filter
    if _filter is None and QApplication.instance():
        _filter = _TranslationFilter(QApplication.instance())
        QApplication.instance().installEventFilter(_filter)
