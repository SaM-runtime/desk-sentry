import os

# Keep test windows off the real desktop; set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Offscreen Qt has no system fonts; point it at Windows' so metrics match the real UI.
_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
if os.path.isdir(_fonts):
    os.environ.setdefault("QT_QPA_FONTDIR", _fonts)
