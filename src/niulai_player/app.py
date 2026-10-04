from __future__ import annotations

import argparse
import ctypes
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .i18n import tr, set_language


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="New Life：Windows 文件夹音效与录音工具")
    parser.add_argument("--folder", default="", help="启动时选择的音频文件夹")
    parser.add_argument("--data-dir", default="", help="独立配置目录（开发与测试使用）")
    parser.add_argument("--no-audio", action="store_true", help="仅预览界面，不开启音频设备")
    parser.add_argument("--screenshot", default="", help="将真实窗口渲染保存为 PNG 后退出")
    parser.add_argument("--view", choices=("main", "settings", "shortcuts", "mini"), default="main")
    parser.add_argument("--quit-after", type=float, default=0, help="指定秒数后退出（冒烟验证）")
    parser.add_argument("--language", choices=("zh_CN", "en"), help="界面语言")
    args = parser.parse_args(argv)
    if args.language:
        set_language(args.language)
    data_dir = Path(args.data_dir or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NiuLaiPlayerPython").absolute()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = data_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_dir / "application.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.info("启动 New Life %s", __version__)
    if os.name == "nt":
        # Stable across releases so Windows groups the exe and all its windows.
        set_identity = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_identity.argtypes = [ctypes.c_wchar_p]
        set_identity.restype = ctypes.c_long
        result = set_identity("NewLife.Player")
        if result < 0:
            logging.warning("Windows 任务栏分组设置失败: 0x%08X", result & 0xffffffff)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("New Life")
    app.setOrganizationName("NiuLaiPlayerPython")
    app.setApplicationVersion(__version__)
    from .branding import app_icon
    app.setWindowIcon(app_icon())
    # Qt offscreen 不枚举 Windows 字体；加载系统字体使测试截图与实际窗口一致。
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisym.ttf", "seguiemj.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setQuitOnLastWindowClosed(False)

    server = QLocalServer(app)
    instance_name = "niulai-player-" + hashlib.sha256(str(data_dir).casefold().encode()).hexdigest()[:20]
    if not args.screenshot:
        socket = QLocalSocket()
        socket.connectToServer(instance_name)
        if socket.waitForConnected(150):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(100)
            socket.disconnectFromServer()
            return 0
        QLocalServer.removeServer(instance_name)
        if not server.listen(instance_name):
            # Another process may have won the startup race after our first probe.
            socket.abort()
            socket.connectToServer(instance_name)
            if socket.waitForConnected(300):
                socket.write(b"show")
                socket.flush()
                socket.waitForBytesWritten(100)
                socket.disconnectFromServer()
                return 0
            logging.error("单实例服务不可用: %s", server.errorString())
            QMessageBox.critical(None, "New Life", tr("无法建立单实例连接。请先关闭另一份 New Life，再重新启动。"))
            return 1
    controller = None
    try:
        from .controller import Controller
        from .ui import MainWindow
        from .preview import PreviewEngine
        controller = Controller(data_dir, engine_factory=PreviewEngine if args.no_audio else None,
                                folder=args.folder, no_hotkeys=bool(args.screenshot or args.no_audio),
                                enable_system_settings=not bool(args.screenshot or args.no_audio))
        if args.language:
            controller.set_language(args.language)
        window = MainWindow(controller)
        window.show()
        def show_existing_instance():
            while server.hasPendingConnections():
                connection = server.nextPendingConnection()
                connection.readAll()
                connection.disconnectFromServer()
                connection.deleteLater()
            window.show_main()
        server.newConnection.connect(show_existing_instance)
        def finish_shutdown():
            for error in controller.shutdown():
                logging.error("退出时需要检查: %s", error)
        app.aboutToQuit.connect(finish_shutdown)

        def report_exception(kind, value, traceback):
            logging.error("未处理异常", exc_info=(kind, value, traceback))
            QMessageBox.critical(window, tr("操作失败"), tr("{error}\n\n详细信息已保存到日志。", error=tr(str(value))))
        sys.excepthook = report_exception

        if args.no_audio:
            QTimer.singleShot(600, lambda: controller.message.emit("界面预览模式：未开启音频设备", False))

        if args.screenshot:
            attempts = [0]

            def capture():
                attempts[0] += 1
                selected = controller.get_item(controller.state.get("path", "")) or next(
                    (entry for entry in controller.items if entry.playable), None)
                if (controller.state.get("loading") or (selected and not selected.peaks)) and attempts[0] < 30:
                    QTimer.singleShot(150, capture)
                    return
                target = window
                if args.view == "mini":
                    window.show_mini()
                    target = window.mini
                elif args.view in ("settings", "shortcuts"):
                    # Helpers are independent windows; capture that window,
                    # not the unobscured player behind it.
                    target = window.open_settings(tab=2 if args.view == "shortcuts" else 1)

                def save():
                    output = Path(args.screenshot).absolute()
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if not target.grab().save(str(output)):
                        logging.error("无法保存截图 %s", output)
                        app.exit(2)
                    else:
                        app.quit()
                QTimer.singleShot(250, save)
            QTimer.singleShot(300, capture)
        elif args.view == "mini":
            QTimer.singleShot(300, window.show_mini)
        elif args.view == "settings":
            QTimer.singleShot(300, window.open_settings)
        elif args.view == "shortcuts":
            QTimer.singleShot(300, lambda: window.open_settings(tab=2))
        if args.quit_after > 0:
            QTimer.singleShot(int(args.quit_after * 1000), app.quit)
        return app.exec()
    except Exception:
        logging.exception("启动失败")
        QMessageBox.critical(None, tr("New Life 启动失败"), tr("请查看日志：\n{path}", path=str(log_dir / 'application.log')))
        return 1
    finally:
        if controller is not None:
            for error in controller.shutdown():
                logging.error("退出时需要检查: %s", error)


if __name__ == "__main__":
    raise SystemExit(main())
