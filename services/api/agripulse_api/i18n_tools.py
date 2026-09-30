"""V3-3: native-speaker review of alert and screen text.

    python -m agripulse_api.i18n_tools status                     # reviewed / machine per language
    python -m agripulse_api.i18n_tools export --lang kn --out review_kn.csv
    python -m agripulse_api.i18n_tools import --lang kn review_kn.csv --reviewer "Name"

The CSV has one row per string: file (alerts | ui), key, field (title | body | text), English, current, corrected,
approve, notes. The reviewer either writes a corrected translation, or puts "yes" in approve to accept the current
one. Import applies corrections, checks every {placeholder} matches the English exactly, and marks each touched
string reviewed (with reviewer and date). Untouched strings stay "machine"."""
import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILES = {"alerts": ROOT / "services" / "api" / "agripulse_api" / "i18n" / "alerts.json",
         "ui": ROOT / "apps" / "web" / "lib" / "messages.json"}
LANGS = ("kn", "hi")
PH = re.compile(r"\{(\w+)\}")


def load(name: str) -> dict:
    return json.loads(FILES[name].read_text("utf-8"))


def save(name: str, data: dict) -> None:
    FILES[name].write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def strings(data: dict, lang: str):
    """(key, field, english, current) for every string of one file."""
    for key, en in data["en"].items():
        cur = data.get(lang, {}).get(key)
        if isinstance(en, dict):
            for field in ("title", "body"):
                yield key, field, en[field], (cur or {}).get(field, "")
        else:
            yield key, "text", en, cur or ""


def placeholders(s: str) -> list[str]:
    return sorted(PH.findall(s))


def status() -> dict:
    out = {}
    for name in FILES:
        data = load(name)
        for lang in LANGS:
            st = [data.get("_review", {}).get(lang, {}).get(k, {}).get("status", "machine") for k in data["en"]]
            missing = [k for k in data["en"] if k not in data.get(lang, {})]
            out.setdefault(lang, {})[name] = {"total": len(st), "reviewed": st.count("reviewed"),
                                              "machine": st.count("machine"), "missing": missing}
    return out


def export(lang: str, out: Path) -> int:
    n = 0
    with open(out, "w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel opens Kannada/Hindi correctly
        w = csv.writer(f)
        w.writerow(["file", "key", "field", "english", "current", "corrected", "approve", "notes"])
        for name in FILES:
            data = load(name)
            for key, field, en, cur in strings(data, lang):
                w.writerow([name, key, field, en, cur, "", "", ""])
                n += 1
    return n


def import_(lang: str, path: Path, reviewer: str) -> dict:
    datas = {name: load(name) for name in FILES}
    changed, approved, errors = 0, 0, []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            name, key, field = row["file"], row["key"], row["field"]
            if name not in datas or key not in datas[name]["en"]:
                errors.append(f"line {i}: unknown {name}/{key}")
                continue
            new, ok = (row.get("corrected") or "").strip(), (row.get("approve") or "").strip().lower() in ("yes", "y", "ok")
            if not new and not ok:
                continue
            data = datas[name]
            en = data["en"][key][field] if field in ("title", "body") else data["en"][key]
            if new:
                if placeholders(new) != placeholders(en):
                    errors.append(f"line {i}: {name}/{key}/{field} placeholders {placeholders(new)} != English {placeholders(en)}")
                    continue
                if field in ("title", "body"):
                    data.setdefault(lang, {}).setdefault(key, {})[field] = new
                else:
                    data.setdefault(lang, {})[key] = new
                changed += 1
            else:
                approved += 1
            data.setdefault("_review", {}).setdefault(lang, {})[key] = {
                "status": "reviewed", "by": reviewer, "on": str(date.today())}
    if errors:
        return {"applied": False, "errors": errors}
    for name, data in datas.items():
        save(name, data)
    return {"applied": True, "corrected": changed, "approved": approved}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    e = sub.add_parser("export")
    e.add_argument("--lang", choices=LANGS, required=True)
    e.add_argument("--out", type=Path, required=True)
    im = sub.add_parser("import")
    im.add_argument("--lang", choices=LANGS, required=True)
    im.add_argument("csv", type=Path)
    im.add_argument("--reviewer", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(), indent=1, ensure_ascii=False))
    elif a.cmd == "export":
        print(f"{export(a.lang, a.out)} strings -> {a.out}")
    else:
        r = import_(a.lang, a.csv, a.reviewer)
        print(json.dumps(r, indent=1, ensure_ascii=False))
        if not r["applied"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
