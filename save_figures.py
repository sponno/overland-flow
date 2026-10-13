"""Save the pictures figures.html draws as files in figs/, for idea.html to show.

  python3 save_figures.py

Opens figures.html in headless Chrome, which runs the model and draws each picture on a
canvas, then reads the images back out of the page. Needs Google Chrome.
"""
import base64
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main():
    page = (HERE / "figures.html").as_uri()
    dom = subprocess.run([CHROME, "--headless=new", "--virtual-time-budget=120000", "--dump-dom", page],
                         capture_output=True, text=True, check=True).stdout
    found = re.findall(r'data-name="([a-z-]+)" src="data:image/png;base64,([^"]+)"', dom)
    if not found:
        raise SystemExit("no pictures in the page: open figures.html in a browser and check the console")
    out = HERE / "figs"
    out.mkdir(exist_ok=True)
    for name, data in found:
        path = out / f"{name}.png"
        path.write_bytes(base64.b64decode(data))
        print(f"wrote {path.relative_to(HERE)}  {path.stat().st_size / 1000:.0f} kB")


if __name__ == "__main__":
    main()
