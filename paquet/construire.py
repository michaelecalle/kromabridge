"""Fabrique les fichiers d'une version de Kromabridge :

  sortie/Kromabridge-Setup.exe   l'installateur Windows (Inno Setup)
  sortie/kromabridge-pi.zip      la version Raspberry Pi (installation et mises a jour)
  sortie/installer-pi.sh         le script d'installation du Pi

Usage (sous Windows, depuis le dossier du depot) :
  python paquet/construire.py --build "D:/.../kromabridge-build" --venv "D:/.../kromabridge-pc/.venv"

--build : dossier de travail, qui contient InnoSetup/ (ISCC.exe) et
          telechargements/python/python-3.12.10-embed-amd64.zip
--venv  : un environnement Python 3.12 Windows ou sont installes python-rtmidi,
          numpy et sounddevice (leurs fichiers sont repris tels quels)
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

DEPOT = Path(__file__).resolve().parent.parent
PAQUET = DEPOT / "paquet"
DOCS = DEPOT / "doc" / "utilisateur"
PYTHON_EMBED = "python-3.12.10-embed-amd64.zip"
# Bibliotheques reprises de l'environnement Python (rien d'autre : ni pip ni pytest).
BIBLIOTHEQUES = ["rtmidi", "numpy", "numpy.libs", "sounddevice.py", "_sounddevice.py", "_sounddevice_data",
                 "cffi", "pycparser", "_cffi_backend.cp312-win_amd64.pyd"]
DOCS_HTML = ["Manuel Kromabridge (FR).md", "Kromabridge Manual (EN).md",
             "Installer la version Raspberry Pi (FR).md", "Installing the Raspberry Pi version (EN).md"]
FICHES = ["Fiche Kromabridge (FR).html", "Kromabridge Quick Reference (EN).html"]
SANS_CACHE = shutil.ignore_patterns("__pycache__", "*.pyc")


def version() -> str:
    texte = (DEPOT / "kromabridge" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', texte).group(1)


# ------------------------------------------------------------ Markdown -> HTML

STYLE = """
  :root { color-scheme: light; }
  body { font: 16px/1.55 "Segoe UI", system-ui, sans-serif; color: #1a1a1a; background: #fff;
         max-width: 820px; margin: 0 auto; padding: 24px 20px 60px; }
  h1 { font-size: 28px; margin: 0 0 6px; }
  h2 { font-size: 21px; margin: 32px 0 10px; padding-bottom: 4px; border-bottom: 2px solid #c06a00; color: #8a4b00; }
  h3 { font-size: 17px; margin: 22px 0 8px; }
  table { border-collapse: collapse; margin: 10px 0; }
  th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }
  th { background: #f6efe6; }
  code { background: #f2f2f2; padding: 1px 5px; border-radius: 4px; font-size: 14px; }
  pre { background: #f2f2f2; padding: 10px 12px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }
  pre code { background: none; padding: 0; }
  blockquote { margin: 10px 0; padding: 6px 14px; border-left: 4px solid #c06a00; background: #fbf6ef; }
  hr { border: none; border-top: 1px solid #ddd; margin: 28px 0; }
  a { color: #8a4b00; }
  @media print { body { max-width: none; padding: 0; } }
"""


def _en_ligne(t: str) -> str:
    morceaux = re.split(r"(`[^`]+`)", t)
    out = []
    for m in morceaux:
        if m.startswith("`") and m.endswith("`") and len(m) > 1:
            out.append("<code>" + html.escape(m[1:-1]) + "</code>")
            continue
        m = html.escape(m, quote=False)
        m = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", m)
        m = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", m)
        m = re.sub(r"(https?://[^\s<)]+[^\s<).,;:])", r'<a href="\1">\1</a>', m)
        out.append(m)
    return "".join(out)


def markdown_html(texte: str, titre: str) -> str:
    lignes = texte.splitlines()
    out: list[str] = []
    i = 0
    pile: list[tuple[str, int]] = []   # listes ouvertes : (ul|ol, retrait)

    def fermer_listes(retrait: int = -1) -> None:
        while pile and pile[-1][1] > retrait:
            out.append(f"</li></{pile.pop()[0]}>")

    while i < len(lignes):
        ligne = lignes[i]
        nu = ligne.strip()
        retrait = len(ligne) - len(ligne.lstrip(" "))
        if not nu:
            i += 1
            continue
        if nu.startswith("```"):
            bloc = []
            i += 1
            while i < len(lignes) and not lignes[i].strip().startswith("```"):
                bloc.append(lignes[i].strip() if retrait else lignes[i])
                i += 1
            i += 1
            pre = "<pre><code>" + html.escape("\n".join(bloc)) + "</code></pre>"
            if pile and retrait > pile[-1][1]:
                out.append(pre)
            else:
                fermer_listes()
                out.append(pre)
            continue
        m = re.match(r"(#{1,3})\s+(.*)", nu)
        if m and retrait == 0:
            fermer_listes()
            n = len(m.group(1))
            out.append(f"<h{n}>{_en_ligne(m.group(2))}</h{n}>")
            i += 1
            continue
        if nu == "---" and retrait == 0:
            fermer_listes()
            out.append("<hr>")
            i += 1
            continue
        if nu.startswith("|"):
            fermer_listes()
            rangees = []
            while i < len(lignes) and lignes[i].strip().startswith("|"):
                rangees.append([c.strip() for c in lignes[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table>")
            for k, r in enumerate(rangees):
                if k == 1 and all(re.fullmatch(r":?-+:?", c) for c in r):
                    continue
                balise = "th" if k == 0 else "td"
                out.append("<tr>" + "".join(f"<{balise}>{_en_ligne(c)}</{balise}>" for c in r) + "</tr>")
            out.append("</table>")
            continue
        if nu.startswith(">"):
            bloc = []
            while i < len(lignes) and lignes[i].strip().startswith(">"):
                bloc.append(lignes[i].strip()[1:].strip())
                i += 1
            q = "<blockquote>" + _en_ligne(" ".join(bloc)) + "</blockquote>"
            if pile and retrait > pile[-1][1]:
                out.append(q)
            else:
                fermer_listes()
                out.append(q)
            continue
        m = re.match(r"(-|\*|\d+\.)\s+(.*)", nu)
        if m:
            genre = "ol" if m.group(1)[0].isdigit() else "ul"
            if pile and retrait > pile[-1][1]:
                out.append(f"<{genre}>")
                pile.append((genre, retrait))
            else:
                fermer_listes(retrait)
                if pile and pile[-1][1] == retrait:
                    out.append("</li>")
                else:
                    out.append(f"<{genre}>")
                    pile.append((genre, retrait))
            out.append("<li>" + _en_ligne(m.group(2)))
            i += 1
            continue
        # paragraphe (ou suite d'un element de liste)
        bloc = [nu]
        i += 1
        while i < len(lignes) and lignes[i].strip() and not re.match(r"\s*(#|>|\||-\s|\*\s|\d+\.\s|```|---)", lignes[i]):
            bloc.append(lignes[i].strip())
            i += 1
        p = _en_ligne(" ".join(bloc))
        if pile and retrait > pile[-1][1]:
            out.append("<p>" + p + "</p>")
        else:
            fermer_listes()
            out.append("<p>" + p + "</p>")
    fermer_listes()
    langue = "en" if "(EN)" in titre else "fr"
    return (f'<!DOCTYPE html>\n<html lang="{langue}">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{html.escape(titre)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n"
            + "\n".join(out) + "\n</body>\n</html>\n")


def documentation(vers: Path) -> None:
    vers.mkdir(parents=True, exist_ok=True)
    for nom in DOCS_HTML:
        texte = (DOCS / nom).read_text(encoding="utf-8")
        (vers / (nom[:-3] + ".html")).write_text(markdown_html(texte, nom[:-3]), encoding="utf-8")
    for nom in FICHES:
        shutil.copy2(DOCS / nom, vers / nom)


# ------------------------------------------------------------------ fabrication

def preparer_pc(build: Path, venv: Path, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    # Python embarque, qui trouve ses bibliotheques et le programme
    py = staging / "python"
    with zipfile.ZipFile(build / "telechargements" / "python" / PYTHON_EMBED) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    pth.write_text(f"{pth.stem}.zip\n.\nLib\\site-packages\n..\n", encoding="ascii")
    sp = venv / "Lib" / "site-packages"
    cible = py / "Lib" / "site-packages"
    cible.mkdir(parents=True)
    for nom in BIBLIOTHEQUES:
        src = sp / nom
        if not src.exists():
            raise SystemExit(f"bibliotheque introuvable dans {sp} : {nom}")
        if src.is_dir():
            shutil.copytree(src, cible / nom, ignore=SANS_CACHE)
        else:
            shutil.copy2(src, cible / nom)
    # le programme
    shutil.copytree(DEPOT / "kromabridge", staging / "kromabridge", ignore=SANS_CACHE)
    (staging / "boitier.json").write_text('{"entree": "kromalaser usb", "sortie": "kromabridge"}\n', encoding="utf-8")
    (staging / "installe").write_text("Version installee : les donnees vont dans Documents\\Kromabridge.\n", encoding="utf-8")
    shutil.copy2(PAQUET / "kromalaser.ico", staging / "kromalaser.ico")
    bat = (PAQUET / "Kromabridge.bat").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    (staging / "Kromabridge.bat").write_bytes(bat.encode("cp1252"))
    documentation(staging / "doc")
    # verification : le Python embarque charge bien tout
    r = subprocess.run([str(py / "python.exe"), "-c",
                        "import kromabridge, kromabridge.main, rtmidi, numpy, sounddevice; print(kromabridge.__version__)"],
                       capture_output=True, text=True, cwd=staging)
    if r.returncode != 0:
        raise SystemExit("le Python embarque ne charge pas le programme :\n" + r.stderr)
    print("  Python embarque : OK, version", r.stdout.strip())


def faire_zip_pi(sortie: Path, docs: Path) -> Path:
    f = sortie / "kromabridge-pi.zip"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        for partie in ("kromabridge", "outils"):
            for p in sorted((DEPOT / partie).rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                    z.write(p, p.relative_to(DEPOT).as_posix())
        for p in sorted(docs.iterdir()):
            z.write(p, "doc/" + p.name)
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, type=Path)
    ap.add_argument("--venv", required=True, type=Path)
    a = ap.parse_args()
    v = version()
    sortie = a.build / "sortie"
    if sortie.exists():
        shutil.rmtree(sortie)
    sortie.mkdir(parents=True)
    staging = a.build / "staging"
    print(f"Kromabridge {v}")
    print("- version PC")
    preparer_pc(a.build, a.venv, staging)
    print("- version Raspberry Pi")
    faire_zip_pi(sortie, staging / "doc")
    sh = (PAQUET / "installer-pi.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    (sortie / "installer-pi.sh").write_bytes(sh.encode("utf-8"))
    print("- installateur Windows")
    r = subprocess.run([str(a.build / "InnoSetup" / "ISCC.exe"), "/Q", f"/DVersion={v}", f"/DStaging={staging}",
                        f"/DSortie={sortie}", str(PAQUET / "kromabridge.iss")], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        return 1
    for f in sorted(sortie.iterdir()):
        print(f"  {f.name:28s} {f.stat().st_size / 1e6:6.1f} Mo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
