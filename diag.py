"""Diagnostic script - run with: python diag.py
Saves results to diag_output.txt so nothing is lost even if the window closes.
"""
import os, sys, traceback

out = []
def log(msg):
    print(msg, flush=True)
    out.append(msg)

def save():
    with open("diag_output.txt", "w") as f:
        f.write("\n".join(out))
    print(">>> Saved to diag_output.txt <<<")

log(f"Python: {sys.version}")
log(f"Executable: {sys.executable}")
log(f"CWD: {os.getcwd()}")
log(f"QT_OPENGL env: {os.environ.get('QT_OPENGL', 'NOT SET')}")

# Set before Qt import
os.environ["QT_OPENGL"] = "software"
log("Set QT_OPENGL=software")

try:
    import p2plan_core
    log("p2plan_core: OK")
except Exception as e:
    log(f"p2plan_core: FAILED - {e}")
    save(); input("Press Enter..."); sys.exit(1)

try:
    from p2p_lan_share import config
    log("config: OK")
except Exception as e:
    log(f"config: FAILED\n{traceback.format_exc()}")
    save(); input("Press Enter..."); sys.exit(1)

try:
    from p2p_lan_share import network
    log("network: OK")
except Exception as e:
    log(f"network: FAILED\n{traceback.format_exc()}")
    save(); input("Press Enter..."); sys.exit(1)

try:
    from PyQt6.QtWidgets import QApplication
    log("QApplication import: OK")
    app = QApplication(sys.argv)
    log("QApplication(): OK")
except Exception as e:
    log(f"QApplication: FAILED\n{traceback.format_exc()}")
    save(); input("Press Enter..."); sys.exit(1)

try:
    from p2p_lan_share.gui.main_window import MainWindow
    log("MainWindow import: OK")
except Exception as e:
    log(f"MainWindow: FAILED\n{traceback.format_exc()}")
    save(); input("Press Enter..."); sys.exit(1)

try:
    from p2p_lan_share.gui.theme import apply_theme
    apply_theme(app)
    log("apply_theme: OK")
    win = MainWindow()
    log("MainWindow(): OK")
    win.show()
    log("win.show(): OK - window should be visible now")
    save()
    sys.exit(app.exec())
except Exception as e:
    log(f"GUI init FAILED\n{traceback.format_exc()}")
    save(); input("Press Enter..."); sys.exit(1)
